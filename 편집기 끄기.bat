@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo 편집기를 끕니다.
echo.
rem 작업 스케줄러에 맡겨 두었으면, 작업까지 멈춰야 한다.
rem 프로세스만 죽이면 5분 뒤 되살아나 「꺼지지 않는다」로 보인다.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_작업스케줄러.ps1" -Mode off
echo.
pause
