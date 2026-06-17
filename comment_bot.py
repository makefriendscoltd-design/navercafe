# -*- coding: utf-8 -*-
"""
네이버 카페 가입인사 게시판 자동 댓글 봇 (독립 실행 스크립트)

특정 게시판(예: 가입인사)을 N초마다 감시하여, 봇 시작 이후 새로 올라온 글에
미리 지정한 여러 문구 중 하나를 랜덤으로 댓글로 작성한다.

- 로그인 계정 / 카페 정보는 config.ini 의 [NAVER] 섹션을 재사용한다.
- 봇 설정은 config.ini 의 [COMMENT_BOT] 섹션을 사용한다 (없으면 자동 생성).
- 이미 처리한 글 ID 는 comment_bot_seen.json 에 저장하여 재시작해도 중복 댓글을 막는다.

실행:
    python comment_bot.py              # 상시 감시 모드 (기준선 설정 후 새 글 자동 댓글)
    python comment_bot.py inspect      # 게시판 최신 글에서 댓글창/등록버튼만 탐색 (작성 X, 셀렉터 검증용)
    python comment_bot.py inspect 123  # 특정 글 ID(123)에서 댓글창 탐색 (작성 X)
    python comment_bot.py test 123     # 특정 글 ID(123)에 실제로 테스트 댓글 1회 작성

중지: 터미널에서 Ctrl+C
"""

import configparser
import datetime
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request

# Windows cp949 콘솔에서 em-dash/이모지 등 출력 시 크래시 방지 → stdout/stderr 를 utf-8 로
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 의존성 검사 ──────────────────────────────────────────────
try:
    import pyperclip
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains
except ImportError as e:
    print("[오류] 필요한 패키지가 없습니다. 아래 명령으로 설치하세요:")
    print("  pip install selenium pyperclip")
    print(f"  (상세: {e})")
    sys.exit(1)

CONFIG_PATH = "config.ini"
SEEN_PATH = "comment_bot_seen.json"
WEEKLY_STATE_PATH = "comment_bot_weekly.json"  # 마지막으로 주간 회원증가 보고한 ISO 주차
# 쿠키/세션을 저장할 전용 크롬 프로필 폴더 (첫 로그인 후 재사용 → 캡차/재로그인 방지)
PROFILE_DIR = os.path.abspath("chrome_profile_commentbot")
LOCK_PATH = os.path.abspath("comment_bot_watch.lock")  # watch 모드 단일 인스턴스 락

# 락 파일 핸들은 프로세스 수명 동안 열어둬야 잠금이 유지됨 (GC 방지용 전역 보관)
_LOCK_FH = None


def acquire_watch_lock():
    """watch(상시 감시) 모드 중복 실행을 막는다.

    같은 크롬 프로필(chrome_profile_commentbot)·seen.json 을 여러 봇이 동시에
    건드리면 프로필 잠금 충돌로 서로 죽는 악순환이 생긴다. Windows msvcrt 파일락은
    프로세스가 죽으면 OS 가 자동으로 잠금을 해제하므로 stale lock 걱정이 없다.

    이미 살아있는 watch 인스턴스가 있으면 False 를 반환한다(잠금 실패).
    """
    global _LOCK_FH
    try:
        import msvcrt
        _LOCK_FH = open(LOCK_PATH, "w")
        msvcrt.locking(_LOCK_FH.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            _LOCK_FH.write(str(os.getpid()))
            _LOCK_FH.flush()
        except Exception:
            pass
        return True
    except OSError:
        # 다른 인스턴스가 이미 잠금 보유 중
        try:
            if _LOCK_FH:
                _LOCK_FH.close()
        except Exception:
            pass
        _LOCK_FH = None
        return False
    except Exception:
        # msvcrt 부재 등 예외 상황에서는 봇 동작을 막지 않는다
        return True

# 기본 댓글 문구 (config 에 없을 때 생성되는 샘플)
DEFAULT_COMMENT_TEXTS = [
    "가입을 진심으로 환영합니다! 앞으로 자주 뵈어요 :)",
    "반갑습니다~ 좋은 정보 많이 나누면서 함께해요!",
    "환영합니다! 궁금한 점 있으면 편하게 물어보세요 :)",
    "어서오세요~ 활동 기대하겠습니다!",
]


# ===================================================================
# 1. 설정 로드 / 생성
# ===================================================================
def load_config():
    """config.ini 를 읽고 [COMMENT_BOT] 섹션이 없으면 샘플을 추가한다."""
    if not os.path.exists(CONFIG_PATH):
        print(f"[오류] {CONFIG_PATH} 가 없습니다. 먼저 youtube_cafe_auto.py 를 한 번 실행해 "
              "네이버 계정 설정을 만들어 주세요.")
        sys.exit(1)

    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG_PATH, encoding="utf-8")

    if not config.has_section("NAVER"):
        print("[오류] config.ini 에 [NAVER] 섹션이 없습니다. 네이버 계정 설정이 필요합니다.")
        sys.exit(1)

    created = False
    if not config.has_section("COMMENT_BOT"):
        config.add_section("COMMENT_BOT")
        created = True

    # 기본값 보강
    defaults = {
        "board_url": "",
        "poll_interval_sec": "120",
        "min_action_delay": "3",
        "max_action_delay": "8",
    }
    for key, val in defaults.items():
        if not config.has_option("COMMENT_BOT", key):
            config.set("COMMENT_BOT", key, val)
            created = True

    # 댓글 문구가 하나도 없으면 샘플 comment_1 을 시드한다.
    has_any_comment = (config.has_option("COMMENT_BOT", "comment_1")
                       or config.has_option("COMMENT_BOT", "comment_texts"))
    if not has_any_comment:
        for i, txt in enumerate(DEFAULT_COMMENT_TEXTS, 1):
            config.set("COMMENT_BOT", f"comment_{i}", txt)
        created = True

    if created:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            config.write(f)
        print("[설정] config.ini 에 [COMMENT_BOT] 섹션을 추가했습니다.")

    return config


def parse_comment_texts(raw):
    """여러 줄 또는 || 구분 문자열을 문구 리스트로 변환한다."""
    if not raw:
        return []
    if "||" in raw:
        parts = raw.split("||")
    else:
        parts = raw.splitlines()
    return [p.strip() for p in parts if p.strip()]


def load_comment_texts(config):
    """댓글 문구를 로드한다.

    우선순위:
    1) comment_1, comment_2, ... 번호별 옵션 (URL/줄바꿈 포함 문구에 권장).
       문구 안의 리터럴 '\\n' 은 실제 줄바꿈으로 변환된다.
    2) 없으면 comment_texts (여러 줄 또는 || 구분) 를 사용.
    """
    numbered = []
    idx = 1
    while config.has_option("COMMENT_BOT", f"comment_{idx}"):
        val = config.get("COMMENT_BOT", f"comment_{idx}", fallback="").strip()
        if val:
            numbered.append(val.replace("\\n", "\n"))
        idx += 1
    if numbered:
        return numbered

    raw = config.get("COMMENT_BOT", "comment_texts", fallback="")
    return [t.replace("\\n", "\n") for t in parse_comment_texts(raw)]


