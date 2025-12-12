import requests
import csv
import os
import socket
import concurrent.futures

# --- تنظیمات ---
CSV_FILE = 'server_list.csv'
URL = "http://www.vpngate.net/api/iphone/"
TIMEOUT_SECONDS = 2.0   # حداکثر زمان انتظار برای پاسخ (تایم‌اوت)
MAX_WORKERS = 50        # تعداد چک‌های همزمان (برای سرعت بالا و عدم فشار به گیت‌هاب)
VPN_PORT = 443          # پورت پیش‌فرض برای تست (اکثر سرورهای vpngate روی 443 باز هستند)

def get_remote_list():
    """دانلود لیست جدید از سایت"""
    try:
        print("Downloading new list from VPN Gate...")
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
            if line.startswith('#HostName'):
                header = line.split(',')
            else:
                parts = line.split(',')
                if len(parts) > 5:
                    data_rows.append(parts)
        return header, data_rows
    except Exception as e:
        print(f"Error downloading new list: {e}")
        return None, []

def load_local_list():
    """خواندن لیست موجود در فایل"""
    if not os.path.exists(CSV_FILE):
        return {}, None

    data_dict = {}
    header = None
    try:
        with open(CSV_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.reader(f)
            for row in reader:
                if not row: continue
                if row[0].startswith('#HostName'):
                    header = row
                    continue
                if len(row) < 2: continue
                ip = row[1]
                data_dict[ip] = row
    except Exception as e:
        print(f"Error reading local file: {e}")
        return {}, None
    return data_dict, header

def check_server_connectivity(server_row):
    """
    تست اتصال به سرور.
    اگر وصل شد، خود سطر را برمی‌گرداند.
    اگر تایم‌اوت شد، None برمی‌گرداند.
    """
    ip = server_row[1]
    try:
        # ایجاد یک سوکت TCP
        # این دقیق‌ترین روش برای سنجش زنده بودن سرویس VPN است
        with socket.create_connection((ip, VPN_PORT), timeout=TIMEOUT_SECONDS):
            return server_row # سرور زنده است
    except (socket.timeout, socket.error, OSError):
        return None # سرور مرده است

def filter_dead_servers(servers_dict):
    """بررسی لیست و حذف سرورهای خاموش با سرعت بالا (چند نخی)"""
    print(f"Verifying connectivity for {len(servers_dict)} servers...")
    print(f"Settings: Timeout={TIMEOUT_SECONDS}s, Threads={MAX_WORKERS}")
    
    alive_servers = {}
    dead_count = 0
    
    # تبدیل دیکشنری به لیست برای پردازش
    rows_to_check = list(servers_dict.values())
    
    # استفاده از ThreadPoolExecutor برای اجرای همزمان
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # ارسال وظایف به تردها
        futures = {executor.submit(check_server_connectivity, row): row for row in rows_to_check}
        
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                # سرور زنده است، آن را نگه دار
                ip = result[1]
                alive_servers[ip] = result
            else:
                dead_count += 1
                
    print(f"Verification done. Alive: {len(alive_servers)} | Removed (Dead): {dead_count}")
    return alive_servers

def main():
    # ۱. بارگذاری لیست محلی
    local_data, local_header = load_local_list()
    print(f"Local servers: {len(local_data)}")

    # ۲. دانلود لیست جدید
    new_header, new_rows = get_remote_list()
    print(f"New fetched servers: {len(new_rows)}")

    final_header = local_header if local_header else new_header

    # ۳. ادغام (Merge) - اضافه کردن جدیدها به لیست محلی
    # هنوز چک نمی‌کنیم، اول همه را یک کاسه می‌کنیم
    if new_rows:
        for row in new_rows:
            ip = row[1]
            local_data[ip] = row # اگر تکراری باشد آپدیت می‌شود، اگر نباشد اضافه می‌شود

    total_before_check = len(local_data)
    print(f"Total servers before check: {total_before_check}")

    # ۴. پایش سلامت (Health Check) و حذف مرده‌ها
    # این بخش سرورهایی که تایم‌اوت می‌شوند را دور می‌ریزد
    final_valid_servers = filter_dead_servers(local_data)

    # ۵. ذخیره نهایی
    if final_valid_servers:
        with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            f.write("*vpn_servers\n") # خط استاندارد vpngate
            
            if final_header:
                writer.writerow(final_header)
                
            for ip in final_valid_servers:
                writer.writerow(final_valid_servers[ip])
        print("List updated and saved successfully.")
    else:
        print("Warning: No valid servers found after filtering!")

if __name__ == "__main__":
    main()