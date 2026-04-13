@echo off
cd /d "%~dp0"
echo ==============================================
echo   Strategic_IT_Suite
echo ==============================================

echo [1/3] Installing requirements...
py -m pip install -r requirements.txt
if errorlevel 1 pause & exit /b 1

echo [2/3] Running...
py app\main.py
if errorlevel 1 pause & exit /b 1

echo [3/3] Opening dashboard...
start "" "%~dp0output\index.html"

pause
