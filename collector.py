import requests
import csv
import socket
import concurrent.futures
import io
import os
import json

# --- تنظیمات ---
GIST_ID = os.environ.get('GIST_ID')
GIST_TOKEN = os.environ.get('GIST_TOKEN')
GIST_FILENAME = 'server_list.csv'

URL = "http://www.vpngate.net/api/iphone/"
TIMEOUT_SECONDS = 2.0
MAX_WORKERS = 50
VPN_PORT = 443

# ایندکس‌های مورد نیاز طبق درخواست شما:
# 0:HostName, 1:IP, 4:Speed, 5:CountryLong, 6:CountryShort, 7:NumVpnSessions
KEEP_INDICES = [0, 1, 4, 5, 6, 7]

def get_gist_headers():
    return {
        'Authorization': f'token {GIST_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }

def filter_columns(row):
    """انتخاب فقط ستون‌های مورد نظر از یک ردیف"""
    if len(row) <= max(KEEP_INDICES):
        return None
    return [row[i] for i in KEEP_INDICES]

def get_remote_list():
    """دانلود لیست و فیلتر کردن ستون‌ها"""
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
            
            # فیلتر کردن ستون‌ها
            filtered_parts = filter_columns(parts)
            if not filtered_parts:
                continue

            if line.startswith('#HostName'):
                header = filtered_parts
            else:
                data_rows.append(filtered_parts)
                    
        return header, data_rows
    except Exception as e:
        print(f"Error fetching remote list: {e}")
        return None, []

def load_gist_data():
    """خواندن Gist و تبدیل به فرمت جدید"""
    print("Loading data from Gist...")
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
                
                # نکته مهم: اینجا باید مطمئن شویم داده‌های قبلی فرمت درست دارند
                # IP در لیست فیلتر شده ما در ایندکس 1 است (چون 0=HostName و 1=IP)
                if len(row) > 1:
                    ip = row[1]
                    data_dict[ip] = row
            return data_dict, header
        return {}, None
    except Exception as e:
        print(f"Error loading Gist: {e}")
        return {}, None

def update_gist(content_string):
    print("Updating Gist...")
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

def check_server_connectivity(server_row):
    # در لیست فیلتر شده جدید ما، IP همچنان در ایندکس 1 است.
    ip = server_row[1]
    try:
        with socket.create_connection((ip, VPN_PORT), timeout=TIMEOUT_SECONDS):
            return server_row
    except:
        return None

def filter_dead_servers(servers_dict):
    print(f"Checking {len(servers_dict)} servers...")
    alive_servers = {}
    rows_to_check = list(servers_dict.values())
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_server_connectivity, row): row for row in rows_to_check}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                ip = result[1]
                alive_servers[ip] = result
                
    print(f"Alive: {len(alive_servers)}")
    return alive_servers

def main():
    if not GIST_ID or not GIST_TOKEN:
        print("Error: Secrets not set!")
        return

    local_data, local_header = load_gist_data()
    new_header, new_rows = get_remote_list()
    
    # همیشه هدر جدید را ترجیح می‌دهیم تا فرمت درست اعمال شود
    final_header = new_header if new_header else local_header

    # اگر از سایت دیتا گرفتیم، مرج می‌کنیم
    if new_rows:
        for row in new_rows:
            ip = row[1] # IP در ایندکس 1 است
            local_data[ip] = row 

    valid_servers = filter_dead_servers(local_data)

    if valid_servers:
        output = io.StringIO()
        writer = csv.writer(output)
        
        if final_header:
            writer.writerow(final_header)
            
        for ip in valid_servers:
            row = valid_servers[ip]
            # فقط ردیف‌هایی که دقیقاً ۶ ستون دارند را نگه دار
            # (این باعث می‌شود اگر دیتای قدیمیِ خراب مانده بود، حذف شود)
            if len(row) == len(KEEP_INDICES):
                writer.writerow(row)
            
        update_gist(output.getvalue())
    else:
        print("No valid servers.")

if __name__ == "__main__":
    main()