def parse_club_menu(url):
    """카페 URL 에서 clubid, menuid 를 추출한다 (신/구버전 모두 지원)."""
    decoded = urllib.parse.unquote(url or "")
    club_match = re.search(r"clubid[=&](\d+)|cafes/(\d+)", decoded)
    menu_match = re.search(r"menuid[=&](\d+)|menus/(\d+)", decoded)
    clubid = (club_match.group(1) or club_match.group(2)) if club_match else None
    menuid = (menu_match.group(1) or menu_match.group(2)) if menu_match else None
    return clubid, menuid


# ===================================================================
# 2. 처리한 글 ID 영속화
# ===================================================================
def load_seen():
    if os.path.exists(SEEN_PATH):
        try:
            with open(SEEN_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(str(x) for x in data.get("seen", []))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    try:
        with open(SEEN_PATH, "w", encoding="utf-8") as f:
            json.dump({"seen": sorted(seen, key=lambda x: int(x) if x.isdigit() else 0)},
                      f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [경고] 상태 저장 실패: {e}")


def load_last_weekly():
    """마지막으로 주간 회원증가 보고한 (연도, ISO주차). 없으면 None."""
    if os.path.exists(WEEKLY_STATE_PATH):
        try:
            with open(WEEKLY_STATE_PATH, "r", encoding="utf-8") as f:
                w = json.load(f).get("last_week")
            return tuple(w) if w else None
        except Exception:
            return None
    return None


def save_last_weekly(week_tuple):
    try:
        with open(WEEKLY_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"last_week": list(week_tuple)}, f, ensure_ascii=False)
    except Exception as e:
        print(f"  [경고] 주간보고 상태 저장 실패: {e}")


# ===================================================================
# 3. 드라이버 생성 / 로그인
# ===================================================================
def _kill_orphan_profile_chrome():
    """commentbot 프로필을 점유 중인 고아 크롬을 종료한다 (자가치유).

    봇이 비정상 종료되면 자기가 띄운 크롬이 살아남아 PROFILE_DIR 을 잠그고,
    다음 실행 때 "Chrome instance exited" 로 봇이 죽는 무한 사망 루프의 원인이 된다.
    드라이버 생성 직전 이 좀비를 제거해 어떤 경로(watchdog/수동)로 떠도 자가복구되게 한다.
    ※ 사용자의 일반 크롬은 건드리지 않는다 — commentbot 프로필을 쓰는 프로세스만 종료.
    """
    if os.name != "nt":
        return
    import subprocess
    profile_name = os.path.basename(PROFILE_DIR)  # chrome_profile_commentbot
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -like '*" + profile_name + "*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            timeout=20, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        print(f"  [경고] 좀비 크롬 정리 실패(무시하고 진행): {e}")


def make_driver():
    """전용 프로필을 사용하는 Chrome 드라이버를 생성한다.

    프로필 폴더에 쿠키/세션이 저장되므로, 첫 로그인 이후에는 재로그인/캡차가 거의 없다.
    또한 자동화 탐지 우회 플래그로 캡차 발생 확률을 낮춘다.
    """
    # 이전 실행이 남긴 고아 크롬이 프로필을 잠그면 드라이버 생성이 실패하므로 먼저 정리한다.
    _kill_orphan_profile_chrome()
    options = webdriver.ChromeOptions()
    options.add_argument("--window-size=1000,800")
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=options)
    # navigator.webdriver 흔적 제거
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        })
    except Exception:
        pass
    return driver


def send_telegram(token, chat_id, text, thread_id=None):
    """텔레그램 봇으로 메시지를 전송한다. 성공 시 True."""
    if not token or not chat_id:
        return False
    try:
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
        if thread_id:
            payload["message_thread_id"] = thread_id
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        print(f"  [텔레그램] 전송 실패: {e}")
        return False


def _format_daily_report(events, period_date):
    """일일 보고 메시지를 만든다. events=[(시각, aid, title, 성공여부), ...]"""
    ok = [e for e in events if e[3]]
    fail = [e for e in events if not e[3]]
    lines = [f"📋 카페 댓글봇 일일 보고 ({period_date})",
             f"가입인사 새 글 {len(events)}건 / 댓글 성공 {len(ok)} · 실패 {len(fail)}"]
    if events:
        lines.append("")
        for t, aid, title, success in events:
            mark = "✅" if success else "❌"
            lines.append(f"{mark} {t} | {title[:24]} (#{aid})")
    else:
        lines.append("(새 가입인사 글 없음 — 정상 작동 중)")
    return "\n".join(lines)


# ===================================================================
# 카페 통계 스크래핑 (cafe.stat.naver.com)
# ===================================================================
def _stat_daily_value(driver, url, date_str, wait=8):
    """통계 일별 표(방문자 조회수 목록)에서 date_str 행의 첫 숫자를 반환. 실패 시 None.

    표는 'YYYY.MM.DD.(요일)\\t숫자\\t...' 형태의 행으로 렌더된다.
    """
    driver.get(url)
    time.sleep(wait)
    txt = driver.execute_script("return document.body ? document.body.innerText : '';") or ""
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith(date_str):
            nums = re.findall(r"[\d,]+", line[len(date_str):])
            if nums:
                try:
                    return int(nums[0].replace(",", ""))
                except ValueError:
                    return None
    return None


def _stat_top_posts(driver, clubid, n=3, wait=9):
    """게시글 순위(/rank/article, 어제 기본)에서 조회수 TOP n = [(순위, 제목, 게시판, 조회수)]."""
    driver.get(f"https://cafe.stat.naver.com/cafe/{clubid}/rank/article")
    time.sleep(wait)
    txt = driver.execute_script("return document.body ? document.body.innerText : '';") or ""
    posts = []
    for line in txt.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) >= 6 and parts[0].isdigit():
            views = re.sub(r"[^\d]", "", parts[-1])
            posts.append((int(parts[0]), parts[1], parts[2], int(views) if views else 0))
            if len(posts) >= n:
                break
    return posts


def fetch_cafe_stats(driver, clubid, target_date):
    """어제(target_date) 카페 전체 통계: 방문자수 / 전체 조회수 / 조회수 TOP3."""
    ds = target_date.strftime("%Y.%m.%d.")  # 예: '2026.06.10.'
    base = f"https://cafe.stat.naver.com/cafe/{clubid}"
    visitors = _stat_daily_value(driver, f"{base}/visit/uv", ds)        # 순방문자수
    views = _stat_daily_value(driver, f"{base}/visit/cv", ds)            # 전체 조회수
    top = _stat_top_posts(driver, clubid, n=3)
    return {"date": ds, "visitors": visitors, "views": views, "top": top}


def _format_stats(stats):
    """통계를 텔레그램 보고용 텍스트로."""
    mmdd = stats["date"][5:].rstrip(".")  # 'MM.DD'
    lines = ["", f"📊 카페 통계 (어제 {mmdd})"]
    v, w = stats.get("visitors"), stats.get("views")
    lines.append(f"👥 방문자 {v:,}명" if v is not None else "👥 방문자 (집계 실패)")
    lines.append(f"👁 조회수 {w:,}회" if w is not None else "👁 조회수 (집계 실패)")
    if stats.get("top"):
        lines.append("🔥 조회수 TOP3")
        for rank, title, board, vw in stats["top"]:
            lines.append(f" {rank}. {title[:24]} ({vw:,}회·{board})")
    return "\n".join(lines)


