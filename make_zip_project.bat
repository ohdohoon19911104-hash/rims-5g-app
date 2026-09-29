@echo off
chcp 65001 > nul
echo Creating Project Package...

:: requirements.txt
(
echo fastapi==0.109.0
echo uvicorn==0.27.0
echo pandas==2.2.0
echo openpyxl==3.1.2
echo supabase==2.3.0
echo python-multipart==0.0.6
echo pydantic==2.5.3
) > requirements.txt

:: start.bat
(
echo @echo off
echo chcp 65001 ^> nul
echo title RIMS Repair Management Server
echo echo [1/2] Checking Python Packages...
echo pip install -r requirements.txt
echo echo.
echo echo [2/2] Starting Server...
echo echo Access URL: http://localhost:8000
echo echo.
echo python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
echo pause
) > start.bat

echo Project Package Ready!
pause