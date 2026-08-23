import sqlite3
import requests
import time
import os
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

VERUS_ADDRESS = "REn28U7KUABvRQTWwWjKYnkCYyiBC1ga7L"

DB_FILE = "datacenter.db"


JACKPOT_THRESHOLD = 0.05
JACKPOT_URL = f"https://luckpool.net/verus/earnings/{VERUS_ADDRESS}/"
JACKPOT_STATE_FILE = "jackpot_collector_state.json"

def get_collection_start_time():
    """
    Start Jackpot recording from the first run of this collector version.
    The start time is kept locally so restarting the PC does not reset it.
    """
    try:
        if os.path.exists(JACKPOT_STATE_FILE):
            with open(JACKPOT_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            value = data.get("start_time")
            if value:
                return datetime.fromisoformat(value)

        now = datetime.now(timezone.utc)
        with open(JACKPOT_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"start_time": now.isoformat()}, f, ensure_ascii=False, indent=2)

        print("Jackpot recording started:", now.isoformat())
        return now

    except Exception as e:
        print("Jackpot state error:", e)
        return datetime.now(timezone.utc)

JACKPOT_COLLECTION_START = get_collection_start_time()

def create_jackpot_table():
    """
    Local secondary backup for Jackpot history.
    The permanent Dashboard history is stored in Supabase jackpot_history.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS jackpot_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        block INTEGER UNIQUE,
        amount REAL NOT NULL,
        timestamp TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()

def _parse_jackpot_item(item):
    """
    LuckPool Earnings API format confirmed from the working app.py:
        timestamp:block:amount

    timestamp = Unix timestamp in seconds
    block     = Verus block number
    amount    = Jackpot amount in VRSC
    """
    timestamp = None
    block = None
    amount = None

    if isinstance(item, dict):
        timestamp = item.get("timestamp") or item.get("time") or item.get("date")
        block = item.get("block") or item.get("height") or item.get("block_height")
        amount = item.get("amount") or item.get("jackpot") or item.get("value")

    elif isinstance(item, (list, tuple)) and len(item) >= 3:
        timestamp, block, amount = item[0], item[1], item[2]

    elif isinstance(item, str):
        text = item.strip()

        # Confirmed LuckPool format: timestamp:block:amount
        parts = text.split(":")

        if len(parts) >= 3:
            timestamp = parts[0].strip()
            block = parts[1].strip()
            amount = parts[2].strip()

    try:
        if timestamp is None or block is None or amount is None:
            return None

        # LuckPool may return Unix timestamp in seconds or milliseconds.
        timestamp_num = float(str(timestamp).strip())
        if timestamp_num > 1_000_000_000_000:
            timestamp_num /= 1000.0

        block_num = int(float(str(block).strip()))
        amount_num = float(
            str(amount).replace("VRSC", "").strip()
        )

        if block_num <= 0 or amount_num < JACKPOT_THRESHOLD:
            return None

        # Store timestamp as UTC ISO-8601 in Supabase/SQLite.
        timestamp_iso = datetime.fromtimestamp(
            timestamp_num,
            tz=timezone.utc
        ).isoformat()

        return timestamp_iso, block_num, amount_num

    except Exception:
        return None

def get_jackpots():
    """
    Read the LuckPool Earnings API and return all qualifying Jackpot
    records currently exposed by the API.
    """
    try:
        r = requests.get(
            JACKPOT_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20
        )

        print("Jackpot Status:", r.status_code)

        if r.status_code != 200:
            return []

        data = r.json()

        if isinstance(data, dict):
            for key in ("earnings", "data", "results", "items"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                data = [data]

        if not isinstance(data, list):
            return []

        unique = {}

        for item in data:
            parsed = _parse_jackpot_item(item)

            if parsed:
                timestamp, block, amount = parsed
                unique[block] = (timestamp, block, amount)

        records = list(unique.values())

        # Do not backfill old LuckPool history.
        # Only Jackpot records generated after this collector first started are stored.
        records = [
            row for row in records
            if datetime.fromisoformat(row[0]) >= JACKPOT_COLLECTION_START
        ]

        records.sort(key=lambda x: x[0])

        print(f"Jackpot records found: {len(records)}")
        return records

    except Exception as e:
        print("Jackpot API Error:", e)
        return []

def save_jackpots(records):
    """
    Save Jackpot history permanently to Supabase.
    A local SQLite copy is also kept as a secondary backup.
    """
    if not records:
        return

    # Local backup
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    for timestamp, block, amount in records:
        c.execute("""
            INSERT OR IGNORE INTO jackpot_history
            (block, amount, timestamp, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            block,
            amount,
            timestamp,
            datetime.now().astimezone().isoformat()
        ))

    conn.commit()
    conn.close()

    # Permanent Supabase history
    try:
        rows = [
            {
                "block": block,
                "amount": float(amount),
                "timestamp": timestamp
            }
            for timestamp, block, amount in records
        ]

        supabase.table("jackpot_history").upsert(
            rows,
            on_conflict="block"
        ).execute()

        print(f"Supabase: Jackpot history saved/updated {len(rows)} record(s)")

    except Exception as e:
        print("Supabase Jackpot Error:", e)

create_jackpot_table()

def create_table():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS hashrate_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        hashrate REAL
    )
    """)

    conn.commit()
    conn.close()

def get_hashrate():
    try:
        url = "https://luckpool.net/verus/miner/REn28U7KUABvRQTwWwjKYnkCYyiBC1ga7L"

        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        r = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        print("Status:", r.status_code)

        if r.status_code == 200:
            data = r.json()

            hashrate = data.get("hashrateString", "0 MH")

            if "MH" in hashrate:
                value = float(hashrate.replace("MH", "").strip())
            elif "KH" in hashrate:
                value = float(hashrate.replace("KH", "").strip()) / 1000
            else:
                value = 0

            print("LuckPool Connected")
            return value

    except Exception as e:
        print("ERROR:", e)

    return None

def save_hashrate(value):

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute(
        """
        INSERT INTO hashrate_history
        (timestamp, hashrate)
        VALUES (?,?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            value
        )
    )

    conn.commit()
    conn.close()
    try:
        supabase.table("hashrate_history").insert({
            "timestamp": datetime.now().astimezone().isoformat(),
            "hashrate": float(value)
        }).execute()

        print("Supabase: Hashrate saved")

    except Exception as e:
        print("Supabase Error:", e)

create_table()

print("Collector Started")

while True:

    hr = get_hashrate()

    if hr:

        save_hashrate(hr)

        print(
            datetime.now().strftime("%H:%M:%S"),
            "Saved",
            hr,
            "MH"
        )

    # Permanently collect Jackpot history on every collector cycle.
    jackpots = get_jackpots()
    if jackpots:
        save_jackpots(jackpots)
    else:
        print(
            datetime.now().strftime("%H:%M:%S"),
            "No Jackpot records found"
        )
    if GITHUB_ACTIONS:
        print("GitHub Actions: one-shot mode complete.")
        break

    time.sleep(300)
