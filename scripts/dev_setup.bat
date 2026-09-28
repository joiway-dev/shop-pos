@echo off
rem Developer setup after cloning/pulling on a new machine:
rem create .venv, install dev requirements, migrate, run tests.
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    py -3.13 -m venv .venv || goto :error
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-dev.txt || goto :error
".venv\Scripts\python.exe" -m alembic upgrade head || goto :error
".venv\Scripts\python.exe" -m pytest -q || goto :error
echo.
echo พร้อมพัฒนาต่อแล้ว
goto :eof

:error
echo.
echo dev setup failed
exit /b 1
