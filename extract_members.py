# -*- coding: utf-8 -*-
"""
네이버 카페 멤버(회원) 관리 페이지에서 회원 아이디를 추출해
`아이디 / 이메일(id@naver.com) / 닉네임` 형태의 CSV로 저장한다.

용도: 내부 명단·관리용. (동의 없는 영리목적 광고메일 발송은 정보통신망법 위반이니 주의)

전제:
- 카페 매니저(운영진) 계정이어야 회원 아이디가 마스킹 없이 보인다.
- 기본값은 Aside 브라우저의 로그인 상태를 재사용한다.
- Selenium fallback에서만 chrome_profile_extractor 프로필을 사용한다.

사용법:
    # 1) 먼저 구조 확인 (셀렉터/iframe/페이지 형태 덤프, 추출 X)
    python extract_members.py inspect "<멤버관리 페이지 URL>"

    # 2) 실제 추출 → members.csv 생성
    python extract_members.py "<멤버관리 페이지 URL>"

    # URL 을 config.ini [MEMBER_EXTRACT] member_url 에 넣어두면 URL 인자 생략 가능
"""

import os
import re
import sys
import csv
import time

import comment_bot as cb  # load_config / naver_login 재사용 (import 시 부작용 없음 — __main__ 가드)
from aside_browser import aside_available, extract_naver_members

try:
    from selenium import webdriver
except ImportError:
    webdriver = None

EXTRACT_PROFILE = os.path.abspath("chrome_profile_extractor")
OUTPUT_CSV = "members.csv"

# 멤버 프로필 링크에서 아이디를 뽑는 URL 파라미터 후보 (마스킹 안 된 실제 아이디가 들어있음)
ID_PARAM_RE = re.compile(r"(?:memberid|userid|memberId|userId|blogId|ID)=([A-Za-z0-9_\-]{2,40})")
# 네이버 아이디 형식: 영문소문자/숫자/_/-, 4~20자 (대략)
ID_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{3,19}$")


def make_extract_driver():
    """추출 전용 프로필을 쓰는 Chrome 드라이버. (댓글봇 프로필과 분리해 충돌 방지)"""
    if webdriver is None:
        raise RuntimeError("Selenium fallback 패키지가 설치되지 않았습니다.")
    options = webdriver.ChromeOptions()
    options.add_argument("--window-size=1100,900")
    options.add_argument(f"--user-data-dir={EXTRACT_PROFILE}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=options)
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        })
    except Exception:
        pass
    return driver


def _enter_cafe_iframe(driver):
    """레거시 카페 관리 페이지는 #cafe_main iframe 안에 내용이 있다. 있으면 진입."""
    driver.switch_to.default_content()
    for sel in ("iframe#cafe_main", "iframe[name='cafe_main']"):
        try:
            frame = driver.find_element("css selector", sel)
            driver.switch_to.frame(frame)
            return True
        except Exception:
            continue
    return False


def _harvest_ids_from_dom(driver):
    """멤버 목록 테이블에서 `별명 (아이디)` 패턴으로 회원 아이디를 수집한다.

    ManageWholeMember 의 각 행은 `별명 (아이디) 등급 가입일 ...` 형태로,
    첫 컬럼이 `별명 (로그인아이디)` 다. 아이디는 영문/숫자/_/- 로만 구성된다.
    (정렬 파라미터 JOINDATE 등 오탐을 피하려고 링크가 아닌 테이블 텍스트를 직접 파싱)
    반환: {아이디: 닉네임} dict
    """
    rows = driver.execute_script("""
        var out = [];
        var trs = document.querySelectorAll('tr');
        for (var i = 0; i < trs.length; i++) {
            out.push((trs[i].innerText || '').replace(/\\s+/g, ' ').trim());
        }
        return out;
    """) or []

    found = {}
    for text in rows:
        # 행 맨 앞의 `별명 (아이디)` 만 매칭 (아이디는 ASCII → 한국어 헤더 '아이디' 는 제외됨)
        m = re.match(r"^(.+?)\s*\(([A-Za-z0-9_\-]{2,40})\)", text)
        if not m:
            continue
        nick, uid = m.group(1).strip(), m.group(2)
        if nick and (uid not in found or not found[uid]):
            found[uid] = nick
        elif uid not in found:
            found[uid] = nick
    return found


