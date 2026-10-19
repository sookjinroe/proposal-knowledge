@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

echo 편집기를 윈도 작업 스케줄러에 맡깁니다.
echo.
echo   로그온할 때 저절로 뜨고, 꺼지면 5분 안에 다시 뜹니다.
echo   띄운 사람이 로그아웃하거나 다른 프로그램이 정리돼도 함께 꺼지지 않습니다.
echo.
set /p ROOT=지식시스템 폴더 경로 (그냥 엔터 = K:\Workgroups\제안전략 지식시스템):
if "%ROOT%"=="" set ROOT=K:\Workgroups\제안전략 지식시스템

set /p OPEN=다른 PC에서도 접속하게 할까요? (y = 사내망 공개, 엔터 = 이 PC만):
if /i "%OPEN%"=="y" (set HOSTARG=0.0.0.0) else (set HOSTARG=127.0.0.1)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_작업스케줄러.ps1" -Mode register -Root "%ROOT%" -Listen %HOSTARG%
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)

echo.
set /p NOW=지금 바로 띄울까요? (y = 지금 띄움, 엔터 = 5분 안에 저절로 뜸):
if /i "%NOW%"=="y" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_작업스케줄러.ps1" -Mode on
echo.
pause
