@echo off
chcp 65001 > nul
cd /d "%~dp0"
python notebook_cafe_auto.py
echo.
echo ─────────────────────────────
echo 종료하려면 아무 키나 누르세요.
pause > nul