# ===================================================================
# 주간 회원증가 집계 (전체 멤버 관리 = ManageWholeMember, 가입일 내림차순)
# ===================================================================
_MEMBER_DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\.")


def _member_join_rows(driver):
    """현재 페이지의 멤버 행에서 가입일(행의 첫 날짜)만 추출 → [date, ...].

    각 행: `별명 (아이디) 등급 가입일 최종방문일 ...` → 첫 YYYY.MM.DD. 가 가입일.
    """
    texts = driver.execute_script(
        "var o=[];var t=document.querySelectorAll('table tr');"
        "for(var i=0;i<t.length;i++){var x=(t[i].innerText||'').replace(/\\s+/g,' ').trim();"
        "if(x)o.push(x);}return o;") or []
    out = []
    for x in texts:
        if not re.match(r"^.+?\([A-Za-z0-9_\-]{2,40}\)", x):
            continue  # 멤버 행(별명(아이디)) 아니면 스킵
        m = _MEMBER_DATE_RE.search(x)
        if m:
            out.append(datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return out


def _member_next_page(driver, cur_page):
    """#paginate 에서 cur_page+1 로 이동(없으면 '다음 10개'). 성공 True."""
    target = str(cur_page + 1)
    clicked = driver.execute_script(
        "var t=arguments[0],box=document.querySelector('#paginate');"
        "if(!box)return 'nobox';var as=box.querySelectorAll('a');"
        "for(var i=0;i<as.length;i++){if((as[i].textContent||'').trim()===t){as[i].click();return 'num';}}"
        "var nx=box.querySelector('a.next');if(nx){nx.click();return 'next';}return 'end';", target)
    if clicked in ("end", "nobox"):
        return False
    deadline = time.time() + 8
    while time.time() < deadline:
        on = driver.execute_script(
            "var o=document.querySelector('#paginate a.on');return o?o.textContent.trim():'';") or ""
        if on == target:
            time.sleep(0.6)
            return True
        time.sleep(0.3)
    return False


def count_member_joins(driver, clubid, start_date, end_date):
    """전체 멤버 관리(가입일 desc)를 순회하며 start~end 가입 회원수를 센다.

    가입일이 start_date 이전으로 내려가면 즉시 중단(정렬이 내림차순이므로).
    반환: (총합, {date: count}).
    """
    driver.get(f"https://cafe.naver.com/ManageWholeMember.nhn?clubid={clubid}")
    time.sleep(4)
    per_day = {}
    page = 1
    while page < 400:
        rows = _member_join_rows(driver)
        if not rows:
            break
        for joined in rows:
            if start_date <= joined <= end_date:
                per_day[joined] = per_day.get(joined, 0) + 1
        if min(rows) < start_date:
            break
        if not _member_next_page(driver, page):
            break
        page += 1
    return sum(per_day.values()), per_day


def _format_weekly_growth(total, per_day, start_date, end_date):
    """주간 회원증가를 텔레그램 보고 텍스트로."""
    wd = ["월", "화", "수", "목", "금", "토", "일"]
    head = f"{start_date.strftime('%m.%d')}~{end_date.strftime('%m.%d')}"
    lines = ["", f"🧑‍🤝‍🧑 주간 회원 증가 ({head})", f"▶ 신규 가입 {total:,}명"]
    d = start_date
    while d <= end_date:
        lines.append(f"  {d.strftime('%m.%d')}({wd[d.weekday()]}) {per_day.get(d, 0)}명")
        d += datetime.timedelta(days=1)
    return "\n".join(lines)


def is_logged_in(driver):
    """저장된 세션으로 이미 로그인되어 있는지 확인한다."""
    try:
        driver.get("https://www.naver.com")
        time.sleep(2)
        # NID_AUT / NID_SES 쿠키가 있으면 로그인 상태 (가장 확실한 판별)
        cookies = {c["name"] for c in driver.get_cookies()}
        return "NID_AUT" in cookies or "NID_SES" in cookies
    except Exception:
        return False


def naver_login(driver, naver_id, naver_pw):
    """nid.naver.com 로그인. 저장된 세션이 있으면 건너뛰고, 캡챠/2차 인증 시 수동 처리를 기다린다."""
    # 저장된 프로필로 이미 로그인되어 있으면 스킵
    if is_logged_in(driver):
        print("[로그인] 저장된 세션으로 이미 로그인됨 (캡차/재입력 불필요).")
        return True

    print("[로그인] 네이버 로그인 페이지로 이동...")
    driver.get("https://nid.naver.com/nidlogin.login")
    time.sleep(2)

    # 이미 로그인된 상태로 리다이렉트되면 스킵
    if "nidlogin" not in driver.current_url:
        print("[로그인] 이미 로그인 상태입니다.")
        return True

    pyperclip.copy(naver_id)
    driver.find_element(By.CSS_SELECTOR, "#id").click()
    driver.find_element(By.CSS_SELECTOR, "#id").send_keys(Keys.CONTROL, "v")
    time.sleep(1)

    pyperclip.copy(naver_pw)
    driver.find_element(By.CSS_SELECTOR, "#pw").click()
    driver.find_element(By.CSS_SELECTOR, "#pw").send_keys(Keys.CONTROL, "v")
    time.sleep(1)

    # '로그인 상태 유지' 체크 → 세션 쿠키 장기 유지 (무인 운영 시 재로그인 방지)
    try:
        keep = driver.execute_script("""
            var k = document.querySelector('#keep, input[id="keep"], .keep_check input');
            if (k && !k.checked) {
                (k.closest('label') || k).click();
                return k.checked;
            }
            return k ? k.checked : 'no-checkbox';
        """)
        print(f"[로그인] '로그인 상태 유지' 설정: {keep}")
    except Exception:
        pass

    driver.find_element(By.CSS_SELECTOR, "#log\\.login").click()
    time.sleep(3)

    # 로그인 페이지를 벗어났는지 확인 (캡챠/2차 인증 시 수동 대기)
    for attempt in range(240):  # 최대 4분
        current = driver.current_url
        if "nidlogin" not in current and "login" not in current.split("/")[-1]:
            print("[로그인] 성공.")
            return True
        if attempt == 5:
            print("[로그인] 자동 로그인이 지연됩니다. 캡챠/보안인증이 있으면 직접 완료해주세요...")
        time.sleep(1)

    raise RuntimeError("로그인 실패: 4분 내에 로그인이 완료되지 않았습니다.")


# ===================================================================
# 4. 게시판 글 목록 읽기
# ===================================================================
def fetch_article_ids(driver, board_url):
    """게시판 페이지를 열어 글 ID 목록을 반환한다. [(article_id, title), ...]"""
    driver.get(board_url)
    _wait_for_article_rows(driver)  # SPA 렌더 대기 (글 행이 나타날 때까지 폴링)

    # 글 목록의 article 링크를 JS 로 수집 (신버전 f-e: /articles/<id>)
    # 공지(tr.board-notice / .board-notice 안의 링크)는 제외한다.
    items = driver.execute_script("""
        var out = [];
        var seen = {};
        var links = document.querySelectorAll('a[href*="/articles/"], a[href*="articleid="]');
        for (var i = 0; i < links.length; i++) {
            var a = links[i];
            // 목록 표의 행(tr) 안에 있는 링크만 대상으로 (사이드바/추천위젯 제외)
            var tr = a.closest ? a.closest('tr') : null;
            if (!tr) continue;
            // 공지 행 제외
            if (/board-notice/.test(tr.className || '')) continue;
            if (a.closest && a.closest('.board-notice')) continue;
            var href = a.href || '';
            var m = href.match(/articles\\/(\\d+)/) || href.match(/articleid=(\\d+)/);
            if (!m) continue;
            var id = m[1];
            if (seen[id]) continue;
            var title = (a.textContent || '').trim();
            if (!title) continue;  // 제목 없는 링크(썸네일 등) 제외
            seen[id] = 1;
            out.push({id: id, title: title});
        }
        return out;
    """)
    return [(str(it["id"]), it.get("title", "")) for it in (items or [])]


def _parse_list_date(text, today):
    """네이버 카페 목록의 날짜 문자열을 date 로 변환한다.

    - 'HH:MM'        → 오늘 (today)
    - 'MM.DD.'       → 올해 MM월 DD일
    - 'YYYY.MM.DD.'  → 그대로
    파싱 실패 시 None.
    """
    text = (text or "").strip()
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})\.?$", text)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.?$", text)
    if m:
        try:
            return datetime.date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    if re.match(r"^\d{1,2}:\d{2}$", text):  # 시간만 표시 = 오늘
        return today
    return None


