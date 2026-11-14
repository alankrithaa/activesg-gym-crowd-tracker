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
DURATION_DAYS = 6  # Runs for exactly 6 full days (144 hours) from first successful run
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

def check_6_day_window(now_sgt):
    if not os.path.isfile(START_TIME_FILE):
        with open(START_TIME_FILE, "w") as f:
            f.write(now_sgt.isoformat())
        print(f"Initialized 6-day window starting at: {now_sgt.strftime('%Y-%m-%d %H:%M:%S SGT')}")
        return True

    with open(START_TIME_FILE, "r") as f:
        start_sgt = datetime.fromisoformat(f.read().strip())

    end_sgt = start_sgt + timedelta(days=DURATION_DAYS)
    if now_sgt > end_sgt:
        print(f"6-day window completed (Ended: {end_sgt.strftime('%Y-%m-%d %H:%M:%S SGT')}). Skipping scrape.")
        return False
    return True

def scrape():
    now_sgt = datetime.now(SGT)

    if not check_6_day_window(now_sgt):
        sys.exit(0)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-SG",
            timezone_id="Asia/Singapore"
        )
        page = context.new_page()
        page.goto(URL, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(3000)  # Give client-side crowd counters 3s to finish rendering

        page_text = page.inner_text("body")
        browser.close()

    strings = [line.strip() for line in page_text.splitlines() if line.strip()]
    records = []

    for i, text in enumerate(strings):
        if i == 0:
            continue
        facility_name = strings[i - 1]
        if "gym" not in facility_name.lower() or "pool" in facility_name.lower():
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
        # Print first 500 chars of body text to help debug if selector ever shifts
        print("DEBUG BODY PREVIEW:", page_text[:500])
        raise RuntimeError("Parsed 0 gyms—check DEBUG BODY PREVIEW in logs.")

    df = pd.DataFrame(records).drop_duplicates(subset=["timestamp_sgt", "gym_name"])
    file_exists = os.path.isfile(CSV_FILE)
    df.to_csv(CSV_FILE, mode="a", header=not file_exists, index=False)
    print(f"[{now_sgt.strftime('%Y-%m-%d %H:%M')}] Saved {len(df)} gyms.")

if __name__ == "__main__":
    scrape()