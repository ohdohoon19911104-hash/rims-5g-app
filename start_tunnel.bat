@echo off
echo ===================================================
echo [RIMS 5G] Cloudflare 외부 접속 터널을 시작합니다.
echo ===================================================
cloudflared.exe tunnel --url http://localhost:8000
pause