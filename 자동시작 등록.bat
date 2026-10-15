@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

echo 로그온할 때 편집기가 자동으로 뜨도록 등록합니다.
echo.
echo   폴더  %~dp0
echo.
set /p ROOT=지식시스템 폴더 경로 (그냥 엔터 = K:\Workgroups\제안전략 지식시스템):
if "%ROOT%"=="" set ROOT=K:\Workgroups\제안전략 지식시스템

if not exist "%ROOT%" (
  echo.
  echo   그 경로를 찾지 못했습니다: %ROOT%
  echo   K드라이브가 연결돼 있는지 확인하세요.
  pause
  exit /b 1
)

set /p OPEN=다른 PC에서도 접속하게 할까요? (y = 사내망 공개, 엔터 = 이 PC만):
if /i "%OPEN%"=="y" (set HOSTARG=0.0.0.0) else (set HOSTARG=127.0.0.1)

where pythonw.exe > "%TEMP%\_pw.txt" 2>nul
set /p PYW=< "%TEMP%\_pw.txt"
del "%TEMP%\_pw.txt" 2>nul
if "%PYW%"=="" (
  echo.
  echo   pythonw.exe 를 찾지 못했습니다. 파이썬이 설치돼 있는지 확인하세요.
  pause
  exit /b 1
)

powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) '지식시스템 편집기.lnk'));" ^
  "$s.TargetPath='%PYW%';" ^
  "$s.Arguments='\"%~dp0server.py\" --host %HOSTARG% --no-open --root \"%ROOT%\" --log \"%~dp0_log\server.log\"';" ^
  "$s.WorkingDirectory='%~dp0';" ^
  "$s.Description='제안전략 지식시스템 편집기';" ^
  "$s.Save()"

if errorlevel 1 (
  echo   등록에 실패했습니다.
  pause
  exit /b 1
)

echo.
echo   등록했습니다. 다음 로그온부터 창 없이 알아서 뜹니다.
echo   기록은 _log\server.log 에 쌓입니다.
echo.
echo   지금 바로 띄울까요?
set /p NOW=(y = 지금 띄움, 엔터 = 다음 로그온부터):
if /i "%NOW%"=="y" (
  start "" "%PYW%" "%~dp0server.py" --host %HOSTARG% --no-open --root "%ROOT%" --log "%~dp0_log\server.log"
  echo   띄웠습니다. 잠시 뒤 http://127.0.0.1:8777 로 들어가 보세요.
  echo   처음 뜰 때는 레코드를 훑느라 15초쯤 걸립니다.
)
echo.
pause
