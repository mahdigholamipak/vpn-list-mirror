import requests
import csv
import socket
import ssl
import concurrent.futures
import io
import os
import json

# --- تنظیمات ---
GIST_ID = os.environ.get('GIST_ID')
GIST_TOKEN = os.environ.get('GIST_TOKEN')
GIST_FILENAME = 'server_list.csv'

URL = "http://www.vpngate.net/api/iphone/"
TIMEOUT_SECONDS = 3.0
MAX_WORKERS = 50
VPN_PORT = 443
MAX_SERVERS = 100  # محدودیت ۱۰۰ سرور برتر

# ایندکس‌های مورد نیاز برای خروجی نهایی:
# 0:HostName, 1:IP, 4:Speed, 5:CountryLong, 6:CountryShort, 7:NumVpnSessions
KEEP_INDICES = [0, 1, 4, 5, 6, 7]

def get_gist_headers():
    return {
        'Authorization': f'token {GIST_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }

def filter_columns(row):
    if len(row) <= max(KEEP_INDICES):
        return None
    return [row[i] for i in KEEP_INDICES]

def get_remote_list():
    """دانلود لیست خام از سایت"""
    try:
        print("Downloading from VPN Gate...")
        response = requests.get(URL, timeout=15)
        response.raise_for_status()
        content = response.content.decode('utf-8-sig', errors='ignore')
        lines = content.splitlines()
        
        data_rows = []
        header = None
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('*'):
                continue
            
            parts = line.split(',')
            
            # فیلتر کردن اولیه ستون‌ها
            filtered = filter_columns(parts)
            if not filtered: continue

            if line.startswith('#HostName'):
                header = filtered
            else:
                # --- فیلتر حذف سشن صفر ---
                # ایندکس 7 در لیست کامل همان NumVpnSessions است.
                # اما چون ما filter_columns را صدا زدیم، باید ببینیم در لیست جدید کجاست.
                # KEEP_INDICES = [0, 1, 4, 5, 6, 7]
                # پس NumVpnSessions در لیست فیلتر شده، آخرین عنصر (ایندکس 5) است.
                try:
                    sessions = int(filtered[5])
                    if sessions > 0:
                        data_rows.append(filtered)
                except ValueError:
                    continue # اگر عدد نبود رد کن

        return header, data_rows
    except Exception as e:
        print(f"Error fetching remote list: {e}")
        return None, []

def load_gist_data():
    """خواندن لیست فعلی از Gist"""
    print("Loading local data from Gist...")
    try:
        r = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=get_gist_headers())
        r.raise_for_status()
        files = r.json().get('files', {})
        if GIST_FILENAME in files:
            content = files[GIST_FILENAME].get('content', '')
            data_dict = {}
            header = None
            reader = csv.reader(content.splitlines())
            for row in reader:
                if not row: continue
                if row[0].startswith('#HostName'):
                    header = row
                    continue
                if len(row) == len(KEEP_INDICES): # فقط ردیف‌های سالم
                    ip = row[1]
                    data_dict[ip] = row
            return data_dict, header
        return {}, None
    except Exception as e:
        print(f"Error loading Gist: {e}")
        return {}, None

def update_gist(content_string):
    print("Updating Gist with new list...")
    try:
        data = { "files": { GIST_FILENAME: { "content": content_string } } }
        r = requests.patch(
            f"https://api.github.com/gists/{GIST_ID}", 
            headers=get_gist_headers(), 
            data=json.dumps(data)
        )
        r.raise_for_status()
        print("Gist updated successfully!")
    except Exception as e:
        print(f"Error updating Gist: {e}")

def check_server_sstp(server_row):
    """بررسی سلامت سرور با هندشیک SSL"""
    ip = server_row[1]
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    
    try:
        with socket.create_connection((ip, VPN_PORT), timeout=TIMEOUT_SECONDS) as sock:
            with context.wrap_socket(sock, server_hostname=ip) as ssock:
                return server_row
    except:
        return None

def filter_servers_concurrent(server_list):
    """چک کردن لیست ورودی به صورت همزمان"""
    print(f"Checking connectivity for {len(server_list)} candidates...")
    alive_dict = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_server_sstp, row): row for row in server_list}
        
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                ip = result[1]
                alive_dict[ip] = result
                
    return alive_dict

def calculate_score(row):
    """
    محاسبه امتیاز بر اساس فرمول: Speed / (Sessions + 1)
    در لیست فیلتر شده ما (KEEP_INDICES):
    Speed           -> index 2
    NumVpnSessions  -> index 5
    """
    try:
        speed = float(row[2])
        sessions = float(row[5])
        return speed / (sessions + 1)
    except (ValueError, IndexError):
        return 0

def main():
    if not GIST_ID or not GIST_TOKEN:
        print("Error: Secrets not set!")
        return

    # 1. خواندن لیست موجود
    local_data, local_header = load_gist_data()
    print(f"Current Gist servers: {len(local_data)}")

    # 2. پالایش لیست موجود
    alive_local = filter_servers_concurrent(list(local_data.values()))
    print(f"Alive servers from Gist: {len(alive_local)}")

    # 3. محاسبه ظرفیت خالی
    slots_needed = MAX_SERVERS - len(alive_local)
    print(f"Slots available: {slots_needed}")

    # 4. تکمیل ظرفیت از لیست جدید
    final_header = local_header
    
    if slots_needed > 0:
        new_header, new_rows = get_remote_list()
        final_header = new_header if new_header else local_header
        
        # پیدا کردن کاندیداهای جدید (غیر تکراری)
        candidates = []
        for row in new_rows:
            ip = row[1]
            if ip not in alive_local:
                candidates.append(row)
        
        print(f"Potential new candidates: {len(candidates)}")
        
        # مرتب‌سازی کاندیداها قبل از تست (تا تست را روی بهترین‌ها انجام دهیم)
        # این کار باعث می‌شود اگر فقط ۱۰ جای خالی داریم، ۱۰ تای اول لیست که
        # احتمالا سرعت بهتری دارند را چک کنیم، نه ۱۰ تای رندوم.
        candidates.sort(key=calculate_score, reverse=True)
        
        # تست سرورهای جدید
        alive_new = filter_servers_concurrent(candidates)
        print(f"Alive new candidates: {len(alive_new)}")
        
        # پر کردن لیست نهایی با سرورهای جدید (تا سقف مجاز)
        # چون کاندیداها سورت شده بودند، alive_new هم ترتیب تقریبی خوبی دارد
        # اما برای اطمینان دوباره سورت نهایی را انجام می‌دهیم.
        
        for ip, row in alive_new.items():
            if len(alive_local) >= MAX_SERVERS:
                break
            alive_local[ip] = row
            
    else:
        print("List is full. No need to fetch new servers.")

    # 5. مرتب‌سازی نهایی و ذخیره
    print(f"Final list size: {len(alive_local)}")
    
    if alive_local:
        output = io.StringIO()
        writer = csv.writer(output)
        
        if final_header:
            writer.writerow(final_header)
        
        # مرتب‌سازی نهایی بر اساس فرمول Speed / (Sessions + 1)
        sorted_rows = sorted(alive_local.values(), key=calculate_score, reverse=True)
        
        for row in sorted_rows:
            writer.writerow(row)
            
        update_gist(output.getvalue())
    else:
        print("Warning: No alive servers found!")

if __name__ == "__main__":
    main()
    
