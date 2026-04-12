@echo off
cd /d "%~dp0"
echo ==============================================
echo   Executive Signal
echo ==============================================

echo [1/3] Installing requirements...
py -m pip install -r requirements.txt
if errorlevel 1 pause & exit /b 1

echo [2/3] Building Executive Signal...
py -m app.main
if errorlevel 1 pause & exit /b 1

echo [3/3] Opening dashboard...
start "" "%~dp0output\index.html"

pause
