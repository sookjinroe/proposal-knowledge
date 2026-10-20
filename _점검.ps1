# 한 달에 한 번, 편집기가 멀쩡한지 훑는다.
#
# 5분마다 도는 되살림은 「꺼진 것」만 잡는다. 꺼지지 않고 **이상한 채로**
# 도는 것은 아무도 알려 주지 않는다. K드라이브가 느려졌거나, 자동 갱신이
# 조용히 멈췄거나, 잠금 파일이 남아 남의 수정을 막고 있거나 하는 것들이다.
# 그래서 달마다 한 번 훑고, 이상이 있을 때만 말을 건다.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File _점검.ps1
#   powershell ... -File _점검.ps1 -Quiet      창 띄우지 않고 기록만
param(
  [string]$Root = 'K:\Workgroups\제안전략 지식시스템',
  [switch]$Quiet,
  # 이미 보고 손본 것을 그만 가리키게 한다. 「여기부터 다시 세라」.
  [switch]$Clear
)

$ErrorActionPreference = 'Continue'
$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
$TASK = '제안전략 지식시스템 편집기'
$줄   = New-Object Collections.Generic.List[string]
$탈   = New-Object Collections.Generic.List[string]

function 적어($표, $글) {
  $줄.Add(('  {0} {1}' -f $표, $글))
  if ($표 -eq '**') { $탈.Add($글) }
}

function 답하나($port) {
  $c = New-Object Net.Sockets.TcpClient
  try { $c.Connect('127.0.0.1', $port); return $true }
  catch { return $false }
  finally { $c.Close() }
}

# ── 기준일 — 「여기부터 다시 센다」
# 이미 손본 일을 달마다 다시 가리키면 금방 무시하게 된다. 그래서 기준을
# 파일에 적어 두고 그 뒤에 생긴 것만 센다. 다 보고 손봤으면 -Clear.
$기준파일 = Join-Path $HERE '_log\점검기준.txt'
if ($Clear) {
  New-Item -ItemType Directory -Path (Split-Path $기준파일) -Force -ErrorAction SilentlyContinue | Out-Null
  Set-Content -LiteralPath $기준파일 -Value (Get-Date).ToString('o') -Encoding UTF8
  Write-Host ('  기준을 지금({0:yyyy-MM-dd HH:mm})으로 옮겼습니다. 이 앞의 것은 더 가리키지 않습니다.' -f (Get-Date))
  exit 0
}
try {
  if (Test-Path $기준파일) {
    $기준 = [datetime]::Parse((Get-Content -LiteralPath $기준파일 -TotalCount 1))
  } else {
    $기준 = Get-Date
    New-Item -ItemType Directory -Path (Split-Path $기준파일) -Force -ErrorAction SilentlyContinue | Out-Null
    Set-Content -LiteralPath $기준파일 -Value $기준.ToString('o') -Encoding UTF8
  }
} catch { $기준 = (Get-Date).AddDays(-40) }

