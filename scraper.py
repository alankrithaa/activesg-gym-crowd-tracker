import os
import re
import sys
import requests
import pandas as pd
from datetime import datetime, timedelta
import pytz
from playwright.sync_api import sync_playwright

URL = "https://activesg.gov.sg/gym-pool-crowd"
CSV_FILE = "activesg_gym_crowds.csv"
START_TIME_FILE = "scrape_start_timestamp.txt"
DURATION_DAYS = 10
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

def is_within_window(now_sgt):
    if not os.path.isfile(START_TIME_FILE) or os.path.getsize(START_TIME_FILE) == 0:
        return True
    with open(START_TIME_FILE, "r") as f:
        start_sgt = datetime.fromisoformat(f.read().strip())
    end_sgt = start_sgt + timedelta(days=DURATION_DAYS)
    if now_sgt > end_sgt:
        print(f"{DURATION_DAYS}-day window completed (Ended: {end_sgt.strftime('%Y-%m-%d %H:%M:%S SGT')}).")
        return False
    return True

def get_live_sg_proxies():
    proxies = []
    sources = [
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=SG&ssl=all&anonymity=all",
        "https://proxylist.geonode.com/api/proxy-list?limit=20&page=1&sort_by=lastChecked&sort_type=desc&country=SG&protocols=http"
    ]
    try:
        r = requests.get(sources[0], timeout=10)
        if r.ok:
            for line in r.text.splitlines():
                line = line.strip()
                if ":" in line:
                    proxies.append(f"http://{line}")
    except Exception as e:
        print(f"ProxyScrape warning: {e}")

    try:
        r = requests.get(sources[1], timeout=10)
        if r.ok:
            for item in r.json().get("data", []):
                ip, port = item.get("ip"), item.get("port")
                if ip and port:
                    proxies.append(f"http://{ip}:{port}")
    except Exception as e:
        print(f"GeoNode warning: {e}")

    return list(dict.fromkeys(proxies))

def fetch_with_proxy(proxy_server=None):
    blocked_types = {"image", "media", "font", "stylesheet"}
    with sync_playwright() as p:
        launch_args = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
        }
        if proxy_server:
            launch_args["proxy"] = {"server": proxy_server}

        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-SG",
            timezone_id="Asia/Singapore",
            ignore_https_errors=True
        )
        context.route("**/*", lambda r: r.abort() if r.request.resource_type in blocked_types else r.continue_())
        page = context.new_page()
        page.goto(URL, wait_until="commit", timeout=25000)
        page.wait_for_function(
            "() => /\\d+\\s*%\\s*full|\\bClosed\\b/i.test(document.body.innerText)",
            timeout=25000
        )
        page.wait_for_timeout(1500)
        text = page.inner_text("body")
        browser.close()
        return text

def fetch_page_text():
    candidates = [None] + get_live_sg_proxies()[:12]
    print(f"Testing {len(candidates)} connection routes (direct + SG proxies)...")

    for idx, proxy in enumerate(candidates, start=1):
        label = proxy if proxy else "Direct Connection"
        try:
            print(f"[{idx}/{len(candidates)}] Trying {label}...")
            text = fetch_with_proxy(proxy)
            if text and len(text.strip()) > 100:
                print(f"Success via {label}!")
                return text
        except Exception as e:
            print(f"Failed via {label}: {str(e).splitlines()[0]}")

    raise RuntimeError("All direct and Singapore proxy routes failed.")

def parse_gyms(page_text, now_sgt):
    strings = [line.strip() for line in page_text.splitlines() if line.strip()]
    gym_lines = []
    in_gym = False
    for line in strings:
        if line == "Gym":
            in_gym = True
            continue
        if in_gym:
            if line in ("Pool", "Pools") or line.startswith("Last updated") or line.startswith("©"):
                break
            gym_lines.append(line)

    target_lines = gym_lines if len(gym_lines) >= 10 else strings
    records = []

    for i in range(1, len(target_lines)):
        text = target_lines[i]
        facility_name = target_lines[i - 1]
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
    return records

def scrape():
    now_sgt = datetime.now(SGT)
    if not is_within_window(now_sgt):
        sys.exit(0)

    page_text = fetch_page_text()
    records = parse_gyms(page_text, now_sgt)

    if not records:
        raise RuntimeError("Parsed 0 gyms from rendered page.")

    if not os.path.isfile(START_TIME_FILE) or os.path.getsize(START_TIME_FILE) == 0:
        with open(START_TIME_FILE, "w") as f:
            f.write(now_sgt.isoformat())
        print(f"Initialized {DURATION_DAYS}-day window starting at: {now_sgt.strftime('%Y-%m-%d %H:%M:%S SGT')}")

    df = pd.DataFrame(records).drop_duplicates(subset=["timestamp_sgt", "gym_name"])
    file_exists = os.path.isfile(CSV_FILE) and os.path.getsize(CSV_FILE) > 0
    df.to_csv(CSV_FILE, mode="a", header=not file_exists, index=False)
    print(f"[{now_sgt.strftime('%Y-%m-%d %H:%M')}] Saved {len(df)} gyms.")

if __name__ == "__main__":
    scrape()