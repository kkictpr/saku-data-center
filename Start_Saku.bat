@echo off
:: ไปที่โฟลเดอร์โปรเจกต์
cd /d D:\financial-datacenter

:: สั่งเปิด VS Code ขึ้นมาก่อน
code .

:: สั่งรัน Streamlit โดยใช้ Python ภายในเครื่องโดยตรง
start cmd /k "C:\Users\PC-001\AppData\Local\Programs\Python\Python313\python.exe -m streamlit run app.py"