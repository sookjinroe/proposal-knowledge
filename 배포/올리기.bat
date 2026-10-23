@echo off
chcp 65001 > nul
setlocal
set DST=K:\Workgroups\제안전략 지식시스템\_편집기

echo 배포 원본을 K드라이브에 올립니다.
echo   %DST%
echo.

if not exist "%DST%" mkdir "%DST%"
copy /y "%~dp0..\server.py" "%DST%\" > nul && echo   server.py
copy /y "%~dp0..\app.html" "%DST%\" > nul && echo   app.html
copy /y "%~dp0편집기 켜기.bat" "%DST%\" > nul && echo   편집기 켜기.bat
copy /y "%~dp0읽어주세요.md" "%DST%\" > nul && echo   읽어주세요.md

echo.
echo 올렸습니다. 쓰는 분들은 다음에 켤 때 새 것으로 바뀝니다.
echo.
pause
