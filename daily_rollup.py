
import sqlite3
conn=sqlite3.connect("datacenter.db")
cur=conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS jackpot_daily(date TEXT PRIMARY KEY,jackpot_count INTEGER,reward REAL)""")
cur.execute("""CREATE TABLE IF NOT EXISTS verus_daily(date TEXT PRIMARY KEY,max_hashrate REAL,avg_hashrate REAL,min_hashrate REAL)""")
conn.commit(); conn.close()
print("Daily tables ready.")
