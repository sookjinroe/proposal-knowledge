@echo off
chcp 65001 > nul
powershell -NoProfile -Command ^
  "$p=Join-Path ([Environment]::GetFolderPath('Startup')) '지식시스템 편집기.lnk';" ^
  "if (Test-Path $p) { Remove-Item $p; '  해제했습니다. 다음 로그온부터 뜨지 않습니다.' } else { '  등록돼 있지 않습니다.' }"
echo.
echo   지금 돌고 있는 것은 그대로입니다. 끄려면 「편집기 끄기.bat」.
echo.
pause
