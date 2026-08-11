import sqlite3
from datetime import datetime

def init_db():
    conn = sqlite3.connect('datacenter.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY,
            account_name TEXT,
            balance REAL,
            split_ratio REAL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            type TEXT,
            amount REAL,
            note TEXT,
            account_id TEXT
        )
    ''')

    cursor.execute("SELECT COUNT(*) FROM accounts")
    if cursor.fetchone()[0] == 0:
        initial_accounts = [
            ('kbank_1', 'กสิกรไทย 1 (บัญชีรวม)', 0.0, 0.0),
            ('kbank_2', 'กสิกรไทย 2 (กองทุนโซล่า)', 0.0, 0.70),
            ('ktb_1', 'กรุงไทย 1 (สำรองฉุกเฉิน)', 0.0, 0.15),
            ('ktb_2', 'กรุงไทย 2 (ลงทุน)', 0.0, 0.15)
        ]
        cursor.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?)", initial_accounts)

    conn.commit()
    conn.close()

def add_transfer(amount, note):
    conn = sqlite3.connect('datacenter.db')
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("UPDATE accounts SET balance = balance + ? WHERE account_id = 'kbank_1'", (amount,))
    cursor.execute("INSERT INTO transactions (timestamp, type, amount, note, account_id) VALUES (?, 'TRANSFER_IN', ?, ?, 'kbank_1')", (timestamp, amount, note))
    conn.commit()
    conn.close()

def add_expense(account_id, amount, note):
    conn = sqlite3.connect('datacenter.db')
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("UPDATE accounts SET balance = balance - ? WHERE account_id = ?", (amount, account_id))
    cursor.execute("INSERT INTO transactions (timestamp, type, amount, note, account_id) VALUES (?, 'EXPENSE', ?, ?, ?)", (timestamp, amount, note, account_id))
    conn.commit()
    conn.close()

def update_account_balance(account_id, new_balance):
    conn = sqlite3.connect('datacenter.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE accounts SET balance = ? WHERE account_id = ?", (new_balance, account_id))
    conn.commit()
    conn.close()

def auto_allocate_monthly():
    conn = sqlite3.connect('datacenter.db')
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM accounts WHERE account_id = 'kbank_1'")
    res = cursor.fetchone()
    if not res or res[0] <= 0:
        conn.close()
        return "ไม่มี 0 บาท หรือยอดเป็นลบ"
    total_amount = res[0]
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("SELECT account_id, split_ratio FROM accounts WHERE account_id != 'kbank_1'")
    ratios = cursor.fetchall()
    for acc_id, ratio in ratios:
        add_val = total_amount * ratio
        cursor.execute("UPDATE accounts SET balance = balance + ? WHERE account_id = ?", (add_val, acc_id))
    cursor.execute("UPDATE accounts SET balance = 0.0 WHERE account_id = 'kbank_1'")
    cursor.execute("INSERT INTO transactions (timestamp, type, amount, note, account_id) VALUES (?, 'ALLOCATION_CUTOFF', ?, 'ตัดรอบอัตโนมัติ', 'kbank_1')", (timestamp, total_amount))
    conn.commit()
    conn.close()
    return f"ตัดรอบสำเร็จ! {total_amount:,.2f} บาท"

init_db()