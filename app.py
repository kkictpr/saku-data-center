import os
from dotenv import load_dotenv
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import sqlite3
import pandas as pd
from datetime import datetime
import requests
import time
import threading
import base64
from supabase import create_client
ASSETS = os.path.join(os.path.dirname(__file__), "assets")

def img_to_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

solar_panel_b64 = img_to_b64(os.path.join(ASSETS, "solar_panel.png"))
house_b64 = img_to_b64(os.path.join(ASSETS, "house.png"))

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="Saku Data Center", layout="wide", initial_sidebar_state="expanded")


st_autorefresh(interval=60_000, key="solar_refresh")
def init_db():
    try:
        conn = sqlite3.connect('datacenter.db', timeout=15)
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY,
            account_name TEXT,
            balance REAL,
            split_ratio REAL
        )''')
        default_accs = [
            ('cash', '💵 เงินสดในมือ (Cash)', 0.0, 0.0),
            ('kbank_1', 'กสิกรไทย 1 (บัญชีรวม)', 0.0, 0.0),
            ('kbank_2', 'กสิกรไทย 2 (กองทุนโซล่า)', 0.0, 0.70),
            ('ktb_1', 'กรุงไทย 1 (สำรองฉุกเฉิน)', 0.0, 0.15),
            ('ktb_2', 'กรุงไทย 2 (ลงทุน)', 0.0, 0.15)
        ]
        for acc in default_accs:
            c.execute("INSERT OR IGNORE INTO accounts (account_id, account_name, balance, split_ratio) VALUES (?, ?, ?, ?)", acc)

        c.execute('''CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            type TEXT,
            amount REAL,
            category TEXT,
            note TEXT,
            account_id TEXT
        )''')

        try:
            c.execute("ALTER TABLE transactions ADD COLUMN category TEXT")
        except Exception:
            pass

        c.execute('''CREATE TABLE IF NOT EXISTS fixed_costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            cost_type TEXT,
            promo_amount REAL,
            normal_amount REAL,
            promo_months INTEGER,
            start_date TEXT,
            due_day INTEGER,
            category TEXT,
            note TEXT,
            account_id TEXT,
            status TEXT,
            deduct_mode TEXT
        )''')

        try:
            c.execute("ALTER TABLE fixed_costs ADD COLUMN deduct_mode TEXT")
        except Exception:
            pass

        c.execute('''CREATE TABLE IF NOT EXISTS fixed_cost_monthly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fixed_cost_id INTEGER,
            year_month TEXT,
            amount REAL,
            UNIQUE(fixed_cost_id, year_month)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loan_type TEXT,
            person_name TEXT,
            amount REAL,
            account_id TEXT,
            due_date TEXT,
            note TEXT,
            status TEXT
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS ice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            category TEXT,
            price REAL,
            image_path TEXT
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS ice_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            items_json TEXT,
            total_amount REAL,
            payment_method TEXT
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"Database Error: {e}")

init_db()

def safe_float(val_str):
    try:
        if val_str is None:
            return None
        cleaned = str(val_str).replace(',', '').strip()
        if cleaned == "":
            return None
        return float(cleaned)
    except Exception:
        return None

def get_setting(key, default=""):
    try:
        conn = sqlite3.connect('datacenter.db', timeout=15)
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default

def save_setting(key, value):
    try:
        conn = sqlite3.connect('datacenter.db', timeout=15)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()
    except Exception:
        pass



def get_verus_wallet_balance(address):
    try:
        url = f"https://insight.verus.io/api/addr/{address}"
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )

        if r.status_code == 200:
            d = r.json()

            if "balance" in d:
                return float(d["balance"])

            if "balanceSat" in d:
                return float(d["balanceSat"]) / 100000000

            if "finalBalance" in d:
                return float(d["finalBalance"])

            if "finalBalanceSat" in d:
                return float(d["finalBalanceSat"]) / 100000000

            print(d)

    except Exception as e:
        print(e)

    return None
def send_telegram_auto(token, chat_id, freq_name=""):
    try:
        conn = sqlite3.connect('datacenter.db', timeout=15)
        c = conn.cursor()
        c.execute("SELECT account_name, balance FROM accounts")
        acc_summary = c.fetchall()
        
        now_dt = datetime.now()
        current_ym = now_dt.strftime('%Y-%m')
        
        c.execute("""
            SELECT type, amount, category, note 
            FROM transactions 
            WHERE timestamp LIKE ? 
              AND note NOT LIKE '%Fixed Cost%' 
              AND note NOT LIKE '%ให้%ยืมเงิน%'
              AND note NOT LIKE '%กู้ยืม%'
        """, (f"{current_ym}%",))
        tx_rows = c.fetchall()
        
        c.execute("SELECT id, name, cost_type, promo_amount, normal_amount, promo_months, start_date FROM fixed_costs")
        fc_rows = c.fetchall()
        conn.close()

        total_bal = sum([row[1] for row in acc_summary])
        
        total_in = 0.0
        total_exp = 0.0
        in_details = []
        exp_details = []
        
        for t_type, amt, cat, note in tx_rows:
            cat_str = f"[{cat}] " if cat else ""
            note_str = f" - {note}" if note else ""
            if t_type == 'TRANSFER_IN':
                total_in += amt
                in_details.append(f"• {cat_str}{amt:,.2f} ฿{note_str}")
            elif t_type == 'EXPENSE':
                total_exp += amt
                exp_details.append(f"• {cat_str}{amt:,.2f} ฿{note_str}")

        total_fc = 0.0
        for row in fc_rows:
            fc_id, name, c_type, p_amt, n_amt, p_mon, s_date = row
            item_amt = 0.0
            if c_type == 'PROMO':
                try:
                    start_dt = datetime.strptime(s_date, '%Y-%m-%d')
                    diff_m = (now_dt.year - start_dt.year) * 12 + (now_dt.month - start_dt.month)
                    p_m = int(p_mon) if p_mon else 3
                except:
                    diff_m = 0
                    p_m = 3
                item_amt = p_amt if 0 <= diff_m < p_m else n_amt
            elif c_type == 'CUSTOM':
                conn = sqlite3.connect('datacenter.db', timeout=15)
                c = conn.cursor()
                c.execute("SELECT amount FROM fixed_cost_monthly WHERE fixed_cost_id = ? AND year_month = ?", (fc_id, current_ym))
                res = c.fetchone()
                conn.close()
                item_amt = res[0] if res else 0.0
            else:
                item_amt = n_amt
            total_fc += item_amt

        msg = f"⏰ [Saku Data Center] รายงานสรุปการเงิน ({freq_name})\n"
        msg += f"📅 ประจำเดือน: {current_ym}\n"
        msg += "----------------------------------\n"
        for acc_name, bal in acc_summary:
            msg += f"• {acc_name}: {bal:,.2f} ฿\n"
        msg += "----------------------------------\n"
        msg += f"💰 ยอดทรัพย์สินรวมทั้งหมด (เงินสด+ทุกบัญชี): {total_bal:,.2f} บาท\n\n"
        
        msg += f"📥 **รายรับรวมเดือนนี้:** {total_in:,.2f} บาท\n"
        if in_details:
            msg += "\n".join(in_details[:5]) + "\n"
            if len(in_details) > 5:
                msg += "• ...และรายการอื่นๆ\n"
        
        msg += f"\n💸 **รายจ่ายทั่วไปเดือนนี้:** {total_exp:,.2f} บาท\n"
        if exp_details:
            msg += "\n".join(exp_details[:5]) + "\n"
            if len(exp_details) > 5:
                msg += "• ...และรายการอื่นๆ\n"

        msg += f"\n📌 **ประมาณการ Fixed Cost เดือนนี้:** {total_fc:,.2f} บาท\n"
        msg += f"🌟 **รวมรายจ่ายทั้งหมด (Fixed Cost + รายจ่ายทั่วไป):** `{total_fc + total_exp:,.2f}` บาท\n"
        msg += "----------------------------------\n"
        msg += f"🕒 อัปเดตเมื่อ: {now_dt.strftime('%Y-%m-%d %H:%M')}"

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass

menu_options = [
    "หน้าแรก (Portal Hub)",
    "💰 การเงิน (Finance System)",
    "📈 หุ้นและการลงทุน (Investment)",
    "📊 สินทรัพย์รวม (Asset Center)",
    "🎯 Freedom Tracker",
    "📱 Telegram Center",
    "⛏️ Verus (Mining Farm)",
    "☀️ Solar (Solar System)",
    "🍦 ร้านไอศกรีม (Ice Cream)",
    "🌾 นาข้าว (Rice Farm)",
    "🏢 หอพัก (Rental Room)"
    
]

if 'nav_menu' not in st.session_state:
    st.session_state['nav_menu'] = "หน้าแรก (Portal Hub)"

selected_menu = st.sidebar.radio("เลือกเมนูหลัก:", menu_options, index=menu_options.index(st.session_state['nav_menu']) if st.session_state['nav_menu'] in menu_options else 0)
st.session_state['nav_menu'] = selected_menu
menu = st.session_state['nav_menu']

# Refresh เฉพาะหน้าปัจจุบัน โดยคงเมนู/หน้าที่กำลังเปิดไว้
if st.sidebar.button("🔄 Refresh หน้านี้", key="refresh_current_page", use_container_width=True):
    st.rerun()

acc_options = {
    'cash': '💵 เงินสดในมือ (Cash)',
    'kbank_1': 'กสิกรไทย 1 (บัญชีรวม)',
    'kbank_2': 'กสิกรไทย 2 (กองทุนโซล่า)',
    'ktb_1': 'กรุงไทย 1 (สำรองฉุกเฉิน)',
    'ktb_2': 'กรุงไทย 2 (ลงทุน)'
}

if menu == "หน้าแรก (Portal Hub)":
    st.markdown("""
        <div style="padding: 10px 0px 10px 0px;">
            <p style="font-size: 42px; font-weight: 800; color: #1e293b; margin-bottom: 0px;">🏠 Saku Data Center</p>
            <p style="font-size: 17px; color: #64748b; margin-top: 5px; margin-bottom: 25px;">ศูนย์รวมระบบจัดการข้อมูลและบริหารธุรกิจครบวงจร เลือกเมนูด้านล่างเพื่อใช้งานได้ทันทีครับ</p>
        </div>
    """, unsafe_allow_html=True)
    st.markdown("---")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("💰 การเงิน (Finance System)"):
            st.session_state['nav_menu'] = "💰 การเงิน (Finance System)"
            st.rerun()
    with col2:
        if st.button("⛏️ Verus (Mining Farm)"):
            st.session_state['nav_menu'] = "⛏️ Verus (Mining Farm)"
            st.rerun()
    with col3:
        if st.button("☀️ Solar (Solar System)"):
            st.session_state['nav_menu'] = "☀️ Solar (Solar System)"
            st.rerun()

    col4, col5, col6 = st.columns(3)
    with col4:
        if st.button("🍦 ร้านไอศกรีม (Ice Cream)"):
            st.session_state['nav_menu'] = "🍦 ร้านไอศกรีม (Ice Cream)"
            st.rerun()
    with col5:
        if st.button("🌾 นาข้าว (Rice Farm)"):
            st.session_state['nav_menu'] = "🌾 นาข้าว (Rice Farm)"
            st.rerun()
    with col6:
        if st.button("🏢 หอพัก (Rental Room)"):
            st.session_state['nav_menu'] = "🏢 หอพัก (Rental Room)"
            st.rerun()
    with col6:
        if st.button("📊 สินทรัพย์รวม (Asset Center)"):
            st.session_state['nav_menu'] = "📊 สินทรัพย์รวม (Asset Center)"
            st.rerun()
            

