import sqlite3
import requests
import time
import os
from datetime import datetime
from supabase import create_client

load_dotenv()

GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

VERUS_ADDRESS = "REn28U7KUABvRQTWwWjKYnkCYyiBC1ga7L"

DB_FILE = "datacenter.db"

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

    c.execute("""
    CREATE TABLE IF NOT EXISTS earnings_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        earned_at TEXT,
        amount REAL,
        source TEXT DEFAULT 'LuckPool',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
    if supabase is not None:
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

    else:

        print(
            datetime.now().strftime("%H:%M:%S"),
            "LuckPool Error"
        )
    if GITHUB_ACTIONS:
        print("GitHub Actions: one-shot mode complete.")
        break

    time.sleep(300)