def _wait_for_article_rows(driver, timeout=14):
    """게시판 목록의 글 행(tr 안의 articles 링크)이 렌더될 때까지 폴링한다."""
    deadline = time.time() + timeout
    last = 0
    while time.time() < deadline:
        try:
            n = driver.execute_script("""
                var links = document.querySelectorAll('a[href*="/articles/"], a[href*="articleid="]');
                var c = 0;
                for (var i = 0; i < links.length; i++) {
                    var tr = links[i].closest ? links[i].closest('tr') : null;
                    if (tr && !/board-notice/.test(tr.className || '')) c++;
                }
                return c;
            """)
        except Exception:
            n = 0
        if n and n == last and n > 0:
            return n  # 두 번 연속 동일 → 렌더 안정
        last = n
        time.sleep(1.0)
    return last


def fetch_articles_with_dates(driver, board_url, today, page=1):
    """게시판 목록(page 페이지)에서 공지 제외 글의 (id, title, date) 를 수집한다.

    각 글 행(tr)의 셀들 중 날짜 형식인 셀을 찾아 날짜를 파싱한다.
    반환: [{'id','title','date_text','date'}, ...]  (date 는 date 객체 또는 None)
    """
    url = board_url
    if page > 1:
        sep = "&" if "?" in board_url else "?"
        url = f"{board_url}{sep}page={page}"
    driver.get(url)
    _wait_for_article_rows(driver)
    rows = driver.execute_script(r"""
        var out = [];
        var seen = {};
        var links = document.querySelectorAll('a[href*="/articles/"], a[href*="articleid="]');
        for (var i = 0; i < links.length; i++) {
            var a = links[i];
            var tr = a.closest ? a.closest('tr') : null;
            if (!tr) continue;
            if (/board-notice/.test(tr.className || '')) continue;
            if (a.closest && a.closest('.board-notice')) continue;
            var href = a.href || '';
            var m = href.match(/articles\/(\d+)/) || href.match(/articleid=(\d+)/);
            if (!m) continue;
            var id = m[1];
            if (seen[id]) continue;
            var title = (a.textContent || '').trim();
            if (!title) continue;
            seen[id] = 1;
            // 행의 셀 텍스트들 수집 (날짜 셀 탐색용)
            var cells = tr.querySelectorAll('td, .td_date, .date');
            var cellTexts = Array.prototype.map.call(cells, function(c){ return (c.innerText||'').trim(); });
            out.push({id: id, title: title, cells: cellTexts});
        }
        return out;
    """)
    result = []
    for it in (rows or []):
        date_text, date_val = "", None
        for ctext in (it.get("cells") or []):
            d = _parse_list_date(ctext, today)
            if d:
                date_text, date_val = ctext, d
                break
        result.append({"id": str(it["id"]), "title": it.get("title", ""),
                       "date_text": date_text, "date": date_val})
    return result


def build_article_url(clubid, article_id, board_url):
    """글 상세 페이지 URL 생성 (신버전 f-e 우선)."""
    if "/f-e/" in board_url or "/cafes/" in board_url:
        return f"https://cafe.naver.com/f-e/cafes/{clubid}/articles/{article_id}?fromList=true"
    # 구버전 fallback
    return (f"https://cafe.naver.com/ArticleRead.nhn?"
            f"clubid={clubid}&articleid={article_id}")


def enter_cafe_iframe(driver):
    """카페 콘텐츠가 cafe_main iframe 안에 있으면 그 안으로 전환한다. 전환 성공 시 True."""
    driver.switch_to.default_content()
    try:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe#cafe_main, iframe[name='cafe_main']")
        if frames:
            driver.switch_to.frame(frames[0])
            return True
    except Exception:
        pass
    return False


def _diagnose(driver, label):
    """현재 (또는 iframe 내부) 페이지 구조를 자세히 출력한다 — 셀렉터 파악용."""
    info = driver.execute_script(r"""
        function cls(e){ return (e.className && e.className.toString ? e.className.toString() : '(noclass)').slice(0,70); }
        var r = {};
        r.url = location.href;
        r.title = document.title;
        var ifr = document.querySelectorAll('iframe');
        r.iframes = Array.prototype.map.call(ifr, function(f){ return (f.id||f.name||'?')+':'+(f.src||'').slice(0,60); });
        var tas = document.querySelectorAll('textarea');
        r.textareas = Array.prototype.map.call(tas, function(t){ return cls(t)+' | ph='+(t.getAttribute('placeholder')||''); });
        var ce = document.querySelectorAll('[contenteditable="true"]');
        r.editables = Array.prototype.map.call(ce, function(e){ return cls(e)+' | ph='+(e.getAttribute('placeholder')||e.getAttribute('data-placeholder')||''); });
        var commentEls = document.querySelectorAll('[class*="omment"]');
        r.commentClasses = Array.prototype.slice.call(commentEls, 0, 12).map(function(e){ return e.tagName+'.'+cls(e); });
        // '공지' 마커가 들어간 요소 클래스 (목록 페이지에서 공지 식별용)
        var notices = document.querySelectorAll('[class*="notice"], [class*="Notice"], .board-notice');
        r.noticeClasses = Array.prototype.slice.call(notices, 0, 8).map(function(e){ return e.tagName+'.'+cls(e); });
        r.bodyHasDaetgeul = document.body ? document.body.innerText.indexOf('댓글') !== -1 : false;
        // 등록 버튼 후보
        var btns = document.querySelectorAll('button, a[role="button"]');
        r.registerBtns = Array.prototype.slice.call(btns, 0).filter(function(b){
            var t=(b.textContent||'').trim(); return t==='등록'||t==='댓글등록'||t==='입력';
        }).slice(0,5).map(function(b){ return b.tagName+'.'+cls(b)+' ="'+(b.textContent||'').trim()+'"'; });
        return r;
    """)
    print(f"\n  ===== 진단: {label} =====")
    print(f"  URL: {info.get('url')}")
    print(f"  title: {info.get('title')}")
    print(f"  iframe({len(info.get('iframes') or [])}): {info.get('iframes')}")
    print(f"  textarea({len(info.get('textareas') or [])}): {info.get('textareas')}")
    print(f"  contenteditable({len(info.get('editables') or [])}): {info.get('editables')}")
    print(f"  'omment' 클래스 요소: {info.get('commentClasses')}")
    print(f"  공지(notice) 클래스 요소: {info.get('noticeClasses')}")
    print(f"  등록버튼 후보: {info.get('registerBtns')}")
    print(f"  본문에 '댓글' 텍스트: {info.get('bodyHasDaetgeul')}")
    print(f"  =========================\n")
    return info