elif menu == "💰 การเงิน (Finance System)":
    if st.button("⬅️ กลับหน้าแรก (Portal Hub)"):
        st.session_state['nav_menu'] = "หน้าแรก (Portal Hub)"
        st.rerun()
    st.markdown("---")
    
    st.title("💰 ระบบจัดการการเงิน (Finance System)")

    st.markdown("""
        <style>
        div[data-testid="stMetricValue"] {
            font-size: 19px !important;
            white-space: nowrap !important;
        }
        div[data-testid="stMetricLabel"] {
            font-size: 13px !important;
        }
        </style>
    """, unsafe_allow_html=True)

    st.subheader("🏦 ยอดเงินคงเหลือ ณ ปัจจุบัน")
    try:
        conn = sqlite3.connect('datacenter.db', timeout=15)
        c = conn.cursor()
        c.execute("SELECT account_id, account_name, balance FROM accounts")
        acc_rows = c.fetchall()
        conn.close()
        
        if acc_rows:
            cols = st.columns(len(acc_rows))
            for i, row in enumerate(acc_rows):
                acc_id, acc_name, balance = row
                color_style = "color: #dc2626;" if balance < 0 else "color: #16a34a;"
                cols[i].markdown(f"""
                    <div style="background-color: #f8fafc; padding: 10px; border-radius: 8px; border: 1px solid #e2e8f0;">
                        <p style="font-size: 13px; color: #64748b; margin-bottom: 2px;">{acc_name}</p>
                        <p style="font-size: 19px; font-weight: 700; {color_style} margin: 0;">{balance:,.2f} ฿</p>
                    </div>
                """, unsafe_allow_html=True)
    except Exception as e:
        st.error(f"ไม่สามารถโหลดข้อมูลบัญชีได้: {e}")

    st.markdown("---")

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("📥 บันทึกยอดเงินโอนเข้า / รับเงินสด")
        with st.form("transfer_form"):
            selected_in_date = st.date_input("เลือกวันที่ทำรายการจริง", value=datetime.now().date(), key="in_date_picker")
            in_category = st.selectbox("หมวดหมู่รายรับ *", options=["-- กรุณาเลือกหมวดหมู่ --", "ร้านไอศกรีม", "เงินเดือน", "กำไรธุรกิจ", "เงินโอนทั่วไป", "อื่นๆ"], key="in_cat_box")
            in_target_acc = st.selectbox("รับเข้าบัญชี / ช่องทาง", options=list(acc_options.keys()), format_func=lambda x: acc_options[x], key="in_acc_box")
            amount_str = st.text_input("จำนวนเงิน (บาท)", placeholder="พิมพ์จำนวนเงิน...", key="trans_amt_str")
            note = st.text_input("หมายเหตุ / รายละเอียด", key="trans_note_str")
            if st.form_submit_button("บันทึกรับเงินเข้า"):
                amount = safe_float(amount_str)
                if in_category == "-- กรุณาเลือกหมวดหมู่ --":
                    st.error("❌ กรุณาเลือกหมวดหมู่รายรับก่อนบันทึก!")
                elif amount is None or amount <= 0:
                    st.error("❌ กรุณากรอกจำนวนเงินเป็นตัวเลขที่ถูกต้อง!")
                else:
                    conn = sqlite3.connect('datacenter.db', timeout=15)
                    cursor = conn.cursor()
                    current_time_str = datetime.now().strftime('%H:%M:%S')
                    timestamp = f"{selected_in_date.strftime('%Y-%m-%d')} {current_time_str}"
                    cursor.execute("UPDATE accounts SET balance = balance + ? WHERE account_id = ?", (amount, in_target_acc))
                    cursor.execute("INSERT INTO transactions (timestamp, type, amount, category, note, account_id) VALUES (?, 'TRANSFER_IN', ?, ?, ?, ?)", (timestamp, amount, in_category, note.strip(), in_target_acc))
                    conn.commit()
                    conn.close()
                    st.success("✅ บันทึกรายรับเรียบร้อย!")
                    st.rerun()

        st.write("")
        st.subheader("⚙️ สถานะระบบตัดรอบแบ่งยอดอัตโนมัติ")
        st.success("🤖 **สถานะ:** เปิดใช้งานอัตโนมัติ\n\n📌 **รายละเอียดการหัก/แบ่งยอดอัตโนมัติ (รอบวันที่ 25):**\n• เมื่อถึงรอบวันที่ 25 ระบบจะดึงยอดรายรับจาก **กสิกรไทย 1 (บัญชีรวม)** มาแบ่งเข้าบัญชีเป้าหมายตามสัดส่วน:\n  - ☀️ **กสิกรไทย 2 (กองทุนโซล่า):** 70%\n  - 🛡️ **กรุงไทย 1 (สำรองฉุกเฉิน):** 15%\n  - 📈 **กรุงไทย 2 (ลงทุน):** 15%")

    with col_right:
        st.subheader("💸 บันทึกการใช้จ่ายทั่วไป / จ่ายเงินสด")
        with st.form("expense_form"):
            selected_exp_date = st.date_input("เลือกวันที่ทำรายการจริง", value=datetime.now().date(), key="exp_date_picker")
            exp_category = st.selectbox("หมวดหมู่รายจ่าย *", options=["-- กรุณาเลือกหมวดหมู่ --", "ร้านไอศกรีม", "Shopping", "ค่าใช้จ่ายส่วนตัว", "อาหาร", "Utilities", "ลงทุน", "อื่นๆ"], key="exp_cat_box")
            exp_acc = st.selectbox("จ่ายจากช่องทาง", options=list(acc_options.keys()), format_func=lambda x: acc_options[x])
            exp_amount_str = st.text_input("จำนวนเงิน (บาท)", placeholder="พิมพ์จำนวนเงิน...", key="exp_amt_str")
            exp_note = st.text_input("รายละเอียดการจ่าย", key="exp_note_str")
            if st.form_submit_button("บันทึกการใช้จ่าย"):
                exp_amount = safe_float(exp_amount_str)
                if exp_category == "-- กรุณาเลือกหมวดหมู่ --":
                    st.error("❌ กรุณาเลือกหมวดหมู่รายจ่ายก่อนบันทึก!")
                elif exp_amount is None or exp_amount <= 0:
                    st.error("❌ กรุณากรอกจำนวนเงินเป็นตัวเลขที่ถูกต้อง!")
                else:
                    conn = sqlite3.connect('datacenter.db', timeout=15)
                    cursor = conn.cursor()
                    current_time_str = datetime.now().strftime('%H:%M:%S')
                    timestamp = f"{selected_exp_date.strftime('%Y-%m-%d')} {current_time_str}"
                    cursor.execute("UPDATE accounts SET balance = balance - ? WHERE account_id = ?", (exp_amount, exp_acc))
                    cursor.execute("INSERT INTO transactions (timestamp, type, amount, category, note, account_id) VALUES (?, 'EXPENSE', ?, ?, ?, ?)", (timestamp, exp_amount, exp_category, exp_note.strip(), exp_acc))
                    conn.commit()
                    conn.close()
                    st.success("✅ บันทึกการจ่ายเรียบร้อย!")
                    st.rerun()

        st.write("")
        with st.expander("🛠️ แก้ไขยอดเงินในแต่ละบัญชี / เงินสดโดยตรง"):
            with st.form("direct_edit_account_form"):
                edit_acc_id = st.selectbox("เลือกบัญชี / เงินสด ที่ต้องการแก้ไข", options=list(acc_options.keys()), format_func=lambda x: acc_options[x], key="d_edit_acc")
                new_exact_balance_str = st.text_input("ระบุยอดเงินใหม่ที่ต้องการตั้งค่า (บาท)", placeholder="พิมพ์ยอดเงินใหม่...", key="dir_edit_bal_str")
                edit_note = st.text_input("หมายเหตุการแก้ไขยอด", value="ปรับยอดบัญชี/เงินสดตรง")

                if st.form_submit_button("💾 บันทึกยอดเงินใหม่นี้ทันที"):
                    new_exact_balance = safe_float(new_exact_balance_str)
                    if new_exact_balance is None or new_exact_balance < 0:
                        st.error("❌ กรุณาระบุยอดเงินใหม่ให้ถูกต้อง!")
                    else:
                        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        conn = sqlite3.connect('datacenter.db', timeout=15)
                        cursor = conn.cursor()
                        cursor.execute("UPDATE accounts SET balance = ? WHERE account_id = ?", (new_exact_balance, edit_acc_id))
                        cursor.execute("INSERT INTO transactions (timestamp, type, amount, category, note, account_id) VALUES (?, 'EDIT_BALANCE', ?, 'ปรับยอด', ?, ?)",
                                       (timestamp, new_exact_balance, f"แก้ไขยอดตรง: {edit_note}", edit_acc_id))
                        conn.commit()
                        conn.close()
                        st.success(f"✅ อัปเดตยอดเรียบร้อย!")
                        st.rerun()

    st.markdown("---")

    current_ym = datetime.now().strftime('%Y-%m')
    try:
        conn = sqlite3.connect('datacenter.db', timeout=15)
        df_months_db = pd.read_sql_query("SELECT DISTINCT year_month FROM fixed_cost_monthly ORDER BY year_month DESC", conn)
        df_fixed_raw = pd.read_sql_query("SELECT id, name, cost_type, promo_amount, normal_amount, promo_months, start_date, due_day, category, note, account_id, deduct_mode FROM fixed_costs", conn)
        conn.close()
    except Exception:
        df_months_db = pd.DataFrame()
        df_fixed_raw = pd.DataFrame()

    available_months = [current_ym]
    if not df_months_db.empty:
        for ym in df_months_db['year_month']:
            if ym not in available_months:
                available_months.append(ym)
    available_months = sorted(list(set(available_months)), reverse=True)
    default_month_index = available_months.index(current_ym) if current_ym in available_months else 0

    st.subheader("📌 รายการค่าใช้จ่ายประจำ / บิลผันแปร (Fixed Cost)")

    col_v1, col_v2 = st.columns([2, 2])
    selected_view_ym = col_v1.selectbox("📅 เลือกเดือนที่ต้องการตรวจสอบรายการ", options=available_months, index=default_month_index, key="view_ym_select")

    if not df_fixed_raw.empty:
        current_display_list = []
        try:
            sel_year, sel_month = map(int, selected_view_ym.split('-'))
        except Exception:
            sel_year, sel_month = datetime.now().year, datetime.now().month

        for _, row in df_fixed_raw.iterrows():
            c_type = row['cost_type'] if pd.notna(row['cost_type']) else 'NORMAL'
            active_amount = 0.0
            price_status = ""
            acc_display = acc_options.get(row['account_id'], 'ไม่ระบุบัญชี')
            d_mode = row.get('deduct_mode', '25')
            mode_display = "⚡ หักตามวันที่กำหนด" if d_mode == 'EXACT_DAY' else "📅 หักรอบวันที่ 25"

            if c_type == 'PROMO':
                try:
                    start_dt = datetime.strptime(row['start_date'], '%Y-%m-%d')
                    diff_months = (sel_year - start_dt.year) * 12 + (sel_month - start_dt.month)
                    p_months = int(row['promo_months']) if pd.notna(row['promo_months']) else 3
                except Exception:
                    diff_months = 0
                    p_months = 3

                if 0 <= diff_months < p_months:
                    active_amount = row['promo_amount']
                    price_status = f"🔥 (โปรโมชั่นเดือนที่ {diff_months + 1}/{p_months})"
                else:
                    active_amount = row['normal_amount']
                    price_status = "📌 (ราคาปกติ)"
            elif c_type == 'CUSTOM':
                try:
                    conn = sqlite3.connect('datacenter.db', timeout=15)
                    c = conn.cursor()
                    c.execute("SELECT amount FROM fixed_cost_monthly WHERE fixed_cost_id = ? AND year_month = ?", (row['id'], selected_view_ym))
                    res = c.fetchone()
                    conn.close()
                    active_amount = res[0] if res else 0.0
                except Exception:
                    active_amount = 0.0
                price_status = f"📊 (กำหนดเองเดือน {selected_view_ym})"
            else:
                active_amount = row['normal_amount']
                price_status = "📌 (รายจ่ายประจำถาวร)"

            current_display_list.append({
                'id': row['id'],
                'รายการ': f"{row['name']} {price_status}",
                'จำนวนเงิน (เดือนที่เลือก)': active_amount,
                'ตัดจากช่องทาง': acc_display,
                'วันที่ต้องจ่าย': row['due_day'],
                'รูปแบบการหัก': mode_display,
                'หมวดหมู่': row['category'],
                'หมายเหตุ': row['note']
            })

        df_fixed = pd.DataFrame(current_display_list)
        st.dataframe(df_fixed[['รายการ', 'จำนวนเงิน (เดือนที่เลือก)', 'ตัดจากช่องทาง', 'วันที่ต้องจ่าย', 'รูปแบบการหัก', 'หมวดหมู่', 'หมายเหตุ']], use_container_width=True, hide_index=True)
    else:
        st.info("ยังไม่มีรายการค่าใช้จ่ายประจำ")

    with st.expander("➕ เพิ่มรายการ Fixed Cost / บิลผันแปรใหม่"):
        tab1, tab2, tab3 = st.tabs(["📌 ปกติถาวร", "🔥 มีโปรโมชั่น (+VAT)", "📊 กำหนดรายเดือนอิสระ (Shopee/บิลผันแปร)"])
        
        with tab1:
            with st.form("form_normal"):
                st.markdown("### ตั้งค่ารายการรายจ่ายประจำปกติถาวร")
                n_name = st.text_input("ชื่อรายการ", key="n_name")
                n_amount_str = st.text_input("จำนวนเงินต่อเดือน (บาท)", placeholder="พิมพ์จำนวนเงิน...", key="n_amount_str")
                n_acc = st.selectbox("ตัดเงินจากช่องทาง", options=list(acc_options.keys()), format_func=lambda x: acc_options[x], key="n_acc")
                n_start = st.date_input("วันที่เริ่มรายการ", value=datetime.now(), key="n_start")
                n_day = st.number_input("วันที่ต้องชำระประจำ (1-31)", min_value=1, max_value=31, value=25, key="n_day")
                n_deduct_mode = st.selectbox("รูปแบบการตัดเงินอัตโนมัติ", options=['25', 'EXACT_DAY'], format_func=lambda x: "📅 หักรอบรวมวันที่ 25 ของเดือน" if x == '25' else f"⚡ หักเงินตามวันที่กำหนดจริง (วันที่ {n_day} ของทุกเดือน)", key="n_deduct_mode")
                n_cat = st.selectbox("หมวดหมู่", ["Family", "Software", "Business", "Utilities", "Shopping", "Other"], key="n_cat")
                n_note = st.text_input("หมายเหตุเพิ่มเติม", key="n_note")
                
                if st.form_submit_button("💾 บันทึกรายการ"):
                    n_amount = safe_float(n_amount_str)
                    if not n_name.strip() or n_amount is None or n_amount <= 0:
                        st.error("❌ กรุณากรอกชื่อรายการและจำนวนเงินเป็นตัวเลขที่ถูกต้อง!")
                    else:
                        start_str = n_start.strftime('%Y-%m-%d')
                        conn = sqlite3.connect('datacenter.db', timeout=15)
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO fixed_costs (name, cost_type, promo_amount, normal_amount, promo_months, start_date, due_day, category, note, account_id, status, deduct_mode) 
                            VALUES (?, 'NORMAL', 0.0, ?, 0, ?, ?, ?, ?, ?, 'Active', ?)
                        """, (n_name, n_amount, start_str, n_day, n_cat, n_note, n_acc, n_deduct_mode))
                        
                        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        cursor.execute("INSERT INTO transactions (timestamp, type, amount, category, note, account_id) VALUES (?, 'EXPENSE', ?, 'Fixed Cost', ?, ?)",
                                       (timestamp, n_amount, f"เพิ่ม Fixed Cost: {n_name}", n_acc))
                        
                        conn.commit()
                        conn.close()
                        st.success("✅ บันทึกรายการประจำเรียบร้อย!")
                        st.rerun()

        with tab2:
            with st.form("form_promo"):
                st.markdown("### ตั้งค่ารายการแบบมีโปรโมชั่น (เลือกอัตรา VAT ได้)")
                p_name = st.text_input("ชื่อรายการ", key="p_name")
                col1, col2, col3 = st.columns(3)
                p_promo_str = col1.text_input("ราคาช่วงโปรโมชั่น (บาท)", placeholder="ราคาโปร...", key="p_promo_str")
                p_normal_str = col2.text_input("ราคาปกติหลังหมดโปร (บาท)", placeholder="ราคาปกติ...", key="p_normal_str")
                p_months = col3.number_input("ใช้ราคาโปรโมชั่นกี่เดือน?", min_value=1, max_value=60, value=3)
                
                col_v1, col_v2 = st.columns(2)
                has_vat = col_v1.checkbox("💡 รวม VAT เพิ่มเติมอัตโนมัติ", value=False, key="has_vat_chk")
                vat_rate = col_v2.selectbox("เลือกอัตรา VAT", options=[7.0, 10.0, 0.0], format_func=lambda x: f"{int(x)}%" if x > 0 else "ไม่บวก VAT", key="vat_rate_box")

                p_acc = st.selectbox("ตัดเงินจากช่องทาง", options=list(acc_options.keys()), format_func=lambda x: acc_options[x], key="p_acc")
                p_start = st.date_input("วันที่เริ่มรายการ", value=datetime.now(), key="p_start")
                p_day = st.number_input("วันที่ต้องชำระประจำ (1-31)", min_value=1, max_value=31, value=25, key="p_day")
                p_deduct_mode = st.selectbox("รูปแบบการตัดเงินอัตโนมัติ", options=['25', 'EXACT_DAY'], format_func=lambda x: "📅 หักรอบรวมวันที่ 25 ของเดือน" if x == '25' else f"⚡ หักเงินตามวันที่กำหนดจริง (วันที่ {p_day} ของทุกเดือน)", key="p_deduct_mode")
                p_cat = st.selectbox("หมวดหมู่", ["Family", "Software", "Business", "Utilities", "Shopping", "Other"], key="p_cat_p")
                p_note = st.text_input("หมายเหตุเพิ่มเติม", key="p_note_p")
                
                if st.form_submit_button("💾 บันทึกโปรโมชั่น"):
                    p_promo = safe_float(p_promo_str)
                    p_normal = safe_float(p_normal_str)
                    if not p_name.strip() or p_promo is None or p_normal is None:
                        st.error("❌ กรุณากรอกข้อมูลและราคาให้ถูกต้องครบถ้วน!")
                    else:
                        final_promo = p_promo * (1 + vat_rate / 100.0) if has_vat else p_promo
                        final_normal = p_normal * (1 + vat_rate / 100.0) if has_vat else p_normal

                        start_str = p_start.strftime('%Y-%m-%d')
                        conn = sqlite3.connect('datacenter.db', timeout=15)
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO fixed_costs (name, cost_type, promo_amount, normal_amount, promo_months, start_date, due_day, category, note, account_id, status, deduct_mode) 
                            VALUES (?, 'PROMO', ?, ?, ?, ?, ?, ?, ?, ?, 'Active', ?)
                        """, (p_name, final_promo, final_normal, p_months, start_str, p_day, p_cat, f"{p_note} (รวม VAT {int(vat_rate)}%)" if has_vat else p_note, p_acc, p_deduct_mode))
                        
                        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        cursor.execute("INSERT INTO transactions (timestamp, type, amount, category, note, account_id) VALUES (?, 'EXPENSE', ?, 'Fixed Cost', ?, ?)",
                                       (timestamp, final_promo, f"เพิ่ม Fixed Cost (โปรโมชั่น): {p_name}", p_acc))

                        conn.commit()
                        conn.close()
                        st.success(f"✅ บันทึกโปรโมชั่นเรียบร้อย!")
                        st.rerun()

        with tab3:
            with st.form("form_custom"):
                st.markdown("### ตั้งค่ารายการแบบยอดไม่เท่ากันในแต่ละเดือน (เช่น Shopee, ค่าไฟ)")
                c_name = st.text_input("ชื่อรายการ (เช่น Shopee / ค่าใช้จ่ายออนไลน์)", key="c_name")
                c_ym = st.text_input("ระบุเดือน-ปีที่ต้องการบันทึกยอด (รูปแบบ YYYY-MM เช่น 2026-10)", value=datetime.now().strftime('%Y-%m'), key="c_ym_text")
                c_amount_str = st.text_input("ยอดเงินของเดือนนี้ (บาท)", placeholder="พิมพ์ยอดเงิน...", key="c_amount_str")
                c_acc = st.selectbox("ตัดเงินจากช่องทาง", options=list(acc_options.keys()), format_func=lambda x: acc_options[x], key="c_acc")
                c_day = st.number_input("วันที่ต้องชำระประจำเดือน (1-31)", min_value=1, max_value=31, value=25, key="c_day")
                c_deduct_mode = st.selectbox("รูปแบบการตัดเงินอัตโนมัติ", options=['25', 'EXACT_DAY'], format_func=lambda x: "📅 หักรอบรวมวันที่ 25 ของเดือน" if x == '25' else f"⚡ หักเงินตามวันที่กำหนดจริง (วันที่ {c_day} ของทุกเดือน)", key="c_deduct_mode")
                c_cat = st.selectbox("หมวดหมู่", ["Shopping", "Utilities", "Other"], key="c_cat_c")
                c_note = st.text_input("หมายเหตุเพิ่มเติม", key="c_note_c")
                
                if st.form_submit_button("💾 บันทึกยอดเงิน"):
                    c_amount = safe_float(c_amount_str)
                    if not c_name.strip() or c_amount is None or c_amount <= 0 or not c_ym.strip():
                        st.error("❌ กรุณากรอกชื่อรายการ จำนวนเงิน และรูปแบบเดือนให้ถูกต้อง!")
                    else:
                        conn = sqlite3.connect('datacenter.db', timeout=15)
                        cursor = conn.cursor()
                        cursor.execute("SELECT id FROM fixed_costs WHERE name = ? AND cost_type = 'CUSTOM'", (c_name,))
                        existing = cursor.fetchone()
                        
                        if existing:
                            fc_id = existing[0]
                            cursor.execute("UPDATE fixed_costs SET account_id = ?, deduct_mode = ? WHERE id = ?", (c_acc, c_deduct_mode, fc_id))
                        else:
                            cursor.execute("""
                                INSERT INTO fixed_costs (name, cost_type, promo_amount, normal_amount, promo_months, start_date, due_day, category, note, account_id, status, deduct_mode) 
                                VALUES (?, 'CUSTOM', 0.0, 0.0, 0, ?, ?, ?, ?, ?, 'Active', ?)
                            """, (c_name, datetime.now().strftime('%Y-%m-%d'), c_day, c_cat, c_note, c_acc, c_deduct_mode))
                            fc_id = cursor.lastrowid

                        cursor.execute("""
                            INSERT OR REPLACE INTO fixed_cost_monthly (fixed_cost_id, year_month, amount) 
                            VALUES (?, ?, ?)
                        """, (fc_id, c_ym.strip(), c_amount))
                        
                        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        cursor.execute("INSERT INTO transactions (timestamp, type, amount, category, note, account_id) VALUES (?, 'EXPENSE', ?, 'Fixed Cost', ?, ?)",
                                       (timestamp, c_amount, f"เพิ่ม Fixed Cost รายเดือน ({c_ym.strip()}): {c_name}", c_acc))

                        conn.commit()
                        conn.close()
                        st.success(f"✅ บันทึกยอดเดือน {c_ym.strip()} เรียบร้อย!")
                        st.rerun()

    st.markdown("---")
    st.subheader("📊 รายงานสรุปยอดจ่าย Fixed Cost และรายจ่ายประจำเดือน")
    st.info("เลือกเดือนที่ต้องการเพื่อดูรายการค่าใช้จ่ายประจำและรายจ่ายทั่วไปที่เกิดขึ้นในเดือนนั้น ๆ")

    report_ym = st.selectbox("📅 เลือกเดือนที่ต้องการออกรายงานสรุป", options=available_months, index=default_month_index, key="report_ym_select")
    
    if not df_fixed_raw.empty:
        report_list = []
        total_report_amount = 0.0
        
        try:
            rep_year, rep_month = map(int, report_ym.split('-'))
        except Exception:
            rep_year, rep_month = datetime.now().year, datetime.now().month

        for _, row in df_fixed_raw.iterrows():
            c_type = row['cost_type'] if pd.notna(row['cost_type']) else 'NORMAL'
            item_amt = 0.0
            acc_name = acc_options.get(row['account_id'], 'ไม่ระบุบัญชี')

            if c_type == 'PROMO':
                try:
                    start_dt = datetime.strptime(row['start_date'], '%Y-%m-%d')
                    diff_months = (rep_year - start_dt.year) * 12 + (rep_month - start_dt.month)
                    p_months = int(row['promo_months']) if pd.notna(row['promo_months']) else 3
                except Exception:
                    diff_months = 0
                    p_months = 3
                item_amt = row['promo_amount'] if 0 <= diff_months < p_months else row['normal_amount']
            elif c_type == 'CUSTOM':
                try:
                    conn = sqlite3.connect('datacenter.db', timeout=15)
                    c = conn.cursor()
                    c.execute("SELECT amount FROM fixed_cost_monthly WHERE fixed_cost_id = ? AND year_month = ?", (row['id'], report_ym))
                    res = c.fetchone()
                    conn.close()
                    item_amt = res[0] if res else 0.0
                except Exception:
                    item_amt = 0.0
            else:
                item_amt = row['normal_amount']

            if item_amt > 0:
                total_report_amount += item_amt
                report_list.append({
                    'รายการ': row['name'],
                    'หมวดหมู่': row['category'],
                    'ตัดจากช่องทาง': acc_name,
                    'วันที่ต้องชำระ': row['due_day'],
                    'จำนวนเงิน (บาท)': item_amt
                })

        st.markdown("#### 📌 รายการค่าใช้จ่ายประจำ (Fixed Cost)")
        if report_list:
            df_report = pd.DataFrame(report_list)
            st.dataframe(df_report[['รายการ', 'หมวดหมู่', 'ตัดจากช่องทาง', 'วันที่ต้องชำระ', 'จำนวนเงิน (บาท)']], use_container_width=True, hide_index=True)
            st.markdown(f"💰 **ยอดรวม Fixed Cost ของเดือน {report_ym}:** <span style='color: #16a34a; font-weight: 700; font-size: 18px;'>{total_report_amount:,.2f} บาท</span>", unsafe_allow_html=True)
        else:
            st.warning(f"⚠️ ไม่มีข้อมูล Fixed Cost ในเดือน {report_ym}")
    else:
        total_report_amount = 0.0
        st.info("ยังไม่มีข้อมูล Fixed Cost ในระบบ")

    st.markdown("---")

    try:
        conn = sqlite3.connect('datacenter.db', timeout=15)
        df_all_tx = pd.read_sql_query("""
            SELECT 
                id,
                timestamp AS 'วัน-เวลา',
                type,
                amount AS 'จำนวนเงิน (บาท)',
                COALESCE(category, 'อื่นๆ') AS 'หมวดหมู่',
                COALESCE(note, '') AS 'หมายเหตุ',
                account_id
            FROM transactions 
            WHERE type IN ('TRANSFER_IN', 'EXPENSE') AND note NOT LIKE '%Fixed Cost%' AND note NOT LIKE '%ให้%ยืมเงิน%'
            ORDER BY id DESC
        """, conn)
        conn.close()
    except Exception:
        df_all_tx = pd.DataFrame()

    total_general_income = 0.0
    total_general_expense = 0.0
    income_list = []
    expense_list = []

    if not df_all_tx.empty:
        for _, row in df_all_tx.iterrows():
            t_str = str(row['วัน-เวลา'])
            if t_str.startswith(report_ym):
                amt = row['จำนวนเงิน (บาท)']
                item_data = {
                    'id': row['id'],
                    'วัน-เวลา': t_str,
                    'หมวดหมู่': row['หมวดหมู่'],
                    'รายการ / หมายเหตุ': row['หมายเหตุ'],
                    'ช่องทาง': acc_options.get(row['account_id'], 'ไม่ระบุบัญชี'),
                    'จำนวนเงิน (บาท)': amt
                }
                if row['type'] == 'TRANSFER_IN':
                    total_general_income += amt
                    income_list.append(item_data)
                elif row['type'] == 'EXPENSE':
                    total_general_expense += amt
                    expense_list.append(item_data)

    st.markdown(f"### 📑 รายรับ - รายจ่ายทั่วไป ประจำเดือน {report_ym}")
    
    all_categories = sorted(list(set([x['หมวดหมู่'] for x in income_list + expense_list])))
    selected_cat_filter = st.selectbox("🔍 กรองดูเฉพาะหมวดหมู่ (เลือก 'ทั้งหมด' เพื่อดูภาพรวม)", options=["ทั้งหมด"] + all_categories, key="cat_filter_box")

    col_in, col_exp = st.columns(2)
    
    with col_in:
        st.markdown("#### 📥 รายรับทั่วไป")
        filtered_in = [x for x in income_list if selected_cat_filter == "ทั้งหมด" or x['หมวดหมู่'] == selected_cat_filter]
        if filtered_in:
            df_in = pd.DataFrame(filtered_in)
            st.dataframe(df_in[['วัน-เวลา', 'หมวดหมู่', 'รายการ / หมายเหตุ', 'ช่องทาง', 'จำนวนเงิน (บาท)']], use_container_width=True, hide_index=True)
            st.markdown(f"📥 **ยอดรวมรายรับ ({selected_cat_filter}):** <span style='color: #16a34a; font-weight: 700; font-size: 18px;'>{sum([x['จำนวนเงิน (บาท)'] for x in filtered_in]):,.2f} บาท</span>", unsafe_allow_html=True)
        else:
            st.info("ไม่มีรายการรายรับ")

    with col_exp:
        st.markdown("#### 💸 รายจ่ายทั่วไป")
        filtered_exp = [x for x in expense_list if selected_cat_filter == "ทั้งหมด" or x['หมวดหมู่'] == selected_cat_filter]
        if filtered_exp:
            df_exp = pd.DataFrame(filtered_exp)
            st.dataframe(df_exp[['วัน-เวลา', 'หมวดหมู่', 'รายการ / หมายเหตุ', 'ช่องทาง', 'จำนวนเงิน (บาท)']], use_container_width=True, hide_index=True)
            st.markdown(f"💸 **ยอดรวมรายจ่าย ({selected_cat_filter}):** <span style='color: #dc2626; font-weight: 700; font-size: 18px;'>{sum([x['จำนวนเงิน (บาท)'] for x in filtered_exp]):,.2f} บาท</span>", unsafe_allow_html=True)
        else:
            st.info("ไม่มีรายการรายจ่าย")

    st.markdown("---")
    
    net_total = total_general_income - (total_report_amount + total_general_expense)
    net_color = "#dc2626" if net_total < 0 else "#16a34a"

    st.markdown(f"### 🌟 **สรุปยอดสุทธิเดือน {report_ym}:**")
    st.markdown(f"• **รายรับรวม:** <span style='color: #16a34a; font-weight: 700;'>{total_general_income:,.2f} บาท</span>", unsafe_allow_html=True)
    st.markdown(f"• **รายจ่ายรวม (Fixed Cost + รายจ่ายทั่วไป):** <span style='color: #dc2626; font-weight: 700;'>{total_report_amount + total_general_expense:,.2f} บาท</span> (Fixed Cost: {total_report_amount:,.2f} + ทั่วไป: {total_general_expense:,.2f})", unsafe_allow_html=True)
    st.markdown(f"• **คงเหลือสุทธิ (รายรับ - รายจ่ายทั้งหมด):** <span style='color: {net_color}; font-weight: 700; font-size: 18px;'>{net_total:,.2f} บาท</span>", unsafe_allow_html=True)

    if not df_all_tx.empty:
        with st.expander("🛠️ แก้ไขหมวดหมู่ หรือ รายละเอียด (หมายเหตุ) รายการย้อนหลัง"):
            st.info("เลือกรายการที่ต้องการแก้ไขหมวดหมู่หรือหมายเหตุ (ตัวเลขยอดเงินและช่องทางจะไม่เปลี่ยนแปลง)")
            
            edit_options_dict = {f"ID: {row['id']} | [{row['วัน-เวลา']}] {'📥 รับ' if row['type']=='TRANSFER_IN' else '💸 จ่าย'} ({row['หมวดหมู่']}) - {row['จำนวนเงิน (บาท)']:,.2f} ฿ (หมายเหตุ: {row['หมายเหตุ']})": row['id'] for _, row in df_all_tx.iterrows()}
            selected_edit_opt = st.selectbox("เลือกรายการที่ต้องการแก้ไข", options=list(edit_options_dict.keys()), key="select_edit_tx_box")
            target_edit_id = edit_options_dict[selected_edit_opt]

            conn = sqlite3.connect('datacenter.db', timeout=15)
            c = conn.cursor()
            c.execute("SELECT type, category, note FROM transactions WHERE id = ?", (target_edit_id,))
            current_tx_data = c.fetchone()
            conn.close()

            if current_tx_data:
                curr_type, curr_cat, curr_note = current_tx_data
                
                if curr_type == 'TRANSFER_IN':
                    cat_choices = ["ร้านไอศกรีม", "เงินเดือน", "กำไรธุรกิจ", "เงินโอนทั่วไป", "อื่นๆ"]
                else:
                    cat_choices = ["ร้านไอศกรีม", "Shopping", "ค่าใช้จ่ายส่วนตัว", "อาหาร", "Utilities", "ลงทุน", "อื่นๆ"]

                try:
                    default_cat_idx = cat_choices.index(curr_cat)
                except:
                    default_cat_idx = 0

                with st.form("edit_tx_form"):
                    new_cat = st.selectbox("แก้ไขหมวดหมู่", options=cat_choices, index=default_cat_idx, key="edit_cat_sel")
                    new_note = st.text_input("แก้ไขรายละเอียด / หมายเหตุ", value=curr_note if curr_note else "", key="edit_note_input")

                    if st.form_submit_button("💾 บันทึกการแก้ไข"):
                        conn = sqlite3.connect('datacenter.db', timeout=15)
                        c = conn.cursor()
                        c.execute("UPDATE transactions SET category = ?, note = ? WHERE id = ?", (new_cat, new_note.strip(), target_edit_id))
                        conn.commit()
                        conn.close()
                        st.success("✅ แก้ไขข้อมูลเรียบร้อยแล้ว!")
                        st.rerun()

    if not df_fixed_raw.empty:
        with st.expander("🛠️ จัดการ / อัปเดตยอดรายเดือน & ลบรายการ Fixed Cost"):
            options_dict = {f"[{row['cost_type']}] {row['name']}": row['id'] for _, row in df_fixed_raw.iterrows()}
            selected_option = st.selectbox("เลือกรายการที่ต้องการจัดการ", options=list(options_dict.keys()), key="select_del_fc_box")
            target_id = options_dict[selected_option]

            conn = sqlite3.connect('datacenter.db', timeout=15)
            c = conn.cursor()
            c.execute("SELECT cost_type, name FROM fixed_costs WHERE id = ?", (target_id,))
            t_data = c.fetchone()
            conn.close()

            if t_data and t_data[0] == 'CUSTOM':
                st.markdown("#### ✏️ อัปเดตยอดเงินสำหรับเดือนอื่นๆ ของรายการนี้")
                with st.form("update_custom_month"):
                    up_ym = st.text_input("ระบุเดือน-ปีที่ต้องการอัปเดต (YYYY-MM)", value=datetime.now().strftime('%Y-%m'), key="up_ym_text")
                    up_amt_str = st.text_input("จำนวนเงินใหม่ (บาท)", placeholder="พิมพ์ยอดเงินใหม่...", key="up_amt_str")
                    up_acc = st.selectbox("ตัดเงินจากช่องทาง", options=list(acc_options.keys()), format_func=lambda x: acc_options[x], key="up_acc_box")
                    if st.form_submit_button("💾 บันทึกยอดเดือนนี้"):
                        up_amt = safe_float(up_amt_str)
                        if up_amt is None or up_amt <= 0 or not up_ym.strip():
                            st.error("❌ กรุณากรอกจำนวนเงินและเดือนให้ถูกต้อง!")
                        else:
                            conn = sqlite3.connect('datacenter.db', timeout=15)
                            conn.execute("INSERT OR REPLACE INTO fixed_cost_monthly (fixed_cost_id, year_month, amount) VALUES (?, ?, ?)", (target_id, up_ym.strip(), up_amt))
                            conn.execute("UPDATE fixed_costs SET account_id = ? WHERE id = ?", (up_acc, target_id))
                            conn.commit()
                            conn.close()
                            st.success(f"✅ อัปเดตยอดเดือน {up_ym.strip()} สำเร็จ!")
                            st.rerun()

            st.write("---")
            if st.button("🗑️ ลบรายการนี้ทิ้งถาวร (รวมถึงประวัติธุรกรรมที่เกี่ยวข้อง)", type="primary", key="btn_del_fc"):
                del_conn = sqlite3.connect('datacenter.db', timeout=15)
                del_cursor = del_conn.cursor()
                
                del_cursor.execute("SELECT name FROM fixed_costs WHERE id = ?", (target_id,))
                fc_row = del_cursor.fetchone()
                if fc_row:
                    fc_name = fc_row[0]
                    del_cursor.execute("DELETE FROM transactions WHERE note LIKE ?", (f"%Fixed Cost%: {fc_name}%",))

                del_cursor.execute("DELETE FROM fixed_costs WHERE id = ?", (target_id,))
                del_cursor.execute("DELETE FROM fixed_cost_monthly WHERE fixed_cost_id = ?", (target_id,))
                del_conn.commit()
                del_conn.close()
                st.success("✅ ลบรายการ Fixed Cost และประวัติธุรกรรมที่เกี่ยวข้องเรียบร้อย!")
                st.rerun()

    st.subheader("🤝 ระบบจัดการการกู้ยืมเงิน")
    try:
        conn = sqlite3.connect('datacenter.db', timeout=15)
        df_loans = pd.read_sql_query("""
            SELECT 
                id,
                loan_type,
                person_name AS 'ชื่อคู่กรณี',
                amount AS 'จำนวนเงิน (บาท)',
                account_id,
                due_date AS 'กำหนดคืน',
                note AS 'หมายเหตุ',
                status AS 'สถานะ'
            FROM loans
        """, conn)
        conn.close()
    except Exception:
        df_loans = pd.DataFrame()

    if not df_loans.empty:
        df_loans_display = df_loans.copy()
        df_loans_display['ประเภท'] = df_loans_display['loan_type'].apply(lambda x: '💸 คนอื่นยืมเรา' if x == 'LEND' else '📥 เรายืมคนอื่น')
        df_loans_display['ช่องทาง'] = df_loans_display['account_id'].map(acc_options).fillna(df_loans_display['account_id'])

        def color_amount_col(val):
            return 'color: #ca8a04; font-weight: bold;'

        styled_loans_df = df_loans_display[['id', 'ประเภท', 'ชื่อคู่กรณี', 'จำนวนเงิน (บาท)', 'ช่องทาง', 'กำหนดคืน', 'หมายเหตุ', 'สถานะ']].style.format({
            'จำนวนเงิน (บาท)': '{:,.2f} ฿'
        }).map(color_amount_col, subset=['จำนวนเงิน (บาท)'])

        st.dataframe(styled_loans_df, use_container_width=True, hide_index=True)
        st.write("")
        
        with st.expander("🛠️ จัดการสถานะ / ลบรายการกู้ยืม"):
            loan_options_dict = {f"ID: {row['id']} | [{row['loan_type']}] {row['ชื่อคู่กรณี']} - {row['จำนวนเงิน (บาท)']:,.2f} ฿": row['id'] for _, row in df_loans.iterrows()}
            selected_loan_opt = st.selectbox("เลือกรายการกู้ยืม", options=list(loan_options_dict.keys()), key="select_loan_box")
            selected_loan_id = loan_options_dict[selected_loan_opt]

            col_l1, col_l2 = st.columns(2)
            if col_l1.button("✅ เปลี่ยนสถานะเป็น 'ชำระคืนแล้ว'", key="btn_status_loan"):
                l_conn = sqlite3.connect('datacenter.db', timeout=15)
                l_cur = l_conn.cursor()
                l_cur.execute("UPDATE loans SET status = 'Completed' WHERE id = ?", (selected_loan_id,))
                l_conn.commit()
                l_conn.close()
                st.success("อัปเดตสถานะสำเร็จ!")
                st.rerun()

            if col_l2.button("🗑️ ลบรายการนี้ทิ้งถาวร", type="primary", key="btn_del_loan"):
                l_conn = sqlite3.connect('datacenter.db', timeout=15)
                l_cur = l_conn.cursor()
                
                l_cur.execute("SELECT loan_type, person_name, amount, account_id FROM loans WHERE id = ?", (selected_loan_id,))
                loan_row = l_cur.fetchone()
                
                if loan_row:
                    l_type = loan_row[0]
                    p_name = loan_row[1]
                    l_amt = loan_row[2]
                    l_acc = loan_row[3]
                    
                    if l_type == 'LEND':
                        l_cur.execute("UPDATE accounts SET balance = balance + ? WHERE account_id = ?", (l_amt, l_acc))
                        note_keyword = f"ให้ {p_name} ยืมเงิน"
                    elif l_type == 'BORROW':
                        l_cur.execute("UPDATE accounts SET balance = balance - ? WHERE account_id = ?", (l_amt, l_acc))
                        note_keyword = f"กู้ยืมมาจาก {p_name}"
                    
                    l_cur.execute("DELETE FROM transactions WHERE note = ? AND amount = ?", (note_keyword, l_amt))
                
                l_cur.execute("DELETE FROM loans WHERE id = ?", (selected_loan_id,))
                l_conn.commit()
                l_conn.close()
                st.success("✅ ลบรายการ, คืนเงิน และลบประวัติธุรกรรมที่เกี่ยวข้องเรียบร้อย!")
                st.rerun()
    else:
        st.info("ยังไม่มีประวัติการกู้ยืมเงินในระบบ")

    with st.expander("➕ บันทึกรายการกู้ยืมใหม่"):
        with st.form("loan_form"):
            loan_type = st.selectbox("ประเภทรายการ", options=['LEND', 'BORROW'], format_func=lambda x: "💸 ให้คนอื่นยืมเงิน (เงินออกจากช่องทางเรา)" if x == 'LEND' else "📥 เราไปกู้ยืมเงินคนคนอื่น (เงินเข้าช่องทางเรา)")
            person_name = st.text_input("ชื่อคู่กรณี")
            loan_amount_str = st.text_input("จำนวนเงิน (บาท)", placeholder="พิมพ์จำนวนเงิน...", key="loan_amt_str")
            target_acc = st.selectbox("ช่องทาง", options=list(acc_options.keys()), format_func=lambda x: acc_options[x])
            due_date = st.text_input("กำหนดคืน")
            loan_note = st.text_input("หมายเหตุเพิ่มเติม")

            if st.form_submit_button("บันทึกและปรับยอดเงินทันที"):
                loan_amount = safe_float(loan_amount_str)
                if not person_name.strip() or loan_amount is None or loan_amount <= 0:
                    st.error("❌ กรุณากรอกชื่อคู่กรณีและจำนวนเงินเป็นตัวเลขที่ถูกต้อง")
                else:
                    l_conn = sqlite3.connect('datacenter.db', timeout=15)
                    l_cur = l_conn.cursor()
                    l_cur.execute("INSERT INTO loans (loan_type, person_name, amount, account_id, due_date, note, status) VALUES (?, ?, ?, ?, ?, ?, 'Active')",
                                  (loan_type, person_name, loan_amount, target_acc, due_date, loan_note))
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    if loan_type == 'LEND':
                        l_cur.execute("UPDATE accounts SET balance = balance - ? WHERE account_id = ?", (loan_amount, target_acc))
                        l_cur.execute("INSERT INTO transactions (timestamp, type, amount, category, note, account_id) VALUES (?, 'EXPENSE', ?, 'Loan', ?, ?)",
                                      (timestamp, loan_amount, f"ให้ {person_name} ยืมเงิน", target_acc))
                    else:
                        l_cur.execute("UPDATE accounts SET balance = balance + ? WHERE account_id = ?", (loan_amount, target_acc))
                        l_cur.execute("INSERT INTO transactions (timestamp, type, amount, category, note, account_id) VALUES (?, 'TRANSFER_IN', ?, 'Loan', ?, ?)",
                                      (timestamp, loan_amount, f"กู้ยืมมาจาก {person_name}", target_acc))
                    l_conn.commit()
                    l_conn.close()
                    st.success("✅ บันทึกสำเร็จ!")
                    st.rerun()

    st.markdown("---")

    with st.expander("📜 ประวัติการทำรายการล่าสุด (คลิกเพื่อเปิด / ย่อ)", expanded=True):
        try:
            conn = sqlite3.connect('datacenter.db', timeout=15)
            df_tx_raw = pd.read_sql_query("""
                SELECT 
                    id,
                    timestamp AS 'วัน-เวลา',
                    type,
                    amount AS 'จำนวนเงิน (บาท)',
                    COALESCE(category, '-') AS 'หมวดหมู่',
                    COALESCE(note, '') AS 'หมายเหตุ',
                    account_id
                FROM transactions 
                ORDER BY id DESC LIMIT 20
            """, conn)
            conn.close()
        except Exception:
            df_tx_raw = pd.DataFrame()
        
        if not df_tx_raw.empty:
            df_tx_display = df_tx_raw.copy()
            df_tx_display['ประเภท'] = df_tx_display['type'].apply(lambda x: 
                '📥 รับเงินเข้า' if x == 'TRANSFER_IN' else (
                '💸 จ่ายเงินออก' if x == 'EXPENSE' else '✏️ แก้ไขยอด'
            ))
            df_tx_display['ช่องทาง'] = df_tx_display['account_id'].map(acc_options).fillna('ไม่ระบุช่องทาง')

            st.dataframe(df_tx_display[['วัน-เวลา', 'ประเภท', 'ช่องทาง', 'หมวดหมู่', 'จำนวนเงิน (บาท)', 'หมายเหตุ']], use_container_width=True, hide_index=True)
            
            with st.expander("🛠️ ลบประวัติรายการรายรายการ (พร้อมคืนยอดเงินอัตโนมัติ)"):
                tx_options_dict = {f"ID: {row['id']} | [{row['วัน-เวลา']}] {row['type']} ({row['หมวดหมู่']}) - {row['จำนวนเงิน (บาท)']:,.2f} ฿": row['id'] for _, row in df_tx_raw.iterrows()}
                selected_tx_opt = st.selectbox("เลือกประวัติรายการที่ต้องการลบ", options=list(tx_options_dict.keys()), key="select_tx_box")
                selected_tx_id = tx_options_dict[selected_tx_opt]

                if st.button("🗑️ ลบประวัติและคืนยอดเงินอัตโนมัติ", type="primary", key="btn_del_single_tx"):
                    tx_conn = sqlite3.connect('datacenter.db', timeout=15)
                    tx_cur = tx_conn.cursor()
                    
                    tx_cur.execute("SELECT type, amount, account_id FROM transactions WHERE id = ?", (selected_tx_id,))
                    tx_row = tx_cur.fetchone()
                    
                    if tx_row:
                        t_type, t_amt, t_acc = tx_row
                        if t_type == 'TRANSFER_IN' and t_acc:
                            tx_cur.execute("UPDATE accounts SET balance = balance - ? WHERE account_id = ?", (t_amt, t_acc))
                        elif t_type == 'EXPENSE' and t_acc:
                            tx_cur.execute("UPDATE accounts SET balance = balance + ? WHERE account_id = ?", (t_amt, t_acc))
                        
                        tx_cur.execute("DELETE FROM transactions WHERE id = ?", (selected_tx_id,))
                    
                    tx_conn.commit()
                    tx_conn.close()
                    st.success("✅ ลบประวัติและคืนยอดเงินเรียบร้อยแล้ว!")
                    st.rerun()
        else:
            st.info("ยังไม่มีประวัติการทำรายการ")

    st.markdown("---")

    with st.expander("⚙️ ตั้งค่า Telegram & ระบบจัดการรีเซ็ตข้อมูลระบบ"):
        st.info("ตั้งค่า Token, Chat ID หรือจัดการล้างข้อมูลแต่ละหมวดหมู่ได้ที่นี่")
        
        current_token = get_setting('tg_token', '')
        current_chat_id = get_setting('tg_chat_id', '')
        current_mode = get_setting('auto_mode', 'รายวัน')
        current_time = get_setting('auto_time', '08:00')
        current_dow = get_setting('auto_dow', 'วันจันทร์')
        current_dom = int(get_setting('auto_dom', '25'))
        current_enable = get_setting('enable_auto', 'False') == 'True'

        tg_token = st.text_input("Telegram Bot Token", type="password", value=current_token)
        tg_chat_id = st.text_input("Telegram Chat ID", value=current_chat_id)
        
        if st.button("💾 บันทึกค่าตั้งค่า Telegram"):
            save_setting('tg_token', tg_token)
            save_setting('tg_chat_id', tg_chat_id)
            st.success("✅ บันทึกข้อมูล Telegram ลงฐานข้อมูลเรียบร้อย!")

        st.markdown("---")
        st.subheader("⏱️ ตั้งเวลารายงานอัตโนมัติ")
        
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            auto_mode = st.selectbox("เลือกรูปแบบการส่ง", options=["รายวัน", "รายสัปดาห์", "รายเดือน"], index=["รายวัน", "รายสัปดาห์", "รายเดือน"].index(current_mode) if current_mode in ["รายวัน", "รายสัปดาห์", "รายเดือน"] else 0)
            auto_time = st.text_input("เวลารายงาน (รูปแบบ 24 ชม. เช่น 08:00 หรือ 19:30)", value=current_time)

        with col_m2:
            auto_dow = current_dow
            auto_dom = current_dom
            if auto_mode == "รายสัปดาห์":
                days_list = ["วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์"]
                auto_dow = st.selectbox("เลือกวันในสัปดาห์", options=days_list, index=days_list.index(current_dow) if current_dow in days_list else 0)
            elif auto_mode == "รายเดือน":
                auto_dom = st.number_input("เลือกวันที่ต้องการส่งประจำเดือน (1-31)", min_value=1, max_value=31, value=current_dom)

            enable_auto = st.checkbox("🟢 เปิดใช้งานระบบส่งรายงานอัตโนมัติ", value=current_enable)

        save_setting('auto_mode', auto_mode)
        save_setting('auto_time', auto_time)
        save_setting('auto_dow', auto_dow)
        save_setting('auto_dom', str(auto_dom))
        save_setting('enable_auto', str(enable_auto))

        if enable_auto:
            if auto_mode == "รายวัน":
                st.success(f"🟢 เปิดใช้งาน: ส่งทุกวัน เวลา {auto_time} น.")
            elif auto_mode == "รายสัปดาห์":
                st.success(f"🟢 เปิดใช้งาน: ส่งทุก{auto_dow} เวลา {auto_time} น.")
            elif auto_mode == "รายเดือน":
                st.success(f"🟢 เปิดใช้งาน: ส่งทุกวันที่ {auto_dom} ของเดือน เวลา {auto_time} น.")

        st.write("")
        if st.button("📤 ทดลองส่งสรุปยอดการเงินตอนนี้", type="primary"):
            if not tg_token or not tg_chat_id:
                st.error("❌ กรุณากรอก Bot Token และ Chat ID ก่อน!")
            else:
                send_telegram_auto(tg_token, tg_chat_id, "ทดสอบส่ง")
                st.success("✅ ส่งรายงานทดสอบสำเร็จ!")

        st.markdown("---")
        st.subheader("⚠️ โซนจัดการ / รีเซ็ตข้อมูล (อันตราย)")
        st.warning("ระมัดระวังในการกดปุ่มด้านล่าง เนื่องจากข้อมูลที่ถูกลบจะไม่สามารถกู้คืนได้ทันที")

        col_r1, col_r2 = st.columns(2)
        with col_r1:
            if st.button("🔄 รีเซ็ตยอดเงินบัญชี/เงินสดทั้งหมดเป็น 0"):
                conn = sqlite3.connect('datacenter.db', timeout=15)
                conn.execute("UPDATE accounts SET balance = 0.0")
                conn.commit()
                conn.close()
                st.success("✅ รีเซ็ตยอดเงินทุกช่องทางเป็น 0 เรียบร้อย!")
                st.rerun()

            if st.button("🗑️ ล้างประวัติธุรกรรมทั้งหมด"):
                conn = sqlite3.connect('datacenter.db', timeout=15)
                conn.execute("DELETE FROM transactions")
                conn.commit()
                conn.close()
                st.success("✅ ล้างประวัติธุรกรรมทั้งหมดแล้ว!")
                st.rerun()

        with col_r2:
            if st.button("🗑️ ล้างรายการ Fixed Cost ทั้งหมด"):
                conn = sqlite3.connect('datacenter.db', timeout=15)
                conn.execute("DELETE FROM fixed_costs")
                conn.execute("DELETE FROM fixed_cost_monthly")
                conn.commit()
                conn.close()
                st.success("✅ ล้างรายการ Fixed Cost ทั้งหมดแล้ว!")
                st.rerun()

            if st.button("🗑️ ล้างข้อมูลกู้ยืมเงินทั้งหมด"):
                conn = sqlite3.connect('datacenter.db', timeout=15)
                conn.execute("DELETE FROM loans")
                conn.commit()
                conn.close()
                st.success("✅ ล้างข้อมูลการกู้ยืมทั้งหมดแล้ว!")
                st.rerun()

        st.write("")
        if st.button("💥 เคลียร์ข้อมูลการเงินทั้งหมด (Factory Reset)", type="primary"):
            conn = sqlite3.connect('datacenter.db', timeout=15)
            conn.execute("UPDATE accounts SET balance = 0.0")
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM fixed_costs")
            conn.execute("DELETE FROM fixed_cost_monthly")
            conn.execute("DELETE FROM loans")
            conn.commit()
            conn.close()
            st.success("💥 รีเซ็ตระบบการเงินทั้งหมดกลับเป็นค่าเริ่มต้นเรียบร้อย!")
            st.rerun()
