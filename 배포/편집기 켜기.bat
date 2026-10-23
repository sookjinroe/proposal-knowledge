@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
title 제안전략 지식시스템 편집기

set SRC=K:\Workgroups\제안전략 지식시스템\_편집기

rem 켤 때마다 K드라이브의 새 파일만 가져온다. 따로 받아 갈 것이 없다.
rem K드라이브가 안 붙어 있으면 조용히 넘어가고 있는 것으로 띄운다.
if /i not "%~dp0"=="%SRC%\" (
  xcopy "%SRC%\server.py" "%~dp0" /d /y /q > nul 2>&1
  xcopy "%SRC%\app.html"  "%~dp0" /d /y /q > nul 2>&1
)

rem 파이썬을 찾는다. 이 폴더에 같이 넣어 둔 것이 있으면 그걸 먼저 쓴다.
set PY=
if exist "%~dp0python\python.exe" set PY=%~dp0python\python.exe
if not "%PY%"=="" goto :found

rem py 런처를 먼저 본다. where python 은 스토어 안내창만 띄우는 껍데기를
rem 집을 때가 있어서, 집은 것이 정말 도는지 한 번 확인한다.
for /f "delims=" %%i in ('where py 2^>nul') do if "%PY%"=="" set PY=%%i
if not "%PY%"=="" "%PY%" -c "pass" > nul 2>&1 && goto :found
set PY=
for /f "delims=" %%i in ('where python 2^>nul') do if "%PY%"=="" set PY=%%i
if not "%PY%"=="" "%PY%" -c "pass" > nul 2>&1 && goto :found
set PY=

echo.
echo   파이썬이 없어서 켜지 못했습니다.
echo   같은 폴더의 「읽어주세요.md」 를 보세요.
echo.
pause
exit /b 1

:found
echo 제안전략 지식시스템 편집기를 켭니다...
echo.
echo   곧 브라우저가 열립니다. 처음에는 레코드를 훑느라 15초쯤 걸립니다.
echo.
echo   ** 이 창은 그대로 두세요. 닫으면 편집기도 꺼집니다.
echo.
"%PY%" "%~dp0server.py" %*
echo.
echo   편집기가 꺼졌습니다. 이 창은 닫아도 됩니다.
pause