# ===================================================================
# 5. 댓글 작성
# ===================================================================
def _rand_delay(cfg_min, cfg_max):
    time.sleep(random.uniform(cfg_min, cfg_max))


def _probe_paste(driver, clubid, article_id, text, board_url):
    """댓글창에 붙여넣기만 하고 textarea 값을 읽어 줄바꿈 보존 여부를 진단한다 (등록 X)."""
    url = build_article_url(clubid, article_id, board_url)
    driver.get(url)
    time.sleep(4)
    if not enter_cafe_iframe(driver):
        print("[PROBE] iframe 전환 실패")
        return
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)

        # ── 이미 달린 댓글들의 저장된 HTML 덤프 (제출 후 줄바꿈 보존 여부 확인) ──
        dumps = driver.execute_script(r"""
            var items = document.querySelectorAll('.comment_text_view, .comment_text_box, li[class*="CommentItem"], .comment_list_area li');
            var out = [];
            for (var i = 0; i < items.length && out.length < 8; i++) {
                var t = (items[i].innerText || '');
                if (t.indexOf('유료강의') !== -1 || t.indexOf('http') !== -1) {
                    out.push({txt: t.slice(0,120), html: items[i].innerHTML.slice(0, 600)});
                }
            }
            return out;
        """)
        print(f"[PROBE] 기존 댓글 중 URL/테스트 포함 {len(dumps or [])}개:")
        for d in (dumps or []):
            print(f"  innerText repr: {d['txt']!r}")
            print(f"  innerHTML: {d['html']}")
            print("  ----")

        box = driver.find_element(By.CSS_SELECTOR, "textarea.comment_inbox_text")
        box.click()
        time.sleep(0.4)
        print(f"[PROBE] 원본 text repr: {text!r}")

        # 방법1: 클립보드 Ctrl+V
        pyperclip.copy(text)
        ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
        time.sleep(0.8)
        val1 = box.get_attribute("value")
        print(f"[PROBE] Ctrl+V 후 value repr: {val1!r}")
        print(f"[PROBE] Ctrl+V value 에 줄바꿈 포함: {chr(10) in (val1 or '')}")

        # 입력창 비우기
        box.click(); time.sleep(0.2)
        ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).perform()
        ActionChains(driver).send_keys(Keys.DELETE).perform()
        time.sleep(0.5)

        # 방법2: JS 로 value 직접 설정 + input 이벤트
        driver.execute_script("""
            var ta = arguments[0], v = arguments[1];
            var setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
            setter.call(ta, v);
            ta.dispatchEvent(new Event('input', {bubbles:true}));
        """, box, text)
        time.sleep(0.8)
        val2 = box.get_attribute("value")
        print(f"[PROBE] JS-set 후 value repr: {val2!r}")
        print(f"[PROBE] JS-set value 에 줄바꿈 포함: {chr(10) in (val2 or '')}")
    except Exception as e:
        print(f"[PROBE] 오류: {e}")
    finally:
        driver.switch_to.default_content()


def is_notice_article(driver):
    """현재 글 상세 페이지(iframe 내부)가 공지글이면 True. (badge_notice 배지로 판별)"""
    try:
        return bool(driver.execute_script(
            "return !!document.querySelector('.badge_notice, .title_notice');"
        ))
    except Exception:
        return False


def _comment_signatures(comment_texts):
    """봇이 다는 댓글에서 '이미 단 댓글'을 식별할 고유 문자열(주로 CTA URL)을 추출한다."""
    sigs = set()
    for t in comment_texts:
        for u in re.findall(r"https?://[^\s]+", t):
            sigs.add(u.strip().rstrip(").,"))
    return [s for s in sigs if len(s) >= 8]


def _already_commented(driver, signatures):
    """현재 글(이미 cafe_main iframe 진입 상태)의 댓글 영역에 봇 시그니처가 있으면 True.

    backfill 을 seen 무시 모드로 돌릴 때, 이미 내 댓글이 달린 글에 중복으로 다는 것을 막는다.
    """
    if not signatures:
        return False
    txt = driver.execute_script("""
        var sel = '.comment_text_view, .comment_text_box, .comment_list_area,'
                + ' li[class*=CommentItem], [class*=comment]';
        var els = document.querySelectorAll(sel);
        var s = '';
        for (var i = 0; i < els.length; i++) { s += (els[i].innerText || '') + '\\n'; }
        return s;
    """) or ""
    return any(sig in txt for sig in signatures)


def write_comment(driver, clubid, article_id, text, board_url, delay_range, dry_run=False,
                  skip_if_commented=False, signatures=None):
    """글 상세 페이지로 이동해 댓글을 작성한다. 성공 시 True.

    네이버 신버전 카페는 글 본문/댓글이 cafe_main iframe 안에 있으므로 iframe 으로 전환한다.
    - 댓글 입력창: textarea.comment_inbox_text
    - 등록 버튼: a.btn_register (텍스트 '등록')
    dry_run=True 이면 탐색 결과만 보고하고 실제 작성/등록은 하지 않는다.
    """
    url = build_article_url(clubid, article_id, board_url)
    print(f"  -> 글 {article_id} 열기 {'(DRY-RUN)' if dry_run else ''}")
    driver.get(url)
    time.sleep(4)

    # 콘텐츠가 들어있는 cafe_main iframe 으로 전환
    if not enter_cafe_iframe(driver):
        print(f"  [실패] 글 {article_id}: cafe_main iframe 을 찾지 못했습니다.")
        driver.switch_to.default_content()
        return False

    try:
        # 공지글이면 건너뛴다 (이중 안전장치)
        if is_notice_article(driver):
            print(f"  [건너뜀] 글 {article_id}: 공지글이라 댓글을 달지 않습니다.")
            return False

        # 댓글 영역이 로드되도록 하단까지 스크롤
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)

        # (backfill seen-무시 모드) 이미 내 댓글이 있으면 중복 작성 방지
        if skip_if_commented and _already_commented(driver, signatures):
            print(f"  [건너뜀] 글 {article_id}: 이미 내 댓글이 있어 다시 달지 않습니다.")
            return True

        # 댓글 입력창 탐색
        try:
            box = driver.find_element(By.CSS_SELECTOR, "textarea.comment_inbox_text")
        except Exception:
            box = None
        if not box:
            # placeholder 기반 fallback
            box = driver.execute_script(
                "var t=document.querySelectorAll('textarea');"
                "for(var i=0;i<t.length;i++){if((t[i].getAttribute('placeholder')||'')"
                ".indexOf('댓글')!==-1)return t[i];}return null;")
        if not box:
            print(f"  [실패] 글 {article_id}: 댓글 입력창(textarea.comment_inbox_text)을 찾지 못했습니다.")
            return False

        if dry_run:
            has_btn = bool(driver.execute_script(
                "var c=document.querySelectorAll('a.btn_register, .btn_register');"
                "for(var i=0;i<c.length;i++){if((c[i].textContent||'').trim()==='등록')return true;}"
                "return c.length>0;"))
            print(f"  [DRY-RUN] 글 {article_id}: 입력창 발견 / 등록버튼 {'발견' if has_btn else '못 찾음'}. "
                  "실제 작성은 하지 않았습니다.")
            return has_btn

        # 입력창 클릭 후 클립보드 붙여넣기
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
            time.sleep(0.5)
            box.click()
            time.sleep(0.5)
        except Exception:
            driver.execute_script("arguments[0].focus();", box)
            time.sleep(0.3)

        pyperclip.copy(text)
        ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
        time.sleep(1.0)

        # 등록 버튼 클릭 (텍스트 '등록' 인 a.btn_register 우선)
        clicked = driver.execute_script("""
            var c = document.querySelectorAll('a.btn_register, .btn_register, button.btn_register');
            for (var i = 0; i < c.length; i++) {
                if ((c[i].textContent || '').trim() === '등록') { c[i].click(); return true; }
            }
            if (c.length) { c[0].click(); return true; }
            return false;
        """)
        if not clicked:
            print(f"  [실패] 글 {article_id}: 등록 버튼(a.btn_register)을 찾지 못했습니다.")
            return False

        time.sleep(2.0)
        print(f"  [성공] 글 {article_id} 댓글 등록: \"{text[:25].replace(chr(10),' ')}...\"")
        _rand_delay(*delay_range)
        return True
    finally:
        driver.switch_to.default_content()


