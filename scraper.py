import os
import re
import sys
import pandas as pd
from datetime import datetime, timedelta
import pytz
from playwright.sync_api import sync_playwright

URL = "https://activesg.gov.sg/gym-pool-crowd"
CSV_FILE = "activesg_gym_crowds.csv"
START_TIME_FILE = "scrape_start_timestamp.txt"
DURATION_DAYS = 6
SGT = pytz.timezone("Asia/Singapore")

REGION_MAP = {
    "Ang Mo Kio": "North-East", "Fernvale": "North-East", "Hougang": "North-East",
    "Punggol": "North-East", "Sengkang": "North-East", "Serangoon": "North-East",
    "Teck Ghee": "North-East",
    "Bishan": "Central", "Delta": "Central", "Enabling Village": "Central",
    "Jalan Besar": "Central", "Queenstown": "Central", "Toa Payoh": "Central",
    "Bedok": "East", "Heartbeat @ Bedok": "East", "Katong": "East",
    "Pasir Ris": "East", "Tampines": "East",
    "Canberra": "North", "Woodlands": "North", "Yio Chu Kang": "North", "Yishun": "North",
    "Boon Lay": "West", "Bukit Batok": "West", "Bukit Gombak": "West",
    "Choa Chu Kang": "West", "Clementi": "West", "Jurong": "West",
    "Senja-Cashew": "West"
}

def get_region(gym_name):
    for key, region in REGION_MAP.items():
        if key.lower() in gym_name.lower():
            return region
    return "Other"

def is_within_6_day_window(now_sgt):
    if not os.path.isfile(START_TIME_FILE):
        return True
    with open(START_TIME_FILE, "r") as f:
        content = f.read().strip()
        if not content:
            return True
        start_sgt = datetime.fromisoformat(content)
    end_sgt = start_sgt + timedelta(days=DURATION_DAYS)
    if now_sgt > end_sgt:
        print(f"6-day window completed (Ended: {end_sgt.strftime('%Y-%m-%d %H:%M:%S SGT')}).")
        return False
    return True

def scrape():
    now_sgt = datetime.now(SGT)
    if not is_within_6_day_window(now_sgt):
        sys.exit(0)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-SG",
            timezone_id="Asia/Singapore"
        )
        page = context.new_page()
        # Use 'domcontentloaded' instead of 'networkidle' so background analytics don't time out
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("text=/% full|Closed/i", timeout=30000)
        page.wait_for_timeout(2000)

        page_text = page.inner_text("body")
        browser.close()

    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    
    # Isolate ONLY the Gym section (between 'Gym' tab/header and 'Pool' section)
    in_gym_section = False
    gym_lines = []
    for line in lines:
        if line == "Gym":
            in_gym_section = True
            continue
        if in_gym_section:
            if line in ("Pool", "Pools") or line.startswith("Last updated") or line.startswith("©"):
                break
            gym_lines.append(line)

    # Fallback if tab header layout changes
    target_lines = gym_lines if len(gym_lines) >= 10 else lines

    records = []
    for i in range(1, len(target_lines)):
        text = target_lines[i]
        facility_name = target_lines[i - 1]

        # Skip navigation/tab words
        if facility_name.lower() in ("gym", "pool", "pools", "open", "closed") or "%" in facility_name:
            continue

        pct_match = re.fullmatch(r"(\d{1,3})\s*%\s*full", text, re.IGNORECASE)
        is_closed = text.lower() == "closed"

        if pct_match or is_closed:
            records.append({
                "timestamp_sgt": now_sgt.strftime("%Y-%m-%d %H:%M:%S"),
                "date": now_sgt.strftime("%Y-%m-%d"),
                "day_of_week": now_sgt.strftime("%A"),
                "is_weekend": int(now_sgt.weekday() >= 5),
                "hour": now_sgt.hour,
                "minute": now_sgt.minute,
                "gym_name": facility_name,
                "region": get_region(facility_name),
                "status": "Open" if pct_match else "Closed",
                "capacity_pct": int(pct_match.group(1)) if pct_match else None
            })

    if not records:
        print("DEBUG BODY PREVIEW:", page_text[:800])
        raise RuntimeError("Parsed 0 gyms—check DEBUG BODY PREVIEW in logs.")

    # Initialize 6-day window only on first actual data save
    if not os.path.isfile(START_TIME_FILE) or os.path.getsize(START_TIME_FILE) == 0:
        with open(START_TIME_FILE, "w") as f:
            f.write(now_sgt.isoformat())

    df = pd.DataFrame(records).drop_duplicates(subset=["timestamp_sgt", "gym_name"])
    file_exists = os.path.isfile(CSV_FILE) and os.path.getsize(CSV_FILE) > 0
    df.to_csv(CSV_FILE, mode="a", header=not file_exists, index=False)
    print(f"[{now_sgt.strftime('%Y-%m-%d %H:%M')}] Saved {len(df)} gyms.")

if __name__ == "__main__":
    scrape()