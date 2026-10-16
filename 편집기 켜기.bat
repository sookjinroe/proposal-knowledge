@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo 제안전략 지식시스템 편집기를 켭니다...
echo.
rem 지킴이를 거쳐 띄운다. 편집기가 뜻밖에 꺼져도 다시 띄워 준다.
python watchdog.py %*
if errorlevel 1 (
  echo.
  echo 켜지지 않았습니다. python 이 설치돼 있는지, K드라이브가 연결돼 있는지 확인하세요.
  pause
)
