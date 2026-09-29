@echo off
chcp 65001 > nul
title RIMS Repair Management Server
echo [1/2] Checking Python Packages...
pip install -r requirements.txt
echo.
echo [2/2] Starting Server...
echo Access URL: http://localhost:8000
echo.
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
