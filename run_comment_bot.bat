@echo off
cd /d D:\coding\ccidacafe

:loop
echo [%date% %time%] === comment bot start === >> comment_bot.log
python -u comment_bot.py >> comment_bot.log 2>&1
echo [%date% %time%] === bot stopped, restarting in 30s === >> comment_bot.log
timeout /t 30 /nobreak >nul
goto loop