def cmd_inspect(driver, url):
    """추출 전 구조 진단: iframe 여부, 후보 링크 샘플, 테이블 텍스트 샘플을 출력."""
    driver.get(url)
    time.sleep(4)
    in_iframe = _enter_cafe_iframe(driver)
    print(f"[inspect] iframe(cafe_main) 진입: {in_iframe}")

    anchors = driver.execute_script("""
        var out = [];
        var els = document.querySelectorAll('a');
        for (var i = 0; i < els.length && out.length < 25; i++) {
            var a = els[i];
            var h = a.getAttribute('href') || '';
            var oc = a.getAttribute('onclick') || '';
            if ((h+oc).match(/member|user|ID=/i))
                out.push((a.textContent||'').trim().slice(0,20) + '  ::  ' + (h||oc).slice(0,120));
        }
        return out;
    """) or []
    print(f"[inspect] member/user 관련 링크 샘플 ({len(anchors)}개):")
    for a in anchors:
        print("   " + a)

    # 체크박스 input 샘플 (ManageWholeMember 는 행마다 체크박스 value 에 아이디가 들어있는 경우가 많다)
    checks = driver.execute_script("""
        var out = [];
        var els = document.querySelectorAll('input[type=checkbox]');
        for (var i = 0; i < els.length && out.length < 15; i++) {
            var c = els[i];
            out.push('name=' + (c.getAttribute('name')||'') + '  value=' + (c.getAttribute('value')||''));
        }
        return out;
    """) or []
    print(f"\n[inspect] 체크박스 input 샘플 ({len(checks)}개):")
    for c in checks:
        print("   " + c)

    # 테이블 행 텍스트 샘플 (아이디/별명 컬럼 구조 파악용)
    rows = driver.execute_script("""
        var out = [];
        var trs = document.querySelectorAll('table tr');
        for (var i = 0; i < trs.length && out.length < 12; i++) {
            var t = (trs[i].innerText || '').replace(/\\s+/g,' ').trim();
            if (t) out.push(t.slice(0, 120));
        }
        return out;
    """) or []
    print(f"\n[inspect] 테이블 행 텍스트 샘플 ({len(rows)}개):")
    for r in rows:
        print("   " + r)

    # 페이지네이션 링크 덤프 (page 번호 이동 방식 파악용)
    pager = driver.execute_script("""
        var out = [];
        var els = document.querySelectorAll('a, button');
        for (var i = 0; i < els.length && out.length < 30; i++) {
            var a = els[i];
            var h = a.getAttribute('href') || '';
            var oc = a.getAttribute('onclick') || '';
            var t = (a.textContent || '').trim();
            if ((h + oc).match(/page|Page|goPage|search\\.page/i) || /^\\d{1,4}$/.test(t)) {
                out.push("[" + t.slice(0,10) + "] href=" + h.slice(0,90) + " onclick=" + oc.slice(0,90));
            }
        }
        return out;
    """) or []
    print(f"\n[inspect] 페이지네이션 후보 링크 ({len(pager)}개):")
    for p in pager:
        print("   " + p)

    # 페이지네이션 영역의 HTML 도 일부 덤프 (form/파라미터 구조 확인)
    pager_html = driver.execute_script("""
        var c = document.querySelector('.prev, .next, [class*=paging], [class*=pagination], [class*=page]');
        var box = c ? (c.closest('div,table,tbody,form') || c) : null;
        return box ? box.outerHTML.slice(0, 900) : '(페이지네이션 컨테이너 못 찾음)';
    """)
    print(f"\n[inspect] 페이지네이션 영역 HTML:\n{pager_html}")

    ids = _harvest_ids_from_dom(driver)
    print(f"\n[inspect] 이 페이지에서 추출된 아이디 후보: {len(ids)}개")
    for uid, nick in list(ids.items())[:15]:
        print(f"   {uid}  (닉:{nick})")
    if not ids:
        print("   → 0개. 링크/체크박스에 아이디가 없거나 마스킹된 상태일 수 있습니다.")
        print("   → 위 '링크/체크박스/테이블 샘플'을 보고 셀렉터를 조정해야 합니다.")