elif menu == "📊 สินทรัพย์รวม (Asset Center)":

    if st.button("⬅️ กลับหน้าแรก (Portal Hub)"):
        st.session_state['nav_menu'] = "หน้าแรก (Portal Hub)"
        st.rerun()

    st.markdown("---")

    st.markdown("# 📊 Asset Center")

    st.markdown("ศูนย์รวมทรัพย์สินทั้งหมดของ Saku Data Center")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("### 💰 เงินสด")
        conn = sqlite3.connect("datacenter.db")
        c = conn.cursor()

        c.execute("SELECT balance FROM accounts WHERE account_id='cash'")
        cash_row = c.fetchone()

        conn.close()

        cash_balance = cash_row[0] if cash_row else 0

        st.markdown(f"{cash_balance:,.2f} บาท")

    with col2:
        st.markdown("### 📈 หุ้น")
        st.markdown("0 บาท")

    with col3:
        st.markdown("### ⛏️ Verus")
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}

            verus_address = "REn28U7KUAbvRQTwWwjKYnKcYyiBC1ga7L"
            api_url = f"https://luckpool.net/verus/miner/{verus_address}"

            res = requests.get(api_url, headers=headers, timeout=10)

            verus_value = 0

            if res.status_code == 200:
                data = res.json()

                api_balance = float(data.get('balance', 0.0))

                cg = requests.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=verus-coin&vs_currencies=thb",
                timeout=10
                )

                if cg.status_code == 200:
                    cg_data = cg.json()
                    thb_price = float(cg_data['verus-coin']['thb'])
                    verus_value = api_balance * thb_price
                    st.markdown(f"{verus_value:,.2f} บาท")
                    

        except:
            st.markdown("0 บาท")