# ===================================================================
# 6-B. 백필 모드 — 특정 날짜 이후 기존 글에 소급 댓글
# ===================================================================
def _run_backfill(naver_id, naver_pw, clubid, board_url, comment_texts,
                  delay_range, cutoff_date, do_post, ignore_seen=False):
    """cutoff_date(포함) 이후의 기존 가입인사 글에 하나씩 댓글을 단다.

    do_post=False 이면 대상만 출력하는 미리보기. True 이면 실제 작성.
    기본적으로 comment_bot_seen.json 에 있는 글은 건너뛴다.
    ignore_seen=True 이면 seen 을 무시하고 다시 훑되, 글마다 '이미 내 댓글이 있는지'
    확인해서 중복 작성을 막는다. (재시작 시 잘못 seen 처리된 밀린 글 복구용)
    """
    today = datetime.date.today()
    signatures = _comment_signatures(comment_texts)
    print("=" * 60)
    print(f"  [BACKFILL] {cutoff_date} 이후 글에 소급 댓글 "
          f"({'실제 작성' if do_post else '미리보기 — 작성 안 함'})")
    print(f"  (오늘={today}, 글당 딜레이={delay_range[0]:.0f}~{delay_range[1]:.0f}초"
          f"{', seen 무시+중복방지' if ignore_seen else ''})")
    print("=" * 60)

    driver = make_driver()
    seen = load_seen()
    try:
        naver_login(driver, naver_id, naver_pw)

        # ── 여러 페이지를 cutoff 이전 글이 나올 때까지 수집 ──
        articles = []
        collected_ids = set()
        MAX_PAGES = 15
        for page in range(1, MAX_PAGES + 1):
            page_arts = fetch_articles_with_dates(driver, board_url, today, page=page)
            fresh = [a for a in page_arts if a["id"] not in collected_ids]
            if not fresh:
                # 새 글이 없으면 페이지네이션 끝(또는 미작동) → 중단
                print(f"[목록] {page}페이지: 새 글 없음 → 수집 종료")
                break
            for a in fresh:
                collected_ids.add(a["id"])
            articles.extend(fresh)
            page_dates = [a["date"] for a in page_arts if a["date"]]
            oldest = min(page_dates) if page_dates else None
            print(f"[목록] {page}페이지: {len(fresh)}개 수집 (이 페이지 최古={oldest})")
            if oldest is not None and oldest < cutoff_date:
                # 이 페이지에 cutoff 이전 글이 나왔으면 경계까지 다 본 것 → 중단
                break

        print(f"[목록] 총 {len(articles)}개 수집 (공지 제외)")

        # 날짜 파싱 결과 + 필터
        targets = []
        for a in articles:
            d = a["date"]
            if d is None:
                mark = "(날짜 파싱 실패)"
            elif d < cutoff_date:
                mark = "(범위 밖)"
            elif a["id"] in seen and not ignore_seen:
                mark = "(이미 처리됨)"
            else:
                mark = ">> 대상"
                targets.append(a)
            print(f"    {a['id']} | {a['date_text'] or '?':>11} | {mark} | {a['title'][:34]}")

        targets.sort(key=lambda x: (x["date"], int(x["id"])))
        print(f"\n[대상] {cutoff_date} 이후 & 미처리 글: {len(targets)}개 (오래된 순으로 작성)")

        if not do_post:
            print("\n[미리보기] 실제로 달려면 다음을 실행하세요:")
            print(f"  python comment_bot.py backfill {cutoff_date.isoformat()} go")
            time.sleep(3)
            return

        for a in targets:
            text = random.choice(comment_texts)
            try:
                ok = write_comment(driver, clubid, a["id"], text,
                                   board_url, delay_range, dry_run=False,
                                   skip_if_commented=ignore_seen, signatures=signatures)
            except Exception as e:
                print(f"  [오류] 글 {a['id']} 작성 중 예외: {e}")
                ok = False
            seen.add(a["id"])
            save_seen(seen)
        print(f"\n[완료] 백필 종료. 처리 시도 {len(targets)}개.")
        time.sleep(3)
    except Exception as e:
        print(f"[오류] {e}")
        time.sleep(5)
    finally:
        save_seen(seen)
        try:
            driver.quit()
        except Exception:
            pass


# ===================================================================
# 6-C. 보고 모드 — 어제 통계를 지금 즉시 텔레그램 발송
# ===================================================================
def _run_report_now(naver_id, naver_pw, clubid, tg):
    today = datetime.date.today()
    yday = today - datetime.timedelta(days=1)
    print("=" * 60)
    print(f"  [REPORT] 어제({yday}) 카페 통계를 즉시 발송")
    print("=" * 60)
    driver = make_driver()
    try:
        naver_login(driver, naver_id, naver_pw)
        try:
            stats = fetch_cafe_stats(driver, clubid, yday)
        except Exception as e:
            print(f"[오류] 통계 수집 실패: {e}")
            return
        msg = (f"📋 카페 일일 보고 ({yday})  *수동 발송*\n"
               + _format_stats(stats))
        print("\n--- 전송할 내용 ---")
        print(msg)
        print("-------------------")
        if tg["enabled"]:
            ok = send_telegram(tg["token"], tg["chat_id"], msg)
            print(f"[텔레그램] 전송 {'성공' if ok else '실패'}")
        else:
            print("[텔레그램] 비활성(telegram_enabled=false) — 발송 안 함")
        time.sleep(2)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ===================================================================
