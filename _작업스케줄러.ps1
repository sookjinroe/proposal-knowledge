# 편집기를 윈도 「작업 스케줄러」에 맡긴다.
#
# 왜 시작 폴더가 아니라 작업 스케줄러인가.
# 시작 폴더에 넣으면 로그온할 때 한 번 뜨고, 그 뒤에 꺼지면 아무도 되살리지
# 않는다. 더 나쁜 것은 누가 띄웠느냐에 따라 **그 사람이 꺼질 때 같이 꺼진다**는
# 점이다. 2026-10-19 에 그래서 주말 내내 멈춰 있었다. Claude 앱이 스스로를
# 업데이트하면서 자기 밑에 붙은 프로세스를 전부 정리했고, 거기에 편집기가
# 끼어 있었다. 작업 스케줄러가 띄우면 윈도가 부모라서 그런 일이 없고,
# 5분마다 살아 있는지 확인해 꺼져 있으면 다시 띄운다.
#
# 쓰는 법 (「자동시작 등록.bat」 등이 대신 불러 준다)
#   powershell -File _작업스케줄러.ps1 -Mode register -Root "K:\..." -Listen 0.0.0.0
#   powershell -File _작업스케줄러.ps1 -Mode remove
#   powershell -File _작업스케줄러.ps1 -Mode on
#   powershell -File _작업스케줄러.ps1 -Mode off
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('register', 'remove', 'on', 'off', 'state')]
  [string]$Mode,
  [string]$Root = 'K:\Workgroups\제안전략 지식시스템',
  [string]$Listen = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'
$NAME = '제안전략 지식시스템 편집기'
$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path

function HasTask {
  $null -ne (Get-ScheduledTask -TaskName $NAME -ErrorAction SilentlyContinue)
}

function StopAll {
  # 지킴이를 먼저 내린다. 서버만 끄면 지킴이가 곧바로 다시 띄운다.
  Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$HERE*" } |
    Sort-Object { if ($_.CommandLine -like '*watchdog*') { 0 } else { 1 } } |
    ForEach-Object {
      Write-Host ('  내리는 중  PID ' + $_.ProcessId)
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

switch ($Mode) {

  'register' {
    if (-not (Test-Path $Root)) {
      Write-Host "  그 경로를 찾지 못했습니다: $Root"
      Write-Host '  K드라이브가 연결돼 있는지 확인하세요.'
      exit 1
    }
    $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $pyw) {
      Write-Host '  pythonw.exe 를 찾지 못했습니다. 파이썬이 설치돼 있는지 확인하세요.'
      exit 1
    }

    $log = Join-Path $HERE '_log\server.log'
    $arg = '"{0}\watchdog.py" --host {1} --no-open --root "{2}" --log "{3}"' -f $HERE, $Listen, $Root, $log

    $act = New-ScheduledTaskAction -Execute $pyw -Argument $arg -WorkingDirectory $HERE

    # 트리거를 둘 단다.
    #   하나는 로그온할 때. 켜고 들어오면 바로 떠 있어야 한다.
    #   하나는 5분마다 「살아 있나」 보는 것. 이미 돌고 있으면 IgnoreNew 로
    #   넘어가므로 두 개가 뜨지 않고, 꺼져 있으면 5분 안에 되살아난다.
    # 되풀이를 로그온 트리거에 달면 **다음 로그온까지 시작되지 않는다.**
    # 그래서 로그온과 무관한 트리거에 달아 둔다.
    $trg = @(
      (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME),
      (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(-1) `
         -RepetitionInterval (New-TimeSpan -Minutes 5))
    )

    # 시간 제한을 풀지 않으면 윈도가 사흘 뒤 알아서 꺼 버린다.
    $set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
             -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
             -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

    # K드라이브는 로그온한 세션 안에만 걸리는 드라이브다. 그래서
    # 「로그온해 있을 때만」(Interactive) 으로 둘 수밖에 없다.
    $prn = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
             -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask -TaskName $NAME -Action $act -Trigger $trg `
      -Settings $set -Principal $prn -Force `
      -Description '제안전략 지식시스템 편집기. 5분마다 살아 있는지 확인한다.' | Out-Null

    # 옛 방식(시작 폴더 바로가기)이 남아 있으면 둘이 겹친다.
    $old = Join-Path ([Environment]::GetFolderPath('Startup')) '지식시스템 편집기.lnk'
    if (Test-Path $old) {
      Remove-Item $old -Force
      Write-Host '  시작 폴더에 있던 옛 바로가기는 지웠습니다.'
    }

    Write-Host '  등록했습니다.'
    $addr = if ($Listen -eq '0.0.0.0') { '<이 PC 주소>' } else { '127.0.0.1' }
    Write-Host ('    여는 곳  http://{0}:8777/' -f $addr)
    Write-Host '    기록     _log\server.log'
  }

  'remove' {
    if (HasTask) {
      Unregister-ScheduledTask -TaskName $NAME -Confirm:$false
      Write-Host '  해제했습니다. 다음 로그온부터 뜨지 않습니다.'
    } else {
      Write-Host '  등록돼 있지 않습니다.'
    }
    $old = Join-Path ([Environment]::GetFolderPath('Startup')) '지식시스템 편집기.lnk'
    if (Test-Path $old) { Remove-Item $old -Force; Write-Host '  옛 바로가기도 지웠습니다.' }
  }

  'off' {
    # 작업을 멈춰 두지 않으면 5분 뒤 되살아난다 — 끄는 것처럼 보이지 않는다.
    if (HasTask) {
      Stop-ScheduledTask -TaskName $NAME -ErrorAction SilentlyContinue
      Disable-ScheduledTask -TaskName $NAME -ErrorAction SilentlyContinue | Out-Null
      Write-Host '  자동으로 다시 뜨지 않게 멈춰 두었습니다.'
    }
    StopAll
    Write-Host '  껐습니다. 다시 켜려면 「편집기 켜기.bat」.'
  }

  'on' {
    if (HasTask) {
      Enable-ScheduledTask -TaskName $NAME -ErrorAction SilentlyContinue | Out-Null
      Start-ScheduledTask -TaskName $NAME
      Write-Host '  켰습니다. 처음 뜰 때는 레코드를 훑느라 15초쯤 걸립니다.'
    } else {
      Write-Host '  등록돼 있지 않습니다. 「자동시작 등록.bat」 을 먼저 누르세요.'
      exit 1
    }
  }

  'state' {
    if (-not (HasTask)) { Write-Host '  등록돼 있지 않습니다.'; exit 1 }
    $t = Get-ScheduledTask -TaskName $NAME
    $i = Get-ScheduledTaskInfo -TaskName $NAME
    Write-Host ('  작업      ' + $t.State)
    Write-Host ('  마지막    {0}  (결과 {1})' -f $i.LastRunTime, $i.LastTaskResult)
    Write-Host ('  다음      ' + $i.NextRunTime)
    $live = Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
              Where-Object { $_.CommandLine -like "*$HERE*" }
    foreach ($p in $live) {
      $who = if ($p.CommandLine -like '*watchdog*') { '지킴이' } else { '편집기' }
      Write-Host ('  {0}    PID {1}' -f $who, $p.ProcessId)
    }
    if (-not $live) { Write-Host '  지금 돌고 있는 것은 없습니다.' }
  }
}
