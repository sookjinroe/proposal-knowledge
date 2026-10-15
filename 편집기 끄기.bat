@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
set PORT=8777
if not "%~1"=="" set PORT=%~1

echo %PORT% 번 포트에서 도는 편집기를 끕니다.
echo.

set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"127.0.0.1:%PORT% .*LISTENING" /c:"0.0.0.0:%PORT% .*LISTENING"') do (
  echo   끄는 중  PID %%p
  taskkill /F /PID %%p > nul 2>&1
  set FOUND=1
)

if "!FOUND!"=="0" (
  echo   켜져 있지 않습니다.
) else (
  echo   껐습니다.
)
echo.
echo   다시 켜려면 「편집기 켜기.bat」 을 누르거나, 로그온을 다시 하세요.
echo   (자동 시작을 등록해 두었다면 로그온할 때 알아서 뜹니다)
echo.
pause
