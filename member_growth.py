# -*- coding: utf-8 -*-
"""
네이버 카페 '전체 멤버 관리'(ManageWholeMember) 표가 가입일 내림차순으로
정렬돼 있는 점을 이용해, 이번주(월~오늘) 신규 가입 회원수를 직접 센다.

각 행: `별명 (아이디) 등급 가입일 최종방문일 방문수 게시글수 댓글수 성별`
→ 행에서 첫 번째 YYYY.MM.DD. 가 '가입일'.

가입일이 이번주 월요일보다 과거로 내려가면 즉시 중단(정렬 desc 이므로).

- 댓글봇과 충돌 방지: 별도 프로필(chrome_profile_extractor).
- comment_bot.load_config / naver_login 재사용.

사용법:  python member_growth.py
"""
import os
import re
import time
import datetime as dt

import comment_bot as cb
from selenium import webdriver

EXTRACT_PROFILE = os.path.abspath("chrome_profile_extractor")
DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\.")
WD = ["월", "화", "수", "목", "금", "토", "일"]


def make_driver():
    o = webdriver.ChromeOptions()
    o.add_argument("--window-size=1200,950")
    o.add_argument(f"--user-data-dir={EXTRACT_PROFILE}")
    o.add_argument("--disable-blink-features=AutomationControlled")
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=o)


def enter_iframe(driver):
    driver.switch_to.default_content()
    for sel in ("iframe#cafe_main", "iframe[name='cafe_main']"):
        try:
            driver.switch_to.frame(driver.find_element("css selector", sel))
            return True
        except Exception:
            continue
    return False


def page_rows(driver):
    """현재 페이지의 멤버 행 → [(가입일date, 행텍스트)]. 헤더/요일 행은 제외."""
    texts = driver.execute_script("""
        var o=[];var t=document.querySelectorAll('table tr');
        for(var i=0;i<t.length;i++){var x=(t[i].innerText||'').replace(/\\s+/g,' ').trim();
        if(x)o.push(x);}return o;""") or []
    out = []
    for x in texts:
        # 멤버 행만: `별명 (아이디) ...` 패턴 + 날짜 포함 — 아이디는 dlgu**** 식으로 마스킹될 수 있음
        if not re.match(r"^.+?\([A-Za-z0-9_\-*]{2,40}\)", x):
            continue
        m = DATE_RE.search(x)  # 첫 날짜 = 가입일
        if not m:
            continue
        joined = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        out.append((joined, x))
    return out


def goto_next_page(driver, cur_page):
    """#paginate 에서 cur_page+1 로 이동. 성공 True."""
    target = str(cur_page + 1)
    clicked = driver.execute_script("""
        var t=arguments[0],box=document.querySelector('#paginate');
        if(!box)return 'nobox';
        var as=box.querySelectorAll('a');
        for(var i=0;i<as.length;i++){if((as[i].textContent||'').trim()===t){as[i].click();return 'num';}}
        var nx=box.querySelector('a.next');
        if(nx){nx.click();return 'next';}
        return 'end';
    """, target)
    if clicked in ("end", "nobox"):
        return False

    def active():
        return driver.execute_script(
            "var o=document.querySelector('#paginate a.on');return o?o.textContent.trim():'';") or ""
    deadline = time.time() + 8
    while time.time() < deadline:
        if active() == target:
            time.sleep(0.6)
            return True
        time.sleep(0.3)
    return active() == target


def main():
    cfg = cb.load_config()
    naver_id, naver_pw = cfg["NAVER"]["id"], cfg["NAVER"]["pw"]
    m = re.search(r"cafes/(\d+)", cfg["NAVER"].get("cafe_url", ""))
    clubid = m.group(1) if m else "26321967"

    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())

    print("=" * 60)
    print("  네이버 카페 이번주 회원증가수 (가입일 기준 직접 집계)")
    print(f"  - clubid: {clubid}")
    print(f"  - 오늘: {today} ({WD[today.weekday()]})")
    print(f"  - 이번주 범위: {monday}(월) ~ {today}({WD[today.weekday()]})")
    print("=" * 60)

    driver = make_driver()
    try:
        cb.naver_login(driver, naver_id, naver_pw)
        url = f"https://cafe.naver.com/ManageWholeMember.nhn?clubid={clubid}"
        driver.get(url)
        time.sleep(4)
        enter_iframe(driver)

        per_day = {}            # date -> count
        counted_ids = 0
        page = 1
        stop = False
        while not stop and page < 200:
            enter_iframe(driver)
            rows = page_rows(driver)
            if not rows:
                break
            page_min = min(j for j, _ in rows)
            for joined, _ in rows:
                if joined >= monday:
                    per_day[joined] = per_day.get(joined, 0) + 1
                    counted_ids += 1
            oldest = min(j for j, _ in rows)
            newest = max(j for j, _ in rows)
            print(f"[{page}p] {len(rows)}행  가입일 {oldest}~{newest}  "
                  f"(이번주 누적 {counted_ids}명)")
            if page_min < monday:
                stop = True  # 이 페이지에 월요일 이전 가입자 등장 → 더 과거뿐
                break
            if not goto_next_page(driver, page):
                break
            page += 1

        week_total = sum(per_day.values())
        print("\n" + "=" * 60)
        print("  이번주 일별 신규 가입")
        d = monday
        while d <= today:
            print(f"   {d} ({WD[d.weekday()]}): {per_day.get(d, 0):>3}명")
            d += dt.timedelta(days=1)
        print("-" * 60)
        print(f"  ▶ 이번주({monday}~{today}) 회원증가수 합계: {week_total:,}명")
        print("=" * 60)
        if not stop:
            print("※ 주의: 월요일 이전 가입자까지 도달하지 못해 일부 누락 가능 "
                  "(페이지 한계). 위 일별 수치를 확인하세요.")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
