import sqlite3
from datetime import datetime
import solar_api

conn = sqlite3.connect("datacenter.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS solar_realtime(
    timestamp TEXT,
    station_name TEXT,
    installed_capacity REAL,
    generation_power REAL,
    generation_total REAL,
    use_power REAL,
    running_days INTEGER
)
""")

station = solar_api.station_detail()["stationList"][0]
real = solar_api.real_time()

cur.execute("""
INSERT INTO solar_realtime (
    timestamp,
    pv_power,
    load_power,
    grid_power,
    today_energy,
    total_energy,
    station_name
)
VALUES (?,?,?,?,?,?,?)
""", (
    real["lastUpdateTime"],              # เวลาอัปเดตจาก SOLARMAN
    real.get("generationPower") or 0,   # กำลังผลิต (W)
    real.get("usePower") or 0,          # ใช้ไฟในบ้าน
    real.get("gridPower") or 0,         # ส่งเข้ากริด
    real.get("dailyGeneration") or 0,   # ผลิตวันนี้ (ถ้ายังไม่มีให้เป็น 0)
    real.get("generationTotal") or 0,   # ผลิตสะสม
    station["name"]
))

conn.commit()
conn.close()

print("☀ SOLARMAN OpenAPI saved successfully")
print(f"กำลังผลิต: {real['generationPower']} W")
print(f"ผลิตสะสม: {real['generationTotal']} kWh")