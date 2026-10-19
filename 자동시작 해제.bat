@echo off
chcp 65001 > nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_작업스케줄러.ps1" -Mode remove
echo.
echo   지금 돌고 있는 것은 그대로입니다. 끄려면 「편집기 끄기.bat」.
echo.
pause