# 6. 일회성 모드 (inspect / test) — 셀렉터 검증용
# ===================================================================
def _run_oneoff(naver_id, naver_pw, clubid, board_url, comment_texts,
                delay_range, mode, target_id):
    dry_run = (mode == "inspect")
    print("=" * 60)
    print(f"  [{mode.upper()} 모드] {'댓글창 탐색만 (작성 안 함)' if dry_run else '실제 테스트 댓글 1회 작성'}")
    print("=" * 60)

    driver = make_driver()
    try:
        naver_login(driver, naver_id, naver_pw)

        # 로그인 상태 쿠키 확인
        cookies = {c["name"] for c in driver.get_cookies()}
        print(f"[로그인 쿠키] NID_AUT={'있음' if 'NID_AUT' in cookies else '없음'}, "
              f"NID_SES={'있음' if 'NID_SES' in cookies else '없음'}")

        # ── 게시판 페이지 진단 ──
        print("\n[진단] 게시판 페이지 로드...")
        driver.get(board_url)
        time.sleep(5)
        _diagnose(driver, "게시판(메인 DOM)")
        if enter_cafe_iframe(driver):
            print("  >> cafe_main iframe 으로 전환됨")
            _diagnose(driver, "게시판(iframe 내부)")
        driver.switch_to.default_content()

        # ── 글 목록에서 대상 글 선정 (공지 제외된 목록) ──
        articles = fetch_article_ids(driver, board_url)
        print(f"\n[목록] 공지 제외 수집 글 {len(articles)}개 (앞 10개):")
        for aid, title in articles[:10]:
            print(f"    - {aid}: {title[:40]}")

        if not target_id:
            if not articles:
                print("[오류] 게시판에서 (공지 제외) 글을 찾지 못했습니다.")
                return
            target_id = articles[0][0]
        print(f"\n[대상 글] ID = {target_id}")

        if mode == "probe":
            # 붙여넣기만 하고 textarea 값을 읽어 줄바꿈 보존 여부 확인 (등록 X)
            _probe_paste(driver, clubid, str(target_id), comment_texts[1] if len(comment_texts) > 1 else comment_texts[0], board_url)
        elif mode == "test":
            # 실제로 테스트 댓글 1회 작성
            text = random.choice(comment_texts)
            print(f"[TEST] 글 {target_id} 에 실제 댓글을 작성합니다:\n  \"{text[:40]}...\"")
            write_comment(driver, clubid, str(target_id), text,
                          board_url, delay_range, dry_run=False)
        else:
            # inspect: 글 상세 페이지 진단 (작성 안 함)
            url = build_article_url(clubid, str(target_id), board_url)
            print(f"[진단] 글 상세 페이지 로드: {url}")
            driver.get(url)
            time.sleep(5)
            _diagnose(driver, "글 상세(메인 DOM)")
            if enter_cafe_iframe(driver):
                print("  >> cafe_main iframe 으로 전환됨")
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                _diagnose(driver, "글 상세(iframe 내부)")
            driver.switch_to.default_content()

        print("\n[안내] 브라우저를 20초간 유지합니다. 화면을 확인하세요...")
        time.sleep(20)
    except Exception as e:
        print(f"[오류] {e}")
        time.sleep(10)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ===================================================================
# 7. 메인 루프
# ===================================================================
def _is_session_dead(exc):
    """브라우저 창이 닫히거나 드라이버 연결이 끊겨 더 이상 복구 불가능한
    Selenium 오류인지 판별한다. 이 경우 봇을 종료시켜 .bat 런처가
    새 브라우저로 재시작하도록 한다 (좀비 상태로 무한 에러 방지)."""
    msg = str(exc).lower()
    signatures = (
        "invalid session id",
        "session deleted",
        "browser has closed",
        "not connected to devtools",
        "disconnected",
        "no such window",
        "web view not found",
        "chrome not reachable",
        "unable to connect to renderer",
        "target window already closed",
    )
    return any(s in msg for s in signatures)


