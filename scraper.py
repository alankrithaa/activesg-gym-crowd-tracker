import os
import re
import sys
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pytz

URL = "https://activesg.gov.sg/gym-pool-crowd?tab=Gym"
CSV_FILE = "activesg_gym_crowds.csv"
START_TIME_FILE = "scrape_start_timestamp.txt"
DURATION_DAYS = 6  # Runs for exactly 6 full days (144 hours) from the first run
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

    # Stop automatically once 144 hours have elapsed from the very first run
    if not check_6_day_window(now_sgt):
        sys.exit(0)

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }
    resp = requests.get(URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    records = []
    strings = [s.strip() for s in soup.stripped_strings if s.strip()]

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
        raise RuntimeError("Parsed 0 gyms—ActiveSG page layout may have changed or uses client-side fetch.")

    df = pd.DataFrame(records).drop_duplicates(subset=["timestamp_sgt", "gym_name"])
    file_exists = os.path.isfile(CSV_FILE)
    df.to_csv(CSV_FILE, mode="a", header=not file_exists, index=False)
    print(f"[{now_sgt.strftime('%Y-%m-%d %H:%M')}] Saved {len(df)} gyms.")

if __name__ == "__main__":
    scrape()