# -*- coding: utf-8 -*-
"""
member_growth.py 의 7일(최근 7일) 버전.
범위만 '월~오늘' → '오늘-6일 ~ 오늘'(7일)로 바꾼 것. 나머지 로직 동일.
사용법:  python member_growth_7d.py [DAYS]   (DAYS 생략 시 7)
"""
import re
import sys
import time
import datetime as dt

import comment_bot as cb
import member_growth as mg


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    cfg = cb.load_config()
    naver_id, naver_pw = cfg["NAVER"]["id"], cfg["NAVER"]["pw"]
    m = re.search(r"cafes/(\d+)", cfg["NAVER"].get("cafe_url", ""))
    clubid = m.group(1) if m else "26321967"

    today = dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    WD = mg.WD

    print("=" * 60)
    print(f"  네이버 카페 최근 {days}일 회원증가수 (가입일 기준 직접 집계)")
    print(f"  - clubid: {clubid}")
    print(f"  - 범위: {start}({WD[start.weekday()]}) ~ {today}({WD[today.weekday()]})")
    print("=" * 60)

    driver = mg.make_driver()
    try:
        cb.naver_login(driver, naver_id, naver_pw)
        driver.get(f"https://cafe.naver.com/ManageWholeMember.nhn?clubid={clubid}")
        time.sleep(4)
        mg.enter_iframe(driver)

        per_day = {}
        counted = 0
        page = 1
        stop = False
        while not stop and page < 200:
            mg.enter_iframe(driver)
            rows = mg.page_rows(driver)
            if not rows:
                break
            page_min = min(j for j, _ in rows)
            for joined, _ in rows:
                if joined >= start:
                    per_day[joined] = per_day.get(joined, 0) + 1
                    counted += 1
            oldest = min(j for j, _ in rows)
            newest = max(j for j, _ in rows)
            print(f"[{page}p] {len(rows)}행  가입일 {oldest}~{newest}  (누적 {counted}명)")
            if page_min < start:
                stop = True
                break
            if not mg.goto_next_page(driver, page):
                break
            page += 1

        total = sum(per_day.values())
        print("\n" + "=" * 60)
        print(f"  최근 {days}일 일별 신규 가입")
        d = start
        while d <= today:
            print(f"   {d} ({WD[d.weekday()]}): {per_day.get(d, 0):>3}명")
            d += dt.timedelta(days=1)
        print("-" * 60)
        print(f"  ▶ {start}~{today} ({days}일) 회원증가수 합계: {total:,}명")
        print("=" * 60)
        if not stop:
            print("※ 주의: 시작일 이전 가입자까지 도달하지 못해 일부 누락 가능(페이지 한계).")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