# 로그 줄머리의 `MM-DD HH:mm:ss` 를 시각으로 읽는다. 해가 없으니 올해로 보고,
# 앞날로 나오면 지난해 것이다 (연말에 넘어간 로그).
function 줄시각($줄) {
  if ($줄 -notmatch '^(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})') { return $null }
  $t = Get-Date -Year (Get-Date).Year -Month $Matches[1] -Day $Matches[2] `
         -Hour $Matches[3] -Minute $Matches[4] -Second $Matches[5]
  if ($t -gt (Get-Date).AddDays(1)) { $t = $t.AddYears(-1) }
  return $t
}

# ── 1. 편집기를 띄워 주는 작업이 살아 있나
$t = Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue
if (-not $t) {
  적어 '**' '편집기 예약 작업이 없습니다. 「자동시작 등록.bat」 을 다시 누르세요.'
} elseif ($t.Settings.Enabled -eq $false) {
  적어 '**' '편집기 예약 작업이 멈춰 있습니다. 「편집기 켜기.bat」 을 누르세요.'
} else {
  $i = Get-ScheduledTaskInfo -TaskName $TASK
  적어 '✔' ('예약 작업 {0} · 마지막 {1:MM-dd HH:mm}' -f $t.State, $i.LastRunTime)
}

# ── 2. 편집기와 조회기가 실제로 답하나
if (답하나 8777) { 적어 '✔' '편집기 8777 답합니다' }
else { 적어 '**' '편집기가 8777 에서 답하지 않습니다. 5분 안에 저절로 뜨는지 보고, 안 되면 「편집기 켜기.bat」.' }

if (답하나 8799) { 적어 '✔' '실적 조회기 8799 답합니다' }
else { 적어 '**' '실적 조회기가 8799 에서 답하지 않습니다.' }

# ── 3. K드라이브가 느려지지 않았나
try {
  $ms = (Measure-Command { Get-ChildItem -LiteralPath $Root -Name -ErrorAction Stop | Out-Null }).TotalSeconds
  if ($ms -ge 5) { 적어 '**' ('K드라이브가 {0:N1}초 걸립니다. 평소는 1초 아래입니다.' -f $ms) }
  else { 적어 '✔' ('K드라이브 {0:N1}초' -f $ms) }
} catch {
  적어 '**' ('K드라이브를 읽지 못했습니다: ' + $_.Exception.Message)
}

# ── 4. 기록에 느림·멈춤이 남았나
$log = Join-Path $HERE '_log\server.log'
if (Test-Path $log) {
  $tail = Get-Content -LiteralPath $log -Tail 20000 -Encoding UTF8
  # @() 로 싸지 않으면 한 줄만 걸릴 때 문자열이 와서 [-1] 이 글자가 된다
  $slow = @($tail | Where-Object {
    $_ -match '느림|멈춘 자리|튕겼습니다|다 실패했습니다' -and
    { $t = 줄시각 $_; $null -eq $t -or $t -ge $기준 }.Invoke()
  })
  if ($slow.Count -gt 0) {
    적어 '**' ('{0:MM-dd} 뒤로 기록에 느림·멈춤이 {1}줄 있습니다. 마지막: {2}' -f $기준, $slow.Count, ($slow[-1].Trim()))
  } else {
    적어 '✔' ('{0:MM-dd} 뒤로 기록에 느림·멈춤 없음' -f $기준)
  }
} else {
  적어 '**' '기록 파일이 없습니다: _log\server.log'
}

# ── 5. 윈도가 적어 둔 멈춤 — 기준 뒤로 새로 생긴 것만
try {
  $hang = @(Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1002; StartTime=$기준} -ErrorAction SilentlyContinue |
             Where-Object { $_.Properties[0].Value -like 'python*' })
  if ($hang.Count -gt 0) {
    적어 '**' ('{0:MM-dd} 뒤로 파이썬 멈춤이 {1}번 있었습니다 (마지막 {2:MM-dd HH:mm}). 누가 띄웠는지 다시 보세요.' -f $기준, $hang.Count, $hang[0].TimeCreated)
  } else {
    적어 '✔' ('{0:MM-dd} 뒤로 윈도 멈춤 기록 없음' -f $기준)
  }
} catch { 적어 '✔' '윈도 멈춤 기록을 읽지 못했습니다 (넘어갑니다)' }

# ── 6. 자동 갱신이 돌고 있나 (10분마다 fetch 한다)
$fh = Join-Path $HERE '.git\FETCH_HEAD'
if (Test-Path $fh) {
  $age = (Get-Date) - (Get-Item $fh).LastWriteTime
  # 자동 갱신은 1시간마다 돈다. 두 번 거르는 것은 프록시가 느린 날이면
  # 흔하다. 네 번 거르면 그때는 정말 안 되는 것이다.
  if ($age.TotalHours -ge 4) {
    적어 '**' ('자동 갱신이 {0:N0}시간째 돌지 않았습니다. GitHub 에 닿지 못하는지 보세요.' -f $age.TotalHours)
  } else {
    적어 '✔' ('자동 갱신 {0:N0}분 전' -f $age.TotalMinutes)
  }
} else {
  적어 '**' '아직 한 번도 자동 갱신을 받지 않았습니다.'
}

# ── 7. 남은 잠금 — 남의 수정을 막는다
$lockdir = Join-Path $Root '_편집중'
if (Test-Path $lockdir) {
  $old = Get-ChildItem -LiteralPath $lockdir -Filter *.json -ErrorAction SilentlyContinue |
           Where-Object { ((Get-Date) - $_.LastWriteTime).TotalMinutes -ge 30 }
  if ($old) {
    적어 '**' ('30분 넘게 남은 잠금이 {0}개 있습니다: {1}. 지워도 됩니다.' -f $old.Count, (($old.Name | Select-Object -First 3) -join ', '))
  } else {
    적어 '✔' '남은 잠금 없음'
  }
}

# ── 8. 백업이 얼마나 쌓였나 (알려 주기만)
$bk = Join-Path $HERE '_백업'
if (Test-Path $bk) {
  $f = Get-ChildItem -LiteralPath $bk -Recurse -File -ErrorAction SilentlyContinue
  적어 '✔' ('백업 {0}개 · {1:N0}MB' -f $f.Count, (($f | Measure-Object Length -Sum).Sum / 1MB))
}

# ── 적어 두고, 이상이 있을 때만 말을 건다
$머리 = '── 점검 {0} ──' -f (Get-Date -Format 'yyyy-MM-dd HH:mm')
$본문 = @($머리) + $줄
$본문 | ForEach-Object { Write-Host $_ }

$기록 = Join-Path $HERE '_log\점검.log'
try {
  New-Item -ItemType Directory -Path (Split-Path $기록) -Force -ErrorAction SilentlyContinue | Out-Null
  Add-Content -LiteralPath $기록 -Value ($본문 + '') -Encoding UTF8
} catch { }

if ($탈.Count -gt 0 -and -not $Quiet) {
  try {
    Add-Type -AssemblyName System.Windows.Forms
    $글 = "제안전략 지식시스템 편집기 점검에서 걸린 것:`r`n`r`n" +
          (($탈 | ForEach-Object { '· ' + $_ }) -join "`r`n") +
          "`r`n`r`n자세한 것은 _log\점검.log"
    [Windows.Forms.MessageBox]::Show($글, '지식시스템 편집기 점검', 'OK', 'Warning') | Out-Null
  } catch { }
}

exit $(if ($탈.Count -gt 0) { 1 } else { 0 })
