# 댓글봇 watchdog — 작업스케줄러가 5분마다 호출한다.
# 핵심 아이디어: "감시자(supervisor)" 역할을 bat 무한루프가 아니라 작업스케줄러가 맡는다.
# 작업스케줄러는 절전/강제종료/트리 전체 사망에도 살아남아 다음 주기에 다시 이 스크립트를 부른다.
#
# 동작:
#   1) comment_bot.py(watch) 가 이미 떠 있으면 → 아무것도 안 하고 종료(멱등).
#   2) 안 떠 있으면 → 봇을 백그라운드(콘솔 숨김)로 1회 기동하고 종료.
#      python 자체가 내부 루프(N초마다 감시)를 돌므로 살아만 있으면 계속 동작한다.
#      봇이 죽으면 최대 5분 안에 이 watchdog 이 다시 띄운다.
$ErrorActionPreference = 'SilentlyContinue'
$dir = 'D:\coding\ccidacafe'
Set-Location $dir

# 이미 실행 중인 댓글봇 python 이 있으면 종료 (backfill/test/inspect 등 수동 실행 포함 →
# 같은 크롬 프로필을 공유하므로 무엇이든 떠 있으면 watch 를 새로 띄우지 않는다)
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*comment_bot.py*' }
if ($running) { exit 0 }

$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

# ── 좀비 크롬 청소 (반복 사망의 근본 원인) ──
# 봇이 비정상 종료되면 자기가 띄운 크롬이 고아로 남아 chrome_profile_commentbot 프로필을
# 계속 잠근다. 그 상태로 새 봇이 같은 프로필을 열려 하면 "Chrome instance exited" 로 죽고,
# 재시작해도 좀비가 그대로라 무한 사망한다. 봇이 안 떠 있는 지금(=고아 확정)만 정리한다.
# ※ 사용자의 일반 크롬은 절대 건드리지 않는다 — commentbot 프로필을 쓰는 것만 정밀 종료.
$orphans = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
    Where-Object { $_.CommandLine -like '*chrome_profile_commentbot*' }
if ($orphans) {
    Add-Content -Path (Join-Path $dir 'comment_bot.log') -Value "[$ts] === watchdog: 좀비 크롬 $(@($orphans).Count)개 정리 ===" -Encoding UTF8
    # 좀비 크롬을 띄운 chromedriver(부모)도 함께 정리 (다른 프로젝트 chromedriver 는 미접촉)
    $orphans | Select-Object -ExpandProperty ParentProcessId -Unique | ForEach-Object {
        $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$_" -ErrorAction SilentlyContinue
        if ($pp -and $pp.Name -eq 'chromedriver.exe') { Stop-Process -Id $pp.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    $orphans | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

Add-Content -Path (Join-Path $dir 'comment_bot.log') -Value "[$ts] === watchdog: 봇 미실행 감지 → 기동 ===" -Encoding UTF8

# 콘솔 창 없이 python 기동, 로그는 comment_bot.log 로 append.
# (Selenium 이 띄우는 크롬 창은 봇 감지 회피상 보이게 뜨며, 이 프로세스와 별개)
Start-Process -FilePath 'cmd.exe' `
    -ArgumentList '/c', 'python -u comment_bot.py >> comment_bot.log 2>&1' `
    -WorkingDirectory $dir -WindowStyle Hidden
exit 0