elif menu == "⛏️ Verus (Mining Farm)":
    st_autorefresh(interval=60000,key="verus_refresh")
    if st.button("⬅️ กลับหน้าแรก (Portal Hub)", key="back_home_verus"):
        st.session_state['nav_menu'] = "หน้าแรก (Portal Hub)"
        st.rerun()
    st.markdown(
        "<h2>⛏️ ระบบบริหารเหมืองขุด Verus (Mining Farm)</h2>",
        unsafe_allow_html=True
    )
    st.info("📊 ดึงข้อมูลสถิติเรียลไทม์จาก LuckPool API และราคาเหรียญสดจาก CoinGecko มาคำนวณมูลค่าให้อัตโนมัติ")
    conn = sqlite3.connect("datacenter.db")

    df_test = pd.read_sql_query(
    "SELECT COUNT(*) as total FROM vrsc_daily",conn)
    
    conn.close()


    verus_address = "REn28U7KUABvRQTwWwjKYnkCYyiBC1ga7L"
    api_url = f"https://luckpool.net/verus/miner/{verus_address}"

    api_balance = 0.0
    api_paid = 0.0
    total_hashrate = 0.0
    immature = 0.0

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(api_url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            # st.json(data)
            # st.write(data)
            api_balance = float(data.get('balance', 0.0))
            api_paid = float(data.get('paid', 0.0))
            immature = float(data.get('immature', 0.0))
            
            # ดึงค่า hashrate จากฟิลด์หลัก หรือคำนวณจากทุกส่วนที่มี
            total_hashrate = data.get("hashrateString", "0 MH")
            # st.write(f"Hashrate = {total_hashrate}")
            # st.success(f"Hashrate = {total_hashrate}")
            # ถ้าฟิลด์หลักเป็น 0 ให้ลองเช็คจากบรรดา workers เพิ่มเติม
        
    except Exception:
        pass

    cg_usd = 0.0
    cg_thb = 0.0
    try:
        cg_res = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=verus-coin&vs_currencies=usd,thb", timeout=5)
        if cg_res.status_code == 200:
            cg_data = cg_res.json()
            if 'verus-coin' in cg_data:
                cg_usd = float(cg_data['verus-coin'].get('usd', 0.0))
                cg_thb = float(cg_data['verus-coin'].get('thb', 0.0))
    except Exception:
        pass
    tg_token = get_setting('tg_token', '')
    tg_chat_id = get_setting('tg_chat_id', '')
    alert_sent = get_setting('verus_low_alert', '0') 

    active_price_thb = cg_thb if cg_thb > 0 else 10.0
    hashrate_num = 0
    try:
        hashrate_num = float(
        str(total_hashrate).replace("MH", "").replace("GH", "").strip()
    )
        
        if hashrate_num < 420 and alert_sent != '1':
            r = requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={
                    "chat_id": tg_chat_id,
                    "text": f"🚨 ALERT\nHashrate ต่ำกว่า 420 MH\nปัจจุบัน {hashrate_num:.2f} MH"
                },
                timeout=10
            )

            save_setting("verus_low_alert", "1")
        elif hashrate_num >= 420:
            save_setting("verus_low_alert", "0")

            # st.write(r.text)
    except Exception:pass
    st.markdown("""
<style>
[data-testid="stMetricLabel"] {
    font-size: 0.9rem !important;
}
[data-testid="stMetricValue"] {
    font-size: 1.4rem !important;
    font-weight: 600 !important;
}
</style>
""", unsafe_allow_html=True)  

    st.subheader("🌐 สถิติการขุดจาก LuckPool (Live)")
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("⚡ Hashrate รวม", total_hashrate)
    col_m2.metric("💰 Unpaid Balance", f"{api_balance:,.6f} VRSC")
    col_m3.metric("📥 Total Paid", f"{api_paid:,.4f} VRSC")
    col_m4.metric("🪙 Immature", f"{immature:.6f} VRSC")

    st.markdown("---")
    st.subheader("⚙️ จัดการยอดเหรียญใน Verus Mobile Wallet")
    
    wallet_balance = get_verus_wallet_balance(verus_address)
    current_wallet = wallet_balance if wallet_balance is not None else (api_paid + api_balance)
    total_market_thb = current_wallet * active_price_thb

    if cg_usd > 0:
        st.success(f"🟢 ดึงราคาจาก CoinGecko สำเร็จ: **${cg_usd} USD** (~**{cg_thb:,.2f} บาท**) ต่อ 1 VRSC")
    else:
        st.warning("🟡 ไม่สามารถเชื่อมต่อ CoinGecko ได้ชั่วคราว ระบบใช้ราคาสำรองในการคำนวณ")

    col_res1, col_res2, col_res3 = st.columns(3)
    col_res1.metric("🪙 ยอดเหรียญใน Mobile Wallet", f"{current_wallet:,.8f} VRSC")
    col_res2.metric("💵 ราคาตลาดปัจจุบัน (CoinGecko)", f"{active_price_thb:,.2f} บาท/VRSC")
    col_res3.metric("💰 มูลค่ารวมในกระเป๋า (THB)", f"{total_market_thb:,.2f} บาท")

    pool_web_url = f"https://luckpool.net/verus/miner.html?{verus_address}"

    st.link_button(
        "🌐 เปิดหน้าเว็บสถิติ LuckPool (เว็บทางการ)",
        pool_web_url,
        use_container_width=True
    )

    st.markdown("---")
    st.subheader("📈 Hashrate Analytics")

    import sqlite3
    import pandas as pd
    from datetime import datetime, timedelta

    try:

        conn = sqlite3.connect("datacenter.db")
        cursor = conn.cursor()
        cursor.execute("""
        DELETE FROM hashrate_history
        WHERE hashrate = 0
        """)

        conn.commit()

        conn.execute("""
        CREATE TABLE IF NOT EXISTS hashrate_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            hashrate REAL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS vrsc_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            paid REAL,
            balance REAL,
            immature REAL
        )
""")

        try:
            hashrate_numeric = float(
                str(total_hashrate)
                .replace("MH", "")
                .strip()
            )

            conn.execute("""
            INSERT INTO hashrate_history
            (timestamp, hashrate)
            VALUES (?,?)
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                hashrate_numeric
            ))

            conn.execute("""
            INSERT INTO vrsc_daily
            (timestamp, paid, balance, immature)
            VALUES (?, ?, ?, ?)
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                api_paid,
                api_balance,
                immature
            ))
            conn.commit()

        except:
            pass
        conn = sqlite3.connect("datacenter.db")

        df_test = pd.read_sql_query(
            "SELECT COUNT(*) as total FROM vrsc_daily",
            conn
        )

        conn = sqlite3.connect("datacenter.db")

        df_test = pd.read_sql_query(
        "SELECT COUNT(*) as total FROM vrsc_daily",
            conn
        )

        df_vrsc = pd.read_sql_query("""
        SELECT *
        FROM vrsc_daily
        ORDER BY timestamp DESC
        LIMIT 5
        """, conn)
        
        df_vrsc = pd.read_sql_query(
            """
        SELECT timestamp, paid, balance, immature            
            FROM vrsc_daily
            ORDER BY timestamp
            """,
            conn
        )

        period = st.selectbox(
            "เลือกช่วงเวลา",
            ["วันนี้", "3 วัน", "7 วัน", "15 วัน", "1 เดือน", "1 ปี", "ทั้งหมด"]
        )

        df_hash = pd.read_sql_query(
            """
            SELECT timestamp, hashrate
            FROM hashrate_history
            ORDER BY timestamp
            """,
            conn
            
        )

        df_vrsc = pd.read_sql_query(
            """
            SELECT timestamp, paid, balance, immature
            FROM vrsc_daily
            ORDER BY timestamp
            """,
            conn
        )
        
        # คำนวณจาก LuckPool earnings/address (V15)
        earnings_url = "https://luckpool.net/verus/earnings/REn28U7KUABvRQTwWwjKYnkCYyiBC1ga7L"
        mined_today = 0.0

        try:
            er = requests.get(earnings_url, timeout=10)
            er.raise_for_status()
            earnings = er.json()
            now = pd.Timestamp.now()

            days_map = {
                "วันนี้": 1,
                "3 วัน": 3,
                "7 วัน": 7,
                "10 วัน": 10,
                "15 วัน": 15,
                "1 เดือน": 30,
                "1 ปี": 365,
            }

            days = days_map.get(period)

            for item in earnings:
                parts = item.split(":")
                if len(parts) != 3:
                    continue

                ts = pd.to_datetime(int(parts[0]), unit="ms")
                amount = float(parts[2])

                if days is None or ts >= now - pd.Timedelta(days=days):
                    mined_today += amount

        except Exception:
            mined_today = 0.0

        conn.close()
        st.metric("⛏️ ขุดได้ในช่วงที่เลือก", f"{mined_today:.8f} VRSC")

        # ===== LUCKPOOL JACKPOT =====
        wallet = "REn28U7KUABvRQTwWwjKYnkCYyiBC1ga7L"

        try:
            r = requests.get(
                f"https://luckpool.net/verus/blocks/{wallet}",
                timeout=15,
                headers={"User-Agent":"Mozilla/5.0"}
            )
            r.raise_for_status()
            data = r.json()

            if isinstance(data, dict):
                records = data.get("blocks") or data.get("data") or []
            elif isinstance(data, list):
                records = data
            else:
                records = []

            jackpot_records = []
            for x in records:
                try:
                    if isinstance(x, str):
                        p=x.split(":")
                        jackpot_records.append({
                            "block": int(p[2]),
                            "amount": float(p[8])/100000000,
                            "timestamp": int(p[4])/1000
                        })
                    elif isinstance(x, dict):
                        jackpot_records.append({
                            "block": int(x.get("height") or x.get("block")),
                            "amount": float(x.get("reward") or x.get("amount") or 0),
                            "timestamp": int(x.get("timestamp") or x.get("time") or 0)
                        })
                except Exception:
                    pass

            jackpot_records.sort(key=lambda z: z["timestamp"], reverse=True)

            now_th = datetime.now()
            start_today = datetime(now_th.year, now_th.month, now_th.day)
            period_days = {"วันนี้":0,"3 วัน":3,"7 วัน":7,"15 วัน":15,"1 เดือน":30,"1 ปี":365}

            if period == "ทั้งหมด":
                filtered = jackpot_records
            elif period == "วันนี้":
                filtered=[x for x in jackpot_records if datetime.fromtimestamp(x["timestamp"]).date()==now_th.date()]
            else:
                start = start_today - timedelta(days=period_days[period]-1)
                filtered=[x for x in jackpot_records if datetime.fromtimestamp(x["timestamp"])>=start]

            latest = filtered[0] if filtered else None
            total_amount = sum(x["amount"] for x in filtered)
            highest = max([x["amount"] for x in filtered], default=0)

            today_records = [x for x in filtered if datetime.fromtimestamp(x["timestamp"]).date()==now_th.date()]

            st.subheader("🏆 Verus Jackpot")

            c1,c2,c3 = st.columns(3)
            c1.metric("🏆 จำนวนครั้ง", f"{len(filtered)} ครั้ง")
            c2.metric("💰 Jackpot ล่าสุด", f"{latest['amount']:.8f} VRSC" if latest else "0.00000000 VRSC")
            c3.metric("📈 Jackpot สูงสุด", f"{highest:.8f} VRSC")

            c4,c5,c6 = st.columns(3)
            c4.metric("🔢 Block ล่าสุด", latest["block"] if latest else "-")
            c5.metric("💰 Jackpot สะสม", f"{total_amount:.8f} VRSC")
            c6.metric("⏰ เวลาล่าสุด", datetime.fromtimestamp(latest["timestamp"]).strftime("%d/%m/%Y %H:%M") if latest else "-")

            st.markdown("---")

            c7,c8 = st.columns(2)
            c7.metric("🎯 Jackpot วันนี้", f"{len(today_records)} Block")
            c8.metric("💰 VRSC วันนี้", f"{sum(x['amount'] for x in today_records):.6f} VRSC")

        except Exception as e:
            st.error(f"Jackpot API Error: {e}")

        if not df_hash.empty:

            df_hash["timestamp"] = pd.to_datetime(
                df_hash["timestamp"]
            )

            now = datetime.now()

            if period == "วันนี้":
                start_date = now - timedelta(days=1)

            elif period == "3 วัน":
                start_date = now - timedelta(days=3)

            elif period == "7 วัน":
                start_date = now - timedelta(days=7)

            elif period == "15 วัน":
                start_date = now - timedelta(days=15)

            elif period == "1 เดือน":
                start_date = now - timedelta(days=30)

            elif period == "1 ปี":
                start_date = now - timedelta(days=365)

            else:
                start_date = None

            if start_date is not None:
                df_hash = df_hash[
                df_hash["timestamp"] >= start_date
            ]

            c1, c2, c3 = st.columns(3)

            c1.metric(
                "📊 ค่าเฉลี่ย",
                f"{df_hash['hashrate'].mean():.2f} MH"
            )

            c2.metric(
                "⬆️ ค่าสูงสุด",
                f"{df_hash['hashrate'].max():.2f} MH"
            )

            c3.metric(
                "⬇️ ค่าต่ำสุด",
                f"{df_hash['hashrate'].min():.2f} MH"
            )

            st.line_chart(
                df_hash.set_index("timestamp")["hashrate"]
            )

            st.markdown("---")
            
            st.subheader("💸 Payment History")
            import sqlite3
            conn = sqlite3.connect("datacenter.db")
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS payments_history(
                txid TEXT PRIMARY KEY,
                amount REAL,
                paid_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""")
            rows = cur.execute("SELECT txid, amount, paid_at FROM payments_history ORDER BY paid_at DESC LIMIT 20").fetchall()
            total = cur.execute("SELECT COALESCE(SUM(amount),0), COUNT(*) FROM payments_history").fetchone()
            latest_payment = rows[0][1] if rows else 0.0
            latest = rows[0][1] if rows else 0
            c1,c2,c3=st.columns(3)
            c1.metric("💰 ยอดรับล่าสุด", f"{latest_payment:.6f} VRSC")
            c2.metric("💎 ยอดสะสม", f"{total[0]:.6f} VRSC")
            c3.metric("🔢 จำนวนครั้ง", int(total[1]))
            if rows:
                st.dataframe([{"เวลา":r[2],"VRSC":r[1],"TXID":r[0]} for r in rows], use_container_width=True)
            else:
                st.info("ยังไม่มีข้อมูลการจ่ายจาก LuckPool ในฐานข้อมูล")
            st.markdown("---")
            st.subheader("🖥️ Worker Details")

            try:
                from luckpool_api import miner as miner_api
                w = miner_api("REn28U7KUABvRQTwWwjKYnkCYyiBC1ga7L")

                # LuckPool miner API ส่ง workers เป็นสตริงรูปแบบ:
                # noname:416281611.54:29068.053:on:ap:false:1:18.475
                worker_display="noname"
                hashrate="0 MH"
                shares=0
                status="🔴 Offline"

                workers=w.get("workers", [])
                if workers:
                    raw=workers[0]
                    if isinstance(raw, str):
                        p=raw.split(":")
                        worker_display=p[0]
                        sol=float(p[1]) if len(p)>1 else 0
                        shares=float(p[2]) if len(p)>2 else 0
                        state=p[3] if len(p)>3 else "off"
                        hashrate=f"{sol/1_000_000:.2f} MH"
                        status="🟢 Online" if state=="on" else "🔴 Offline"
                    elif isinstance(raw, dict):
                        worker_display=raw.get("name","noname")
                        hashrate=raw.get("hashrateString","0 MH")
                        shares=raw.get("shares",0)
                        status="🟢 Online" if float(raw.get("hashrateSol",0) or 0)>0 else "🔴 Offline"

                w1,w2,w3,w4 = st.columns(4)
                w1.metric("สถานะ", status)
                w2.metric("Worker", worker_display)
                w3.metric("Hashrate", hashrate)
                w4.metric("Shares", shares)

            except Exception as e:
                w1,w2,w3,w4 = st.columns(4)
                w1.metric("สถานะ","ยังไม่เชื่อม")
                w2.metric("Worker","noname")
                w3.metric("Hashrate","0 MH")
                w4.metric("Shares","0")
                st.info("รอข้อมูลจาก LuckPool OpenAPI : worker/address.worker")

            st.markdown("---")
            st.subheader("🌐 Network / Pool Stats")

            try:
                from luckpool_api import stats as pool_stats

                s = pool_stats()

                # LuckPool /stats ส่งค่าแบบ Flat JSON
                n1,n2,n3,n4 = st.columns(4)
                n1.metric("Difficulty", f"{float(s.get('networkDiff',0)):,.0f}")
                n2.metric("Network Hashrate", s.get("networkRate","-"))
                n3.metric("Block Height", f"{int(s.get('networkHeight',0)):,}")
                n4.metric("Pool Miners", f"{int(s.get('poolMiners',0)):,}")

                p1,p2,p3 = st.columns(3)
                p1.metric("Pool Hashrate", s.get("poolRate","-"))
                p2.metric("Workers", f"{int(s.get('poolWorkers',0)):,}")
                p3.metric("LuckPool Fee", "1%")

            except Exception as e:
                st.info("รอข้อมูลจาก LuckPool OpenAPI : stats/network")




        else:
            st.info("ยังไม่มีข้อมูล Hashrate")

    except Exception as e:
        st.error(f"Hashrate Analytics Error : {e}")
elif menu == "☀️ Solar (Solar System)":
    # st_autorefresh moved to top of file (avoid duplicate key)

    if st.button("⬅️ กลับหน้าแรก (Portal Hub)", key="back_home_solar"):
        st.session_state['nav_menu'] = "หน้าแรก (Portal Hub)"
        st.rerun()

    st.markdown("<h2>☀️ ระบบโซลาร์เซลล์ (Solar System)</h2>", unsafe_allow_html=True)
    st.info("📡 ข้อมูลจาก SOLARMAN Collector")

    conn = sqlite3.connect("datacenter.db")

    df_solar = pd.read_sql_query("""
    SELECT *
    FROM solar_realtime
    ORDER BY timestamp DESC
    LIMIT 1
    """, conn)

    conn.close()


    if not df_solar.empty:
        r = df_solar.iloc[0]

        # ===== LIVE SOLARMAN =====
        try:
            import solar_api
            live = solar_api.real_time()
            station = solar_api.station_detail()["stationList"][0]

            pv_power = float(live.get("generationPower") or 0) / 1000
            load_power = float(live.get("usePower") or 0) / 1000
            grid_power = (float(live.get("gridPower")) / 1000) if live.get("gridPower") is not None else None
            today_energy = float(live.get("generationToday") or 0)
            total_energy = float(live.get("generationTotal") or 0)
            station_name = station.get("name","โอ้เธอ หวานเจี๊ยบ")
            timestamp = live.get("lastUpdateTime")
        except Exception:
            pv_power = float(r.get("pv_power") or 0) / 1000
            load_power = float(r.get("load_power") or 0) / 1000
            grid_raw = r.get("grid_power")
            grid_power = None if grid_raw is None else float(grid_raw) / 1000
            today_energy = float(r.get("today_energy") or 0)
            total_energy = float(r.get("total_energy") or 0)
            station_name = r.get("station_name") or "โอ้เธอ หวานเจี๊ยบ"
            timestamp = r.get("timestamp")

        # ใช้ค่า gridPower ถ้ามีจริง ถ้าไม่มีให้คำนวณจากสมดุลพลังงาน
        if grid_power is not None and abs(grid_power) > 0.01:
            purchase_kw = max(0, -grid_power)
            sell_kw = max(0, grid_power)
        else:
            purchase_kw = max(0, load_power - pv_power)
            sell_kw = max(0, pv_power - load_power)
        if purchase_kw < 0.1:
            color = "#16a34a"; status = "🟢 แสงแดดเพียงพอ"
        elif purchase_kw < 0.5:
            color = "#ca8a04"; status = "🟡 เริ่มซื้อไฟ"
        else:
            color = "#dc2626"; status = "🔴 ซื้อไฟสูง"

        st.markdown(f"""
        <div style="background:{color};padding:22px;border-radius:16px;margin-bottom:18px;">
          <div style="font-size:18px;color:white;font-weight:bold;">⚡ ซื้อไฟจากการไฟฟ้า (KPI หลัก)</div>
          <div style="font-size:54px;font-weight:bold;color:white;">{purchase_kw*1000:.0f} W</div>
          <div style="color:white;font-size:16px;">{status}</div>
        </div>
        """, unsafe_allow_html=True)

        c1,c2,c3=st.columns(3)
        c1.metric("☀️ กำลังผลิต (PV)",f"{pv_power:.2f} kW")
        c2.metric("🏠 ใช้ไฟในบ้าน",f"{load_power:.2f} kW")
        c3.metric("📤 ส่งเข้ากริด",f"{sell_kw:.2f} kW")

        st.markdown("---")
        st.subheader("🔄 แผนผังพลังงานแบบเรียลไทม์ (Power Flow)")
        flow_html=f"""
<style>
.panel{{background:linear-gradient(180deg,#07111f,#020814);border:1px solid #1f3b5a;border-radius:22px;padding:22px}}
.glow{{filter:drop-shadow(0 0 8px #38bdf8)}}
.pv{{stroke:#38bdf8;stroke-width:7;fill:none;stroke-linecap:round;stroke-dasharray:12 18;animation:pv 1s linear infinite}}
.grid{{stroke:#22c55e;stroke-width:7;fill:none;stroke-linecap:round;stroke-dasharray:12 18;animation:grid 1s linear infinite}}
.load{{stroke:#60a5fa;stroke-width:7;fill:none;stroke-linecap:round;stroke-dasharray:12 18;animation:pv 1s linear infinite}}
@keyframes pv{{from{{stroke-dashoffset:30}}to{{stroke-dashoffset:0}}}}
@keyframes grid{{from{{stroke-dashoffset:-30}}to{{stroke-dashoffset:0}}}}
</style>
<div class="panel">
<svg viewBox="0 0 760 460" width="100%" xmlns="http://www.w3.org/2000/svg">
<path stroke="#365f8f" stroke-width="7" fill="none" d="M110 185 H320"/>
<path stroke="#365f8f" stroke-width="7" fill="none" d="M430 185 H650"/>
<path stroke="#365f8f" stroke-width="7" fill="none" d="M375 185 V340"/>
<path class="pv glow" d="M110 185 H320"/>
<path class="{'grid glow' if sell_kw>0 else 'pv glow'}" d="M430 185 H650"/>
<path class="load glow" d="M375 185 V340"/>
<circle cx="110" cy="185" r="8" fill="#60a5fa"/><circle cx="320" cy="185" r="8" fill="#60a5fa"/><circle cx="430" cy="185" r="8" fill="#60a5fa"/><circle cx="650" cy="185" r="8" fill="#60a5fa"/><circle cx="375" cy="265" r="8" fill="#60a5fa"/>
<image href="data:image/png;base64,{solar_panel_b64}"
       x="25"
       y="45"
       width="95"
       height="95"/>

<text x="135" y="95" class="title">กำลังผลิต</text>
<text x="135" y="125" class="value-blue">{pv_power:.2f} kW</text>
<text x="135" y="145" font-size="14" fill="#9ecbff">จากแผงโซลาร์เซลล์</text>
<circle cx="375" cy="185" r="8" fill="#60a5fa"/>

<image href="data:image/png;base64,{house_b64}"
       x="270"
       y="70"
       width="220"
       height="190"/>

<text x="350" y="255" class="title">ใช้ไฟในบ้าน</text>
<text x="350" y="280" class="value-yellow">{load_power:.2f} kW</text>
</svg>
<div style="display:flex;justify-content:space-between;margin-top:12px;color:#cbd5e1">
<span>🔵 พลังงานจากโซลาร์</span><span>{'🟢 ส่งเข้ากริด' if sell_kw>0 else '🔴 ซื้อไฟจากกริด' if purchase_kw>0 else '🟢 สมดุลพลังงาน'}</span>
</div></div>"""
        st.markdown(flow_html, unsafe_allow_html=True)

        col_left, col_right = st.columns([1, 1])

        with col_left:
            st.markdown("""
            <div style="
                background:#081524;
                border:1px solid #16395d;
                border-radius:16px;
                padding:16px;
                min-height:150px;">
                <h4 style="color:white;">📋 คำอธิบาย</h4>
                <div style="color:#38bdf8;">🔵 โซลาร์ → บ้าน</div>
                <div style="color:#60a5fa;">🔹 ใช้ไฟภายในบ้าน</div>
                <div style="color:#22c55e;">🟢 ส่งเข้ากริด</div>
                <div style="color:#facc15;">🟡 ซื้อไฟจากกริด</div>
            </div>
            """, unsafe_allow_html=True)

        with col_right:
            status_text = "แสงแดดเพียงพอ" if purchase_kw < 0.1 else "เริ่มซื้อไฟ"
            status_color = "#22c55e" if purchase_kw < 0.1 else "#f59e0b"

            st.markdown(f"""
            <div style="
                background:#081524;
                border:1px solid #16395d;
                border-radius:16px;
                padding:18px;
                min-height:180px;
                box-shadow:0 0 15px rgba(0,120,255,.08);">

            <div style="font-size:18px;font-weight:bold;color:white;">
                📊 สถานะปัจจุบัน
            </div>

            <div style="
                margin-top:18px;
                font-size:28px;
                font-weight:bold;
                color:{status_color};">
                {status_text}
            </div>

            <hr style="border-color:#16395d;margin:18px 0;">

            <div style="color:#9ecbff;font-size:15px;">
                ซื้อไฟจากการไฟฟ้า
            </div>
            <div style="font-size:22px;font-weight:bold;color:white;">
                {purchase_kw*1000:.0f} W
            </div>

            <div style="margin-top:10px;color:#9ecbff;font-size:15px;">
                พลังงานเหลือเข้าระบบ
            </div>
            <div style="font-size:22px;font-weight:bold;color:#22c55e;">
                {sell_kw:.2f} kW
            </div>

        """, unsafe_allow_html=True)
        
        ts = (
            datetime.fromtimestamp(int(timestamp)).strftime("%d/%m/%Y %H:%M:%S")
            if timestamp else "-"
        )
        st.caption(f"อัปเดตล่าสุด {ts}")

    else:
        st.warning("ยังไม่มีข้อมูลจาก Solar Collector")


elif menu == "🍦 ร้านไอศกรีม (Ice Cream)":
    if st.button("⬅️ กลับหน้าแรก (Portal Hub)"):
        st.session_state['nav_menu'] = "หน้าแรก (Portal Hub)"
        st.rerun()
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("👤 **เจ้าของร้าน / ผู้ดูแลระบบ**\n📍 **POS Terminal 1**\n🍦 **ระบบบริหารร้านไอศกรีม (Loyverse Style)**")
    st.sidebar.markdown("---")
    
    ice_menu_options = [
        "🛒 ขายสินค้า (POS Dashboard)", 
        "🧾 ใบเสร็จรับเงิน (Receipts)", 
        "📋 รายการสินค้า (Items Catalog)", 
        "⚙️ การตั้งค่าระบบ (Settings)", 
        "📊 รายงานภาพรวม (Back Office)", 
        "📱 คู่มือและการสนับสนุน (Support)"
    ]
    
    if 'ice_sub_menu' not in st.session_state:
        st.session_state['ice_sub_menu'] = "🛒 ขายสินค้า (POS Dashboard)"
        
    def update_ice_sub():
        st.session_state['ice_sub_menu'] = st.session_state['ice_sub_radio']

    selected_ice_menu = st.sidebar.radio("เมนูร้านไอศกรีม:", ice_menu_options, index=ice_menu_options.index(st.session_state['ice_sub_menu']) if st.session_state['ice_sub_menu'] in ice_menu_options else 0, key="ice_sub_radio", on_change=update_ice_sub)
    sub_menu = st.session_state['ice_sub_menu']

    if sub_menu == "🛒 ขายสินค้า (POS Dashboard)":
        st.title("🛒 หน้าจอขายสินค้า (POS Dashboard - Loyverse Style)")
        
        conn = sqlite3.connect('datacenter.db', timeout=15)
        conn.execute('''CREATE TABLE IF NOT EXISTS pos_cart_temp (
            item_id TEXT PRIMARY KEY,
            name TEXT,
            price REAL,
            qty INTEGER
        )''')
        conn.commit()

        col_catalog, col_cart = st.columns([3, 2])

        with col_catalog:
            st.subheader("📋 เลือกสินค้าและหมวดหมู่ (การ์ดแนวนอนมาตรฐาน POS)")
            try:
                df_items = pd.read_sql_query("SELECT id, name, category, price, image_path FROM ice_items", conn)
            except Exception:
                df_items = pd.DataFrame()

            if not df_items.empty:
                categories = ["ทั้งหมด"] + list(df_items['category'].unique())
                selected_cat = st.selectbox("📂 กรองหมวดหมู่สินค้า", options=categories, key="pos_cat_filter")

                if selected_cat != "ทั้งหมด":
                    filtered_df = df_items[df_items['category'] == selected_cat]
                else:
                    filtered_df = df_items

                st.markdown("""
                    <style>
                    div.stButton > button {
                        background-color: #16a34a !important;
                        color: white !important;
                        border: none !important;
                        border-radius: 6px !important;
                        font-weight: 600 !important;
                        padding: 8px 12px !important;
                        box-shadow: 0 1px 2px rgba(0,0,0,0.1) !important;
                        transition: background-color 0.2s ease !important;
                        width: 100% !important;
                    }
                    div.stButton > button:hover {
                        background-color: #15803d !important;
                    }
                    </style>
                """, unsafe_allow_html=True)

                for _, row in filtered_df.iterrows():
                    item_id = row['id']
                    item_name = row['name']
                    item_price = row['price']
                    img_path = row['image_path']

                    img_html = "🍦"
                    if pd.notna(img_path) and os.path.exists(str(img_path)):
                        try:
                            with open(img_path, "rb") as img_file:
                                encoded_string = base64.b64encode(img_file.read()).decode()
                            img_html = f'<img src="data:image/jpeg;base64,{encoded_string}" style="width: 45px; height: 45px; object-fit: cover; border-radius: 6px;">'
                        except:
                            pass

                    c_card, c_btn = st.columns([4, 1])

                    with c_card:
                        st.markdown(f"""
                            <div style="display: flex; align-items: center; gap: 10px; background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 6px 10px; margin-bottom: 4px;">
                                {img_html}
                                <div>
                                    <div style="font-size: 14px; font-weight: 600; color: #1e293b;">{item_name}</div>
                                    <div style="font-size: 13px; font-weight: 700; color: #16a34a;">฿{item_price:,.2f}</div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)

                    with c_btn:
                        if st.button("➕ เพิ่ม", key=f"add_pos_{item_id}", use_container_width=True):
                            c_add = conn.cursor()
                            c_add.execute("SELECT qty FROM pos_cart_temp WHERE item_id = ?", (str(item_id),))
                            cart_row = c_add.fetchone()
                            if cart_row:
                                new_q = cart_row[0] + 1
                                c_add.execute("UPDATE pos_cart_temp SET qty = ? WHERE item_id = ?", (new_q, str(item_id)))
                            else:
                                c_add.execute("INSERT INTO pos_cart_temp (item_id, name, price, qty) VALUES (?, ?, ?, 1)", (str(item_id), item_name, item_price))
                            conn.commit()
                            st.rerun()
            else:
                st.info("ยังไม่มีรายการสินค้าในระบบ กรุณาเพิ่มสินค้าที่เมนู 'รายการสินค้า (Items Catalog)'")

        with col_cart:
            st.subheader("🛒 ตะกร้าสินค้า (Cart)")
            df_cart = pd.read_sql_query("SELECT item_id, name, price, qty FROM pos_cart_temp", conn)
            
            if not df_cart.empty:
                total_price = 0.0
                for _, cart_row in df_cart.iterrows():
                    c_id = cart_row['item_id']
                    c_name = cart_row['name']
                    c_price = cart_row['price']
                    c_qty = cart_row['qty']
                    subtotal = c_price * c_qty
                    total_price += subtotal
                    
                    cc1, cc_minus, cc_plus, cc2, cc3 = st.columns([3, 1, 1, 2, 1])
                    cc1.write(f"**{c_name}** (x{c_qty})")
                    
                    if cc_minus.button("➖", key=f"minus_cart_{c_id}"):
                        if c_qty > 1:
                            conn.execute("UPDATE pos_cart_temp SET qty = qty - 1 WHERE item_id = ?", (c_id,))
                        else:
                            conn.execute("DELETE FROM pos_cart_temp WHERE item_id = ?", (c_id,))
                        conn.commit()
                        st.rerun()

                    if cc_plus.button("➕", key=f"plus_cart_{c_id}"):
                        conn.execute("UPDATE pos_cart_temp SET qty = qty + 1 WHERE item_id = ?", (c_id,))
                        conn.commit()
                        st.rerun()

                    cc2.write(f"฿{subtotal:,.2f}")
                    if cc3.button("❌", key=f"del_cart_{c_id}"):
                        conn.execute("DELETE FROM pos_cart_temp WHERE item_id = ?", (c_id,))
                        conn.commit()
                        st.rerun()

                st.markdown("---")
                st.markdown(f"### 💰 ยอดรวมทั้งสิ้น: `{total_price:,.2f}` บาท")
                
                payment_method = st.radio("เลือกวิธีชำระเงิน:", options=["เงินสด", "โอนจ่าย (QR Code)", "อื่นๆ"], horizontal=True)

                col_b1, col_b2 = st.columns(2)
                if col_b1.button("🗑️ ล้างตะกร้า"):
                    conn.execute("DELETE FROM pos_cart_temp")
                    conn.commit()
                    st.rerun()

                if col_b2.button("💳 ชำระเงิน (Charge)", type="primary"):
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    items_summary = ", ".join([f"{r['name']} x{r['qty']}" for _, r in df_cart.iterrows()])
                    
                    conn.execute("INSERT INTO ice_orders (timestamp, items_json, total_amount, payment_method) VALUES (?, ?, ?, ?)",
                                 (timestamp, items_summary, total_price, payment_method))
                    conn.execute("DELETE FROM pos_cart_temp")
                    conn.commit()
                    
                    st.success(f"✅ ชำระเงินสำเร็จ! ยอดรวม {total_price:,.2f} บาท")
                    time.sleep(1)
                    st.rerun()
            else:
                st.info("ตะกร้าสินค้าว่างเปล่า กดปุ่ม '➕ เพิ่ม' ที่การ์ดสินค้าด้านซ้ายได้เลยครับ")
        conn.close()

    elif sub_menu == "🧾 ใบเสร็จรับเงิน (Receipts)":
        st.title("🧾 ใบเสร็จรับเงิน (Receipts / History)")
        try:
            conn = sqlite3.connect('datacenter.db', timeout=15)
            df_orders = pd.read_sql_query("""
                SELECT 
                    id AS 'เลขที่บิล',
                    timestamp AS 'วัน-เวลา',
                    items_json AS 'รายการสินค้า',
                    total_amount AS 'ยอดรวม (บาท)',
                    payment_method AS 'วิธีชำระเงิน'
                FROM ice_orders
                ORDER BY id DESC
            """, conn)
            
            df_items_catalog = pd.read_sql_query("SELECT name, price FROM ice_items", conn)
            conn.close()
        except Exception:
            df_orders = pd.DataFrame()
            df_items_catalog = pd.DataFrame()

        item_names_list = list(df_items_catalog['name']) if not df_items_catalog.empty else []

        if not df_orders.empty:
            for _, ord_row in df_orders.iterrows():
                b_id = ord_row['เลขที่บิล']
                b_time = ord_row['วัน-เวลา']
                b_items = ord_row['รายการสินค้า']
                b_total = ord_row['ยอดรวม (บาท)']
                b_pay = ord_row['วิธีชำระเงิน']

                col_r1, col_r2, col_r3, col_r4, col_r5, col_r6 = st.columns([1, 2, 4, 2, 2, 2])
                col_r1.write(f"**#{b_id}**")
                col_r2.write(b_time)
                col_r3.write(b_items)
                col_r4.write(f"฿{b_total:,.2f}")
                col_r5.write(b_pay)

                with col_r6:
                    with st.popover("⚙️ จัดการ"):
                        st.markdown(f"**แก้ไขบิลเลขที่: #{b_id}**")
                        
                        selected_product = st.selectbox(
                            "เปลี่ยนรายการสินค้าเป็น:", 
                            options=["-- คงค่าเดิมไว้ --"] + item_names_list,
                            key=f"sel_prod_{b_id}"
                        )
                        
                        target_item_name = b_items
                        target_amount = b_total

                        if selected_product != "-- คงค่าเดิมไว้ --":
                            target_item_name = f"{selected_product} x1"
                            if not df_items_catalog.empty:
                                matched = df_items_catalog[df_items_catalog['name'] == selected_product]
                                if not matched.empty:
                                    target_amount = float(matched.iloc[0]['price'])

                        new_items_desc = st.text_input("รายการสินค้า", value=target_item_name, key=f"txt_items_{b_id}")
                        new_amount_str = st.text_input("ยอดรวม (บาท)", value=str(target_amount), key=f"txt_amt_{b_id}")
                        
                        pay_options = ["เงินสด", "โอนจ่าย (QR Code)", "อื่นๆ"]
                        default_pay_idx = pay_options.index(b_pay) if b_pay in pay_options else 0
                        new_pay_method = st.selectbox("วิธีชำระเงิน", options=pay_options, index=default_pay_idx, key=f"sel_pay_{b_id}")
                        
                        st.write("")
                        col_btn1, col_btn2 = st.columns(2)
                        
                        with col_btn1:
                            if st.button("💾 บันทึกการแก้ไข", key=f"save_edit_{b_id}", type="primary"):
                                p_amt = safe_float(new_amount_str)
                                if p_amt is not None and p_amt >= 0:
                                    conn_up = sqlite3.connect('datacenter.db', timeout=15)
                                    conn_up.execute("UPDATE ice_orders SET items_json = ?, total_amount = ?, payment_method = ? WHERE id = ?", (new_items_desc, p_amt, new_pay_method, b_id))
                                    conn_up.commit()
                                    conn_up.close()
                                    st.success("✅ แก้ไขบิลสำเร็จ!")
                                    time.sleep(0.5)
                                    st.rerun()
                                else:
                                    st.error("❌ กรุณากรอกยอดเงินให้ถูกต้อง!")

                        with col_btn2:
                            if st.button("🗑️ ลบบิลนี้", key=f"del_order_{b_id}"):
                                conn_del = sqlite3.connect('datacenter.db', timeout=15)
                                conn_del.execute("DELETE FROM ice_orders WHERE id = ?", (b_id,))
                                conn_del.commit()
                                conn_del.close()
                                st.success(f"✅ ลบบิล #{b_id} เรียบร้อย!")
                                time.sleep(0.5)
                                st.rerun()
                st.markdown("---")
        else:
            st.info("ยังไม่มีประวัติใบเสร็จรับเงินในระบบ")
        try:
            conn = sqlite3.connect('datacenter.db', timeout=15)
            df_catalog = pd.read_sql_query("SELECT id AS 'รหัส', name AS 'ชื่อสินค้า', category AS 'หมวดหมู่', price AS 'ราคา (บาท)' FROM ice_items", conn)
            conn.close()
            if not df_catalog.empty:
                st.dataframe(df_catalog, use_container_width=True, hide_index=True)
            else:
                st.info("ยังไม่มีรายการสินค้าในระบบ")
        except Exception:
            st.info("ยังไม่มีรายการสินค้าในระบบ")

    elif sub_menu == "⚙️ การตั้งค่าระบบ (Settings)":
        st.title("⚙️ การตั้งค่าระบบ (POS Settings)")
        st.info("การตั้งค่าระบบ POS")

    elif sub_menu == "📊 รายงานภาพรวม (Back Office)":
        st.title("📊 ระบบหลังร้านและรายงานภาพรวม (Back Office)")
        st.info("รายงานสรุปยอดขาย")

    elif sub_menu == "📱 คู่มือและการสนับสนุน (Support)":
        st.title("📱 คู่มือการใช้งาน (Support)")
        st.success("ระบบ POS ร้านไอศกรีมเวอร์ชัน 1.0.0")
        
    if st.button("⬅️ กลับหน้าแรก (Portal Hub)", key="back_home_btn_ice"):
       st.session_state['nav_menu'] = "หน้าแรก (Portal Hub)"
       st.rerun()