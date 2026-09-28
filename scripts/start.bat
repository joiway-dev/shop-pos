@echo off
rem Start the shop system and open the browser.
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

rem Already running? (e.g. double-clicked twice, or an old window left open)
netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul
if not errorlevel 1 (
    echo ระบบเปิดอยู่แล้วในหน้าต่างอื่น — กำลังเปิดเบราว์เซอร์ให้
    echo ถ้าเพิ่งอัปเดตโปรแกรม: ปิดหน้าต่างสีดำเดิมก่อน แล้วเปิดไฟล์นี้ใหม่
    start "" http://localhost:8000
    pause
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] กำลังติดตั้งโปรแกรมครั้งแรก กรุณารอสักครู่...
    py -3.13 -m venv .venv || goto :error
)
rem Installs anything new after a program update (no internet needed when nothing changed).
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :error

echo [2/3] กำลังเตรียมฐานข้อมูล...
".venv\Scripts\python.exe" -m alembic upgrade head || goto :error

echo [3/3] กำลังเปิดระบบที่ http://localhost:8000
echo       ห้ามปิดหน้าต่างนี้ระหว่างใช้งาน (ปิดหน้าต่าง = ปิดระบบ)
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start "" http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
goto :eof

:error
echo.
echo เกิดข้อผิดพลาด กรุณาถ่ายรูปหน้าจอนี้ส่งให้ผู้ดูแลระบบ
pause
exit /b 1
