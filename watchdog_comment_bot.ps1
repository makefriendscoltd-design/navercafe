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
$running = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -match '(^|\s|\\)comment_bot\.py(\s|$)' }
if ($running) { exit 0 }

$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

# ── 로그파일 잠금 해제: 고아 chromedriver 청소 (봇이 안 뜨는 또다른 근본 원인) ──
# 봇이 비정상 종료되면, 봇이 띄운 chromedriver 가 watchdog 의 '>> comment_bot.log'
# 리다이렉트에서 상속받은 로그파일 핸들을 그대로 쥔 채 고아로 남는다. 그 상태로 새 봇을
# 띄우면 cmd 가 '>> comment_bot.log' 열기에 실패해 python 이 출력 한 줄 없이 즉사한다
# (banner 조차 안 찍힘 → "봇이 영영 안 뜸"). chromedriver 의 cmdline 엔 프로필 문자열이
# 없어 아래 프로필 기반 정리에서 빠지므로, 'comment_bot.log 를 잠근 PID' 를 Restart Manager
# 로 직접 찾아 정리한다. 이 지점은 봇 미실행 확정(위 $running 체크 통과) → comment_bot.log 를
# 잠근 것은 죽은 봇의 고아(chromedriver/cmd)뿐이라 종료해도 안전하다.
# (다른 selenium 프로젝트는 각자 다른 로그파일을 잠그지 ccidacafe 의 comment_bot.log 는 안 건드림)
$logPath = Join-Path $dir 'comment_bot.log'
$locked = $false
try { $fs = [System.IO.File]::Open($logPath, 'Append', 'Write', [System.IO.FileShare]::ReadWrite); $fs.Close() }
catch { $locked = $true }
if ($locked) {
    try {
        Add-Type -ErrorAction Stop -Language CSharp -TypeDefinition @'
using System; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class RMLock {
  [StructLayout(LayoutKind.Sequential)] struct RUP { public int pid; public System.Runtime.InteropServices.ComTypes.FILETIME t; }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct RPI {
    public RUP Process;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] public string app;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=64)] public string svc;
    public int type; public uint status; public uint tsid; [MarshalAs(UnmanagedType.Bool)] public bool restartable; }
  [DllImport("rstrtmgr.dll", CharSet=CharSet.Unicode)] static extern int RmStartSession(out uint h, int f, string k);
  [DllImport("rstrtmgr.dll")] static extern int RmEndSession(uint h);
  [DllImport("rstrtmgr.dll", CharSet=CharSet.Unicode)] static extern int RmRegisterResources(uint h, uint nf, string[] f, uint na, RUP[] a, uint ns, string[] s);
  [DllImport("rstrtmgr.dll")] static extern int RmGetList(uint h, out uint need, ref uint cnt, [In,Out] RPI[] arr, ref uint reason);
  public static int[] Lockers(string path){
    var res=new List<int>(); uint h; string key=Guid.NewGuid().ToString().Substring(0,16);
    if(RmStartSession(out h,0,key)!=0) return res.ToArray();
    try{ string[] f={path}; if(RmRegisterResources(h,1,f,0,null,0,null)!=0) return res.ToArray();
      uint need=0,cnt=0,reason=0; RmGetList(h,out need,ref cnt,null,ref reason);
      if(need==0) return res.ToArray(); var arr=new RPI[need]; cnt=need;
      if(RmGetList(h,out need,ref cnt,arr,ref reason)==0) for(uint i=0;i<cnt;i++) res.Add(arr[i].Process.pid);
    } finally { RmEndSession(h); } return res.ToArray();
  }
}
'@
        $killed = 0
        foreach ($lpid in [RMLock]::Lockers($logPath)) {
            $lp = Get-CimInstance Win32_Process -Filter "ProcessId=$lpid" -ErrorAction SilentlyContinue
            if ($lp -and $lp.Name -in @('chromedriver.exe', 'cmd.exe', 'conhost.exe')) {
                Stop-Process -Id $lpid -Force -ErrorAction SilentlyContinue
                $killed++
            }
        }
        if ($killed -gt 0) { Start-Sleep -Seconds 2 }
        # 정리 후 로그가 쓰기 가능해졌으면 기록 남김
        try { $fs2 = [System.IO.File]::Open($logPath, 'Append', 'Write', [System.IO.FileShare]::ReadWrite); $fs2.Close()
              Add-Content -Path $logPath -Value "[$ts] === watchdog: 로그 잠근 고아 프로세스 ${killed}개 정리(해제 성공) ===" -Encoding UTF8 } catch { }
    } catch { }
}

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

# python 전체 경로 확정 (작업스케줄러/수동 등 어떤 호출 맥락에서도 PATH 의존 없이 동작)
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }

# 콘솔 창 없이 python 기동, 로그는 comment_bot.log 로 append.
# (Selenium 이 띄우는 크롬 창은 봇 감지 회피상 보이게 뜨며, 이 프로세스와 별개)
Start-Process -FilePath 'cmd.exe' `
    -ArgumentList '/c', ('"' + $py + '" -u comment_bot.py >> comment_bot.log 2>&1') `
    -WorkingDirectory $dir -WindowStyle Hidden
exit 0