def cmd_extract(driver, url):
    """페이지를 순회하며 아이디를 모아 CSV 로 저장. (현재는 페이지네이션 수동/자동 감지)"""
    all_ids = {}

    def harvest_current():
        _enter_cafe_iframe(driver)
        got = _harvest_ids_from_dom(driver)
        for uid, nick in got.items():
            if uid not in all_ids or (nick and not all_ids[uid]):
                all_ids[uid] = nick
        return len(got)

    driver.get(url)
    time.sleep(3.5)
    harvest_current()
    print(f"[추출] 1페이지: {len(all_ids)}명")

    # ── 페이지네이션: #paginate 의 페이지 번호 링크를 클릭하며 순회 ──
    # 레거시 ManageWholeMember 는 URL 파라미터 없이 AJAX 로 표를 갱신한다.
    # <div id="paginate"><a>1</a>...<a>10</a><a class="next">다음 10개</a></div>
    def active_page():
        return driver.execute_script(
            "var o=document.querySelector('#paginate a.on');return o?o.textContent.trim():'';") or ""

    def goto_page(target):
        """#paginate 에서 target 페이지로 이동. 현재 블록에 없으면 '다음 10개' 로 블록 이동.
        이동 후 활성 페이지가 target 이 될 때까지 대기. 성공 True."""
        t = str(target)
        clicked = driver.execute_script("""
            var t = arguments[0], box = document.querySelector('#paginate');
            if (!box) return 'nobox';
            var as = box.querySelectorAll('a');
            for (var i=0;i<as.length;i++){ if((as[i].textContent||'').trim()===t){ as[i].click(); return 'num'; } }
            var nx = box.querySelector('a.next');
            if (nx){ nx.click(); return 'next'; }
            return 'end';
        """, t)
        if clicked in ("end", "nobox"):
            return False
        # 활성 페이지가 target 으로 바뀔 때까지 대기 (AJAX 로딩)
        deadline = time.time() + 8
        while time.time() < deadline:
            if active_page() == t:
                time.sleep(0.6)  # 표 렌더 안정화
                return True
            time.sleep(0.3)
        # '다음 10개' 였다면 새 블록의 첫 페이지가 활성화됐을 수 있으니 한 번 더 시도
        return active_page() == t

    page = 1
    while page < 2000:
        target = page + 1
        if not goto_page(target):
            break
        before = len(all_ids)
        harvest_current()
        gained = len(all_ids) - before
        print(f"[추출] {target}페이지: +{gained} (누적 {len(all_ids)})")
        page = target
        if gained == 0:
            break  # 새 아이디가 없으면 마지막 페이지로 간주

    # ── CSV 저장 ──
    rows = sorted(all_ids.items())
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["아이디", "이메일", "닉네임"])
        for uid, nick in rows:
            w.writerow([uid, f"{uid}@naver.com", nick])

    print(f"\n[완료] 아이디 {len(rows)}개 → {os.path.abspath(OUTPUT_CSV)}")
    print("       (CSV 는 UTF-8 BOM 이라 엑셀에서 바로 열립니다)")


def main():
    args = [a for a in sys.argv[1:]]
    mode = "extract"
    url = ""
    for a in args:
        if a in ("inspect", "extract"):
            mode = a
        elif a.startswith("http"):
            url = a

    if not url:
        config = cb.load_config()
        url = config.get("MEMBER_EXTRACT", "member_url", fallback="").strip() \
            if config.has_section("MEMBER_EXTRACT") else ""
    if not url:
        print("[사용법] python extract_members.py [inspect] \"<멤버관리 페이지 URL>\"")
        print("  또는 config.ini 에 아래 섹션을 추가하세요:")
        print("  [MEMBER_EXTRACT]")
        print("  member_url = https://cafe.naver.com/...멤버관리URL...")
        sys.exit(1)

    config = cb.load_config()
    backend = config.get("BROWSER", "backend", fallback="").strip().lower() or (
        "aside" if aside_available() else "selenium"
    )
    naver_id = config.get("NAVER", "id", fallback="")
    naver_pw = config.get("NAVER", "pw", fallback="")

    print("=" * 60)
    print("  네이버 카페 회원 아이디 추출기")
    print(f"  - 대상 URL: {url}")
    print(f"  - 모드: {mode}")
    print(f"  - 브라우저: {backend}")
    if backend == "selenium":
        print(f"  - 프로필: {EXTRACT_PROFILE} (댓글봇과 분리)")
    print("=" * 60)

    if backend == "aside":
        result = extract_naver_members(url, inspect=(mode == "inspect"))
        if mode == "inspect":
            print(f"[inspect] iframe(cafe_main) 진입: {result.get('iframe', False)}")
            print(f"[inspect] 테이블 행 샘플 ({len(result.get('samples') or [])}개):")
            for row in result.get("samples") or []:
                print("   " + row)
            print(f"[inspect] 이 페이지에서 추출된 아이디 후보: {result.get('rows', 0)}개")
            return

        members = result.get("members") or {}
        rows = sorted(members.items())
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["아이디", "이메일", "닉네임"])
            for uid, nick in rows:
                writer.writerow([uid, f"{uid}@naver.com", nick])
        print(f"\n[완료] 아이디 {len(rows)}개 → {os.path.abspath(OUTPUT_CSV)}")
        return
    if backend != "selenium":
        raise ValueError(f"지원하지 않는 브라우저 백엔드입니다: {backend}")

    driver = make_extract_driver()
    try:
        cb.naver_login(driver, naver_id, naver_pw)
        if mode == "inspect":
            cmd_inspect(driver, url)
        else:
            cmd_extract(driver, url)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
