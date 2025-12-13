import requests
import csv
import socket
import concurrent.futures
import io
import os
import json

# --- تنظیمات ---
# دریافت اطلاعات از متغیرهای محیطی (Secrets)
GIST_ID = os.environ.get('GIST_ID')
GIST_TOKEN = os.environ.get('GIST_TOKEN')
GIST_FILENAME = 'server_list.csv'

URL = "http://www.vpngate.net/api/iphone/"
TIMEOUT_SECONDS = 2.0
MAX_WORKERS = 50
VPN_PORT = 443

def get_gist_headers():
    return {
        'Authorization': f'token {GIST_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }

def get_remote_list():
    """دانلود لیست از سایت اصلی و حذف ستون کانفیگ"""
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
            
            # --- حذف ستون OpenVPN_ConfigData_Base64 ---
            if len(parts) > 14:
                parts = parts[:-1]
            
            if line.startswith('#HostName'):
                header = parts
            else:
                if len(parts) > 5:
                    data_rows.append(parts)
                    
        return header, data_rows
    except Exception as e:
        print(f"Error fetching remote list: {e}")
        return None, []

def load_gist_data():
    """خواندن اطلاعات فعلی از Gist"""
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
                if len(row) < 2: continue
                ip = row[1]
                data_dict[ip] = row
            return data_dict, header
        return {}, None
    except Exception as e:
        print(f"Error loading Gist: {e}")
        return {}, None

def update_gist(content_string):
    """ذخیره اطلاعات جدید در Gist"""
    print("Updating Gist...")
    try:
        data = {
            "files": {
                GIST_FILENAME: {
                    "content": content_string
                }
            }
        }
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
        print("Error: GIST_ID or GIST_TOKEN not set in Secrets!")
        return

    local_data, local_header = load_gist_data()
    new_header, new_rows = get_remote_list()
    
    final_header = local_header if local_header else new_header

    if new_rows:
        for row in new_rows:
            ip = row[1]
            local_data[ip] = row

    valid_servers = filter_dead_servers(local_data)

    if valid_servers:
        output = io.StringIO()
        writer = csv.writer(output)
        if final_header:
            writer.writerow(final_header)
        for ip in valid_servers:
            writer.writerow(valid_servers[ip])
            
        update_gist(output.getvalue())
    else:
        print("No valid servers. Gist not updated.")

if __name__ == "__main__":
    main()