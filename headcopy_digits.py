"""헤드카피의 한글 숫자를 아라비아 숫자로 바꾼다.

음성은 TTS가 읽으므로 대본에는 `세 시간`, `일천삼백만 원`으로 적는다. 화면은 눈으로
읽으므로 `3시간`, `1300만 원`이라야 한 눈에 들어온다. 그 구분이 없어서 화면에까지
한글로 풀어 쓴 숫자가 나갔다(2026-09-29).

단위가 붙은 수사만 바꾼다. `수천 명`이나 `첫 번째`처럼 정확한 수가 아닌 표현, 그리고
도구 이름에 들어간 글자는 건드리지 않는다.
"""
from __future__ import annotations

import re

SINO = {"영": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5,
        "육": 6, "륙": 6, "칠": 7, "팔": 8, "구": 9}
NATIVE = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7,
          "여덟": 8, "아홉": 9, "열": 10, "스무": 20, "스물": 20}
UNITS = ("시간", "분", "초", "명", "개월", "개", "번", "주", "달", "년", "원",
         "배", "퍼센트", "프로", "가지", "단계", "시", "일")
# 정확한 수가 아니라서 바꾸지 않는 말들.
VAGUE = ("수천", "수백", "수만", "수억", "몇")

# `십삼만 육천 원`처럼 띄어 쓴 수사도 한 덩어리로 읽는다.
_SINO_WORD = r"(?:[일이삼사오육륙칠팔구십백천만억]+(?:\s+[일이삼사오육륙칠팔구십백천만억]+)*)"
_NATIVE_WORD = r"(?:스물|스무|다섯|여섯|일곱|여덟|아홉|한|두|세|네|열)"
_UNIT = "|".join(UNITS)
# 앞이 한글이면 조사다. `시간이 이 분`의 첫 `이`를 수사로 읽지 않게 막는다.
PATTERN = re.compile(rf"(?<![가-힣])({_SINO_WORD}|{_NATIVE_WORD})(\s*)({_UNIT})")


def _sino_value(word: str) -> int | None:
    """`일천삼백만` 같은 한자 수사를 수로 읽는다. 읽을 수 없으면 None."""
    total = section = 0
    digit = 0
    seen = False
    for char in word:
        if char in SINO:
            digit = SINO[char]
            seen = True
        elif char == "십":
            section += (digit or 1) * 10
            digit = 0
            seen = True
        elif char == "백":
            section += (digit or 1) * 100
            digit = 0
            seen = True
        elif char == "천":
            section += (digit or 1) * 1000
            digit = 0
            seen = True
        elif char in ("만", "억"):
            scale = 10000 if char == "만" else 100000000
            total += (section + digit or 1) * scale
            section = digit = 0
            seen = True
        else:
            return None
    return total + section + digit if seen else None


def _format(value: int) -> str:
    """큰 수는 만·억 단위를 남긴다. 13만 6천이 136000보다 읽기 쉽다."""
    if value >= 100000000 and value % 100000000 == 0:
        return f"{value // 100000000}억"
    if value >= 10000:
        man, rest = divmod(value, 10000)
        if rest == 0:
            return f"{man}만"
        if rest % 1000 == 0:
            return f"{man}만 {rest // 1000}천"
        return f"{man}만 {rest}"
    return str(value)


def to_digits(text: str) -> str:
    """단위가 붙은 한글 수사를 아라비아 숫자로 바꾼 문자열."""
    def replace(match: re.Match[str]) -> str:
        word, unit = match.group(1), match.group(3)
        start = match.start()
        if any(text[max(0, start - len(v)):start + len(word)].startswith(v) for v in VAGUE):
            return match.group(0)
        if text[max(0, start - 1):start] in ("수", "몇"):
            return match.group(0)
        value = NATIVE.get(word)
        if value is None:
            value = _sino_value(re.sub(r"\s+", "", word))
        if value is None or value == 0:
            return match.group(0)
        # 숫자와 단위는 붙여 쓴다(3시간). 금액만 띄운다(13만 6천 원).
        return f"{_format(value)}{' ' if unit == '원' else ''}{unit}"

    return PATTERN.sub(replace, text)
