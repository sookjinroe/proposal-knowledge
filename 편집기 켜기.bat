@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo 제안전략 지식시스템 편집기를 켭니다...
echo.
rem 작업 스케줄러에 등록돼 있으면 그쪽으로 켠다. 창 없이 뜨고, 꺼져도 되살아난다.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_작업스케줄러.ps1" -Mode on
if not errorlevel 1 (
  echo.
  echo   잠시 뒤 http://127.0.0.1:8777 로 들어가 보세요.
  echo.
  pause
  exit /b 0
)

rem 등록돼 있지 않으면 이 창에 붙여 띄운다. 창을 닫으면 편집기도 꺼진다.
echo   창을 닫으면 편집기도 꺼집니다. 계속 켜 두려면 「자동시작 등록.bat」.
echo.
python watchdog.py %*
if errorlevel 1 (
  echo.
  echo 켜지지 않았습니다. python 이 설치돼 있는지, K드라이브가 연결돼 있는지 확인하세요.
  pause
)
