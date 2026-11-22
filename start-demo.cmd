@echo off
cd /d "%~dp0"
py -3 KimsufiStockCommander.py --demo --open-browser
if errorlevel 1 pause