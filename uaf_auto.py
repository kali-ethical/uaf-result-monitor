import requests
from bs4 import BeautifulSoup
import re
import csv
from urllib.parse import urljoin
from datetime import datetime
import time
import hashlib
import os

# ----------------- YOUR SETTINGS -----------------
BASE_URL = "https://lms.uaf.edu.pk"
LOGIN_URL = urljoin(BASE_URL, "/login/index.php")
OUTPUT_CSV = "result.csv"
HASH_FILE = "last_result_hash.txt"

# ---------- TELEGRAM CREDENTIALS (from Environment Variables) ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

# ---------- YOUR REGISTRATION NUMBER ----------
# Set this in Render Environment Variables as "REG_NO", or change the default below
REG_NO = os.environ.get("REG_NO", "2025-ag-11198")  # <-- Change default if needed

# Check every 1 hour (3600 seconds)
CHECK_INTERVAL = 3600

requests.packages.urllib3.disable_warnings()
# ----------------------------------------------------------------------

def send_telegram(message, file_path=None):
    """Send a Telegram message and optionally a file."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Failed to send message: {e}")

    if file_path and os.path.exists(file_path):
        url_file = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
        files = {"document": open(file_path, "rb")}
        data = {"chat_id": TELEGRAM_CHAT_ID}
        try:
            requests.post(url_file, data=data, files=files, timeout=15)
        except Exception as e:
            print(f"Failed to send file: {e}")

def get_token(soup, html_text):
    for script in soup.find_all("script"):
        if script.string and "token" in script.string:
            match = re.search(r"token['\"]\)\.value\s*=\s*['\"]([^'\"]+)", script.string)
            if match:
                return match.group(1)
    token_input = soup.find("input", {"name": "token"})
    if token_input and token_input.get("value"):
        return token_input["value"]
    return None

def fetch_and_check(reg_no):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    session.verify = False

    try:
        resp = session.get(LOGIN_URL)
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to load login page: {e}")
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    token = get_token(soup, resp.text)
    if not token:
        print("CSRF token not found")
        return

    result_form = None
    for form in soup.find_all("form"):
        if "uaf_student_result.php" in form.get("action", ""):
            result_form = form
            break
    if not result_form:
        print("Result form not found")
        return

    post_url = urljoin(LOGIN_URL, result_form.get("action"))
    payload = {"Register": reg_no, "token": token}
    session.headers.update({"Referer": LOGIN_URL})

    try:
        response = session.post(post_url, data=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to submit form: {e}")
        return

    result_soup = BeautifulSoup(response.text, "html.parser")

    info_table = None
    for table in result_soup.find_all("table", class_="tab-content"):
        if table.find("td", string=re.compile(r"Registration\s*#")):
            info_table = table
            break

    reg_val = name_val = ""
    if info_table:
        for row in info_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True)
                value = cells[1].get_text(strip=True)
                if "Registration #" in key:
                    reg_val = value
                elif "Student Full Name" in key:
                    name_val = value

    grades_table = None
    for table in result_soup.find_all("table", class_="tab-content"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if "Course Code" in headers:
            grades_table = table
            break

    if not grades_table:
        print("Grades table not found")
        return

    rows = grades_table.find_all("tr")
    if len(rows) < 2:
        print("Grades table is empty")
        return

    data_rows = []
    for row in rows[1:]:
        cols = row.find_all("td")
        if cols:
            data_rows.append([col.get_text(strip=True) for col in cols])

    content_for_hash = ""
    for row in data_rows:
        content_for_hash += "|".join(row) + "\n"
    current_hash = hashlib.md5(content_for_hash.encode("utf-8")).hexdigest()

    old_hash = ""
    if os.path.exists(HASH_FILE):
        with open(HASH_FILE, "r") as f:
            old_hash = f.read().strip()

    if current_hash != old_hash:
        print(f"\n🚨 NEW RESULT FOUND! {datetime.now()}")
        with open(HASH_FILE, "w") as f:
            f.write(current_hash)

        csv_headers = ["Registration", "Name", "Semester", "Teacher", "Course Code",
                       "Course Title", "Credits", "Mid", "Assignment", "Final",
                       "Practical", "Total", "Grade"]
        file_exists = os.path.exists(OUTPUT_CSV)
        with open(OUTPUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if not file_exists or os.path.getsize(OUTPUT_CSV) == 0:
                writer.writerow(csv_headers)
            for row in data_rows:
                if row and row[0].isdigit():
                    row = row[1:]
                while len(row) < 11:
                    row.append("")
                full_row = [reg_val, name_val] + row[:11]
                writer.writerow(full_row)

        msg = f"✅ <b>New result is available!</b>\n"
        msg += f"👤 {name_val} ({reg_val})\n"
        msg += f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        msg += f"📊 Total courses: {len(data_rows)}"
        send_telegram(msg, OUTPUT_CSV)

        print("📨 Notification sent to your phone.")
    else:
        print(f"{datetime.now()} - No new result yet.")

def main_loop(reg_no):
    print(f"🔄 Monitoring started. Checking every {CHECK_INTERVAL//60} minutes.")
    send_telegram(f"🚀 UAF Result Monitor started.\nRegistration: {reg_no}")
    while True:
        try:
            fetch_and_check(reg_no)
        except Exception as e:
            print(f"An error occurred: {e}")
            send_telegram(f"⚠️ Error: {str(e)}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    print(f"🚀 Starting UAF Result Monitor for: {REG_NO}")
    main_loop(REG_NO)