def main():
    config = load_config()
    naver_id = config["NAVER"]["id"]
    naver_pw = config["NAVER"]["pw"]

    board_url = config.get("COMMENT_BOT", "board_url", fallback="").strip()
    comment_texts = load_comment_texts(config)
    poll_interval = config.getint("COMMENT_BOT", "poll_interval_sec", fallback=120)
    min_delay = config.getfloat("COMMENT_BOT", "min_action_delay", fallback=3.0)
    max_delay = config.getfloat("COMMENT_BOT", "max_action_delay", fallback=8.0)
    delay_range = (min_delay, max_delay)

    # 텔레그램 일일 보고 설정
    tg = {
        "enabled": config.getboolean("COMMENT_BOT", "telegram_enabled", fallback=False),
        "token": config.get("COMMENT_BOT", "telegram_token", fallback="").strip(),
        "chat_id": config.get("COMMENT_BOT", "telegram_chat_id", fallback="").strip(),
        "report_hour": config.getint("COMMENT_BOT", "telegram_report_hour", fallback=9),
        # 주간 회원증가 보고 (기본: 매주 월요일=0, 보고시각은 위 report_hour 재사용)
        "weekly_enabled": config.getboolean("COMMENT_BOT", "telegram_weekly_enabled", fallback=True),
        "weekly_day": config.getint("COMMENT_BOT", "telegram_weekly_report_day", fallback=0),
    }

    if not board_url:
        print("=" * 60)
        print("[설정 필요] config.ini 의 [COMMENT_BOT] board_url 이 비어 있습니다.")
        print("감시할 게시판(가입인사) 페이지를 브라우저에서 연 뒤 주소창의 URL 을")
        print("config.ini 의 board_url 에 붙여넣고 다시 실행하세요.")
        print("예: board_url = https://cafe.naver.com/f-e/cafes/26321967/menus/<게시판번호>")
        print("=" * 60)
        sys.exit(1)

    if not comment_texts:
        print("[오류] comment_texts 가 비어 있습니다. config.ini 에 댓글 문구를 입력하세요.")
        sys.exit(1)

    clubid, menuid = parse_club_menu(board_url)
    if not clubid:
        print(f"[오류] board_url 에서 카페 ID(clubid)를 찾지 못했습니다: {board_url}")
        sys.exit(1)

    # ── 모드 분기: inspect / test / probe / backfill / (기본) 감시 ──
    mode = sys.argv[1] if len(sys.argv) > 1 else "watch"
    if mode == "backfill":
        if len(sys.argv) < 3:
            print("[사용법] python comment_bot.py backfill YYYY-MM-DD [go] [ignoreseen]")
            print("  예: python comment_bot.py backfill 2026-06-08              (미리보기)")
            print("      python comment_bot.py backfill 2026-06-08 go           (실제 작성, seen 건너뜀)")
            print("      python comment_bot.py backfill 2026-06-08 go ignoreseen(밀린 글 복구: seen 무시+중복방지)")
            sys.exit(1)
        try:
            cutoff = datetime.date.fromisoformat(sys.argv[2])
        except ValueError:
            print(f"[오류] 날짜 형식이 잘못됐습니다: {sys.argv[2]} (예: 2026-06-08)")
            sys.exit(1)
        rest = sys.argv[3:]
        do_post = "go" in rest
        ignore_seen = any(a in ("ignoreseen", "force", "--ignore-seen") for a in rest)
        # 백필은 다량 작성이라 스팸 감지 회피용으로 더 긴 딜레이 사용
        bf_min = config.getfloat("COMMENT_BOT", "backfill_min_delay", fallback=15.0)
        bf_max = config.getfloat("COMMENT_BOT", "backfill_max_delay", fallback=40.0)
        _run_backfill(naver_id, naver_pw, clubid, board_url, comment_texts,
                      (bf_min, bf_max), cutoff, do_post, ignore_seen=ignore_seen)
        return
    if mode == "report":
        # 어제 통계를 지금 즉시 텔레그램으로 1회 발송 (수동)
        _run_report_now(naver_id, naver_pw, clubid, tg)
        return
    if mode in ("inspect", "test", "probe"):
        target_id = sys.argv[2] if len(sys.argv) > 2 else None
        _run_oneoff(naver_id, naver_pw, clubid, board_url, comment_texts,
                    delay_range, mode, target_id)
        return

    # ── watch(상시 감시) 모드: 단일 인스턴스 보장 ──
    if not acquire_watch_lock():
        print("[중복 차단] 이미 다른 댓글봇(watch) 인스턴스가 실행 중입니다. 이 프로세스는 종료합니다.")
        print("           (감시봇은 하나만 떠 있어야 크롬 프로필 충돌이 없습니다.)")
        sys.exit(0)

    print("=" * 60)
    print("  네이버 카페 자동 댓글 봇")
    print(f"  - 게시판: clubid={clubid}, menuid={menuid}")
    print(f"  - 댓글 문구: {len(comment_texts)}개 (랜덤)")
    print(f"  - 감시 주기: {poll_interval}초")
    print("  - 대상: 봇 시작 이후 새로 올라온 글만")
    print(f"  - 텔레그램 보고: {'켜짐 (매일 ' + str(tg['report_hour']) + '시)' if tg['enabled'] else '꺼짐'}")
    if tg["enabled"] and tg["weekly_enabled"]:
        _wd = ["월", "화", "수", "목", "금", "토", "일"][tg["weekly_day"] % 7]
        print(f"  - 주간 회원증가 보고: 켜짐 (매주 {_wd}요일 {tg['report_hour']}시, 지난 한 주)")
    print("  - 중지: Ctrl+C")
    print("=" * 60)

    driver = make_driver()

    seen = load_seen()
    # 텔레그램 일일 보고용 누적 기록
    pending = []                      # [(시각문자열, aid, title, 성공여부), ...]
    last_report_date = datetime.date.today()
    last_weekly_week = load_last_weekly()   # (연도, ISO주차) — 주간 회원증가 보고 중복 방지
    fail_count = 0

    def _tg(text):
        if tg["enabled"]:
            send_telegram(tg["token"], tg["chat_id"], text)

    try:
        naver_login(driver, naver_id, naver_pw)

        # ── 기준선 설정: 시작 시점의 글들을 '이미 본 것'으로 기록 (댓글 X) ──
        print("[기준선] 현재 게시판 글 목록을 기준선으로 기록합니다 (댓글 안 닮)...")
        baseline = fetch_article_ids(driver, board_url)
        new_baseline = 0
        for aid, title in baseline:
            if aid not in seen:
                seen.add(aid)
                new_baseline += 1
        save_seen(seen)
        print(f"[기준선] 글 {len(baseline)}개 확인, {new_baseline}개를 기준선에 추가. "
              "이후 새 글부터 댓글을 작성합니다.\n")

        # 텔레그램은 매일 보고 시각에만 발송 (시작/중지/오류 알림은 보내지 않음)

        # ── 감시 루프 ──
        while True:
            try:
                articles = fetch_article_ids(driver, board_url)
            except Exception as e:
                if _is_session_dead(e):
                    # 브라우저가 닫힘/연결 끊김 → 복구 불가. 봇을 종료해
                    # .bat 런처가 새 브라우저로 재시작하게 한다.
                    print(f"[복구] 브라우저 세션이 끊겼습니다: {e}")
                    print("[복구] 봇을 종료합니다. 런처(run_comment_bot.bat)가 곧 재시작합니다.")
                    raise
                print(f"[경고] 글 목록 조회 실패: {e}. {poll_interval}초 후 재시도.")
                time.sleep(poll_interval)
                continue

            # 새 글 = seen 에 없는 ID (목록은 보통 최신순이므로 오래된 것부터 처리하려 역순)
            new_articles = [(aid, title) for aid, title in articles if aid not in seen]
            new_articles.reverse()

            if new_articles:
                print(f"[감지] 새 글 {len(new_articles)}개 발견.")
                for aid, title in new_articles:
                    text = random.choice(comment_texts)
                    try:
                        ok = write_comment(driver, clubid, aid, text,
                                           board_url, delay_range)
                    except Exception as e:
                        print(f"  [오류] 글 {aid} 댓글 작성 중 예외: {e}")
                        ok = False
                    # 성공/실패 모두 seen 에 기록해 무한 재시도 방지
                    seen.add(aid)
                    save_seen(seen)
                    pending.append((time.strftime("%m-%d %H:%M"), aid, title, ok))
                    if not ok:
                        fail_count += 1
            else:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] 새 글 없음. {poll_interval}초 후 재확인.")

            # ── 일일 보고: 날짜가 바뀌고 보고 시각이 지나면 전송 ──
            now = datetime.datetime.now()
            if now.date() > last_report_date and now.hour >= tg["report_hour"]:
                report = _format_daily_report(pending, last_report_date)
                try:
                    stats = fetch_cafe_stats(driver, clubid, last_report_date)
                    report += "\n" + _format_stats(stats)
                except Exception as e:
                    report += f"\n📊 통계 수집 실패: {e}"
                _tg(report)
                pending = []
                fail_count = 0
                last_report_date = now.date()

            # ── 주간 회원증가 보고: 설정 요일(기본 월)·보고시각 이후 주 1회 ──
            if tg["enabled"] and tg["weekly_enabled"]:
                iso = now.isocalendar()
                this_week = (iso[0], iso[1])
                if (this_week != last_weekly_week
                        and now.weekday() >= tg["weekly_day"]
                        and now.hour >= tg["report_hour"]):
                    # 직전에 완료된 한 주(월~일)를 집계
                    last_sun = now.date() - datetime.timedelta(days=now.date().weekday() + 1)
                    last_mon = last_sun - datetime.timedelta(days=6)
                    try:
                        total, per_day = count_member_joins(driver, clubid, last_mon, last_sun)
                        _tg(_format_weekly_growth(total, per_day, last_mon, last_sun))
                    except Exception as e:
                        if _is_session_dead(e):
                            raise  # 세션 끊김 → 런처 재시작에 맡김
                        _tg(f"🧑‍🤝‍🧑 주간 회원증가 집계 실패: {e}")
                    last_weekly_week = this_week
                    save_last_weekly(last_weekly_week)

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n[종료] 사용자가 중지했습니다.")
    except Exception as e:
        print(f"\n[치명적 오류] {e}")
    finally:
        save_seen(seen)
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
