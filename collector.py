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

MAX_SERVERS = 100          # سقف نهایی تعداد سرورها در فایل
CHECK_NEW_CANDIDATES = 50  # تعداد سرورهای جدیدی که در هر دور برای ورود به لیست رقابت می‌کنند

# ایندکس‌های مورد نیاز (خروجی):
# 0:HostName, 1:IP, 4:Speed, 5:CountryLong, 6:CountryShort, 7:NumVpnSessions
KEEP_INDICES = [0, 1, 4, 5, 6, 7]

def get_gist_headers():
    return {
        'Authorization': f'token {GIST_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }

def filter_columns(row):
    """انتخاب فقط ستون‌های مورد نظر"""
    if len(row) <= max(KEEP_INDICES):
        return None
    return [row[i] for i in KEEP_INDICES]

def get_remote_list():
    """دانلود و پارس کردن لیست از سایت اصلی"""
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
            filtered = filter_columns(parts)
            if not filtered: continue

            if line.startswith('#HostName'):
                header = filtered
            else:
                # فیلتر اولیه: حذف سرورهای با سشن 0 یا سرعت نامعتبر
                try:
                    # در لیست فیلتر شده: index 2 = Speed, index 5 = Sessions
                    if int(filtered[5]) > 0:
                        data_rows.append(filtered)
                except:
                    continue

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
                if len(row) == len(KEEP_INDICES):
                    ip = row[1]
                    data_dict[ip] = row
            return data_dict, header
        return {}, None
    except Exception as e:
        print(f"Error loading Gist: {e}")
        return {}, None

def update_gist(content_string):
    print("Updating Gist with optimized list...")
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
    """بررسی اتصال SSTP (SSL Handshake)"""
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
    """تست همزمان لیستی از سرورها"""
    print(f"Checking connectivity for {len(server_list)} servers...")
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
    امتیاز = Speed / (Sessions + 1)
    در لیست فیلتر شده (KEEP_INDICES):
    row[2] -> Speed
    row[5] -> NumVpnSessions
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

    # 1. خواندن و تست سرورهای فعلی (Survivors)
    local_data, local_header = load_gist_data()
    print(f"Local servers before check: {len(local_data)}")
    
    alive_local = filter_servers_concurrent(list(local_data.values()))
    print(f"Local servers alive: {len(alive_local)}")

    # 2. دریافت و آماده‌سازی کاندیداهای جدید (Challengers)
    new_header, new_rows = get_remote_list()
    final_header = new_header if new_header else local_header
    
    candidates = []
    if new_rows:
        # فقط کسانی که الان در لیست نیستند را کاندید می‌کنیم
        for row in new_rows:
            ip = row[1]
            if ip not in alive_local:
                candidates.append(row)
        
        # مرتب‌سازی کاندیداها بر اساس امتیاز (تا بهترین‌هایشان را تست کنیم)
        candidates.sort(key=calculate_score, reverse=True)
        
        # انتخاب تعداد محدودی کاندیدا برای تست (مثلا 50 تا)
        # این کار برای صرفه‌جویی در زمان اجراست
        candidates_to_check = candidates[:CHECK_NEW_CANDIDATES]
        print(f"New candidates selected for testing: {len(candidates_to_check)}")
        
        # تست کاندیداهای جدید
        alive_new = filter_servers_concurrent(candidates_to_check)
        print(f"New servers passed test: {len(alive_new)}")
        
        # ادغام زنده‌های قدیمی با زنده‌های جدید
        alive_local.update(alive_new)

    # 3. رقابت نهایی و برش لیست (The Hunger Games!)
    print(f"Total pool size: {len(alive_local)}")
    
    if alive_local:
        # مرتب‌سازی کل استخر بر اساس امتیاز
        sorted_rows = sorted(alive_local.values(), key=calculate_score, reverse=True)
        
        # انتخاب 100 تای برتر (Top 100)
        final_list = sorted_rows[:MAX_SERVERS]
        print(f"Servers kept after cutoff: {len(final_list)}")

        # ذخیره‌سازی
        output = io.StringIO()
        writer = csv.writer(output)
        
        if final_header:
            writer.writerow(final_header)
            
        for row in final_list:
            writer.writerow(row)
            
        update_gist(output.getvalue())
    else:
        print("Warning: No alive servers found!")

if __name__ == "__main__":
    main()
