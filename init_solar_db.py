
import sqlite3
c=sqlite3.connect("datacenter.db"); x=c.cursor()
x.execute('''CREATE TABLE IF NOT EXISTS solar_realtime(id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp INTEGER,pv_power REAL,load_power REAL,grid_power REAL,today_energy REAL,total_energy REAL,station_name TEXT)''')
x.execute('''CREATE TABLE IF NOT EXISTS solar_daily(date TEXT PRIMARY KEY,generation REAL,consumption REAL,purchase REAL)''')
c.commit(); c.close(); print("Solar DB ready.")
