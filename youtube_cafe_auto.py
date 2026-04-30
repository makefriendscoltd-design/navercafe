"""
유튜브/뉴스/텍스트 → 네이버 카페 자동 포스팅 도구
==================================================
다양한 소스(유튜브, 뉴스기사, 직접 텍스트)에서 콘텐츠를 추출하고,
Gemini AI로 블로그 글을 생성한 뒤, 네이버 카페에 자동 발행합니다.

지원 기능:
  - 유튜브 영상 자막 기반 글 생성 + 영상 프레임 이미지
  - 뉴스기사/웹페이지 스크래핑 기반 글 생성 + 사용자 이미지
  - 직접 텍스트 입력 기반 글 생성 + 사용자 이미지
  - CTA(Call to Action) 문구 + 링크 자동 삽입
  - AI 자동 선별 키워드 볼드/하이라이트(음영) 서식
  - 사용자 커스텀 프롬프트 반영
  - 글 길이/이미지 수 설정 가능

사용법:
  python youtube_cafe_auto.py

필요 패키지 (한 줄로 설치):
  pip install opencv-python youtube-transcript-api yt-dlp google-genai selenium pyperclip pyautogui

선택 패키지 (뉴스기사 스크래핑 품질 향상):
  pip install beautifulsoup4

초기 설정:
  첫 실행 시 팝업창에서 네이버 계정, 카페 URL, Gemini API 키,
  CTA 설정, 커스텀 프롬프트 등을 입력하면
  config.ini 파일이 자동 생성됩니다. 이후 config.ini를 직접 수정해도 됩니다.
"""

# ===================================================================
# 1. 표준 라이브러리
# ===================================================================
import os
import re
import sys
import time
import glob
import configparser
import urllib.parse
import urllib.request
import traceback
from html.parser import HTMLParser

# ===================================================================
# 2. 의존성 검사 (서드파티 패키지 설치 여부 확인)
# ===================================================================
REQUIRED_PACKAGES = [
    ('cv2',                     'opencv-python'),
    ('youtube_transcript_api',  'youtube-transcript-api'),
    ('yt_dlp',                  'yt-dlp'),
    ('google.genai',            'google-genai'),
    ('selenium',                'selenium'),
    ('pyperclip',               'pyperclip'),
    ('pyautogui',               'pyautogui'),
]

def check_dependencies():
    """필요한 패키지가 모두 설치되어 있는지 확인합니다."""
    missing = []
    for module_name, pip_name in REQUIRED_PACKAGES:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(pip_name)
    if missing:
        print("=" * 60)
        print("[오류] 다음 패키지가 설치되지 않았습니다:")
        for pkg in missing:
            print(f"  - {pkg}")
        print(f"\n아래 명령어로 한 번에 설치하세요:")
        print(f"  pip install {' '.join(missing)}")
        print("=" * 60)
        sys.exit(1)

check_dependencies()

# ===================================================================
# 3. 서드파티 라이브러리 import (의존성 검사 통과 후)
# ===================================================================
import cv2
import pyperclip
import pyautogui
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from google import genai
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    UnexpectedAlertPresentException, NoAlertPresentException
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ===================================================================
# 4. 경로 및 설정 관리
# ===================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.ini")

# 전역 설정 변수
NAVER_ID = ""
NAVER_PW = ""
CAFE_URL = ""
GEMINI_API_KEY = ""


def _ask_multiline(root, title, prompt, width=600, height=400):
    """tkinter로 멀티라인 텍스트 입력창을 표시합니다."""
    import tkinter as tk

    dialog = tk.Toplevel(root)
    dialog.title(title)
    dialog.attributes('-topmost', True)
    dialog.resizable(True, True)

    # 화면 중앙 배치
    screen_w = dialog.winfo_screenwidth()
    screen_h = dialog.winfo_screenheight()
    x = (screen_w - width) // 2
    y = (screen_h - height) // 2
    dialog.geometry(f"{width}x{height}+{x}+{y}")

    # 안내 라벨
    label = tk.Label(dialog, text=prompt, justify='left', wraplength=width - 40, padx=10, pady=8)
    label.pack(fill='x')

    # 결과를 StringVar로 저장
    result_var = tk.StringVar(value="")
    cancelled = tk.BooleanVar(value=True)

    def on_ok():
        result_var.set(text_widget.get('1.0', 'end-1c').strip())
        cancelled.set(False)
        dialog.destroy()

    def on_cancel():
        dialog.destroy()

    # 버튼 영역 (먼저 pack해서 항상 보이도록)
    btn_frame = tk.Frame(dialog)
    btn_frame.pack(side='bottom', fill='x', padx=10, pady=8)
    tk.Button(btn_frame, text="확인", command=on_ok, width=12, height=1).pack(side='right', padx=5)
    tk.Button(btn_frame, text="취소", command=on_cancel, width=12, height=1).pack(side='right')

    # 텍스트 입력 영역
    text_frame = tk.Frame(dialog)
    text_frame.pack(fill='both', expand=True, padx=10, pady=(0, 5))

    scrollbar = tk.Scrollbar(text_frame)
    scrollbar.pack(side='right', fill='y')

    text_widget = tk.Text(text_frame, wrap='word', font=('맑은 고딕', 10),
                          yscrollcommand=scrollbar.set)
    text_widget.pack(fill='both', expand=True)
    scrollbar.config(command=text_widget.yview)

    text_widget.focus_set()
    dialog.grab_set()
    root.wait_window(dialog)
    return None if cancelled.get() else result_var.get()


def load_or_create_config():
    """config.ini를 읽거나, 없으면 설정 마법사를 실행합니다."""
    import tkinter as tk
    from tkinter import simpledialog, messagebox

    if os.path.exists(CONFIG_FILE):
        config = configparser.RawConfigParser()
        config.read(CONFIG_FILE, encoding='utf-8')
        required = [('NAVER', 'id'), ('NAVER', 'pw'), ('NAVER', 'cafe_url'), ('GEMINI', 'api_key')]
        for section, key in required:
            if not config.has_option(section, key) or not config[section][key].strip():
                print(f"[오류] config.ini의 [{section}] {key} 값이 비어있습니다.")
                print(f"  -> {CONFIG_FILE} 을 열어 수정하거나, 삭제 후 재실행하세요.")
                sys.exit(1)
        return config

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)

    messagebox.showinfo(
        "초기 설정",
        "처음 실행합니다.\n"
        "네이버 계정, 카페 URL, Gemini API 키,\n"
        "CTA 설정, 글 작성 스타일을 차례로 입력합니다.\n\n"
        "입력한 정보는 config.ini 파일에 저장됩니다.",
        parent=root
    )

    # ── 필수 항목 (한 줄 입력) ──
    essential_fields = [
        ("1/7 네이버 아이디", "네이버 아이디 (이메일 형식):"),
        ("2/7 네이버 비밀번호", "네이버 비밀번호:"),
        ("3/7 카페 게시판 URL", "글을 올릴 카페 게시판 URL:\n(예: https://cafe.naver.com/f-e/cafes/12345/menus/67)"),
        ("4/7 Gemini API 키", "Google Gemini API 키:\n(https://aistudio.google.com/apikey 에서 발급)"),
    ]

    values = []
    for title, prompt in essential_fields:
        val = simpledialog.askstring(title, prompt, parent=root)
        if not val or not val.strip():
            messagebox.showerror("오류", f"{title} 값이 입력되지 않았습니다.\n프로그램을 종료합니다.", parent=root)
            root.destroy()
            sys.exit(1)
        values.append(val.strip())

    # ── CTA 설정 ──
    cta_enabled = messagebox.askyesno(
        "5/7 CTA 설정",
        "글 마지막에 CTA(유입 링크)를 삽입하시겠습니까?\n\n"
        "예: 카카오톡 오픈채팅, 웹사이트, 상담 링크 등",
        parent=root
    )

    cta_text = ""
    cta_link_url = ""
    cta_link_text = ""
    if cta_enabled:
        cta_text = simpledialog.askstring(
            "5/7 CTA 안내 문구",
            "CTA 안내 문구를 입력하세요:\n(예: 더 많은 정보가 궁금하다면 아래 링크를 확인하세요!)",
            parent=root
        ) or ""
        cta_link_url = simpledialog.askstring(
            "5/7 CTA 링크 URL",
            "유입할 링크 URL을 입력하세요:\n(예: https://open.kakao.com/o/xxxxx)",
            parent=root
        ) or ""
        cta_link_text = simpledialog.askstring(
            "5/7 CTA 링크 버튼 텍스트",
            "링크에 표시할 텍스트를 입력하세요:\n(예: 카카오톡 오픈채팅 참여하기)",
            parent=root
        ) or ""

    # ── 커스텀 프롬프트 ──
    custom_instructions = _ask_multiline(
        root,
        "6/7 글 작성 스타일 지시사항",
        "AI가 글을 작성할 때 반영할 추가 지시사항을 입력하세요.\n"
        "(예: 반말 사용, ~하세요 체 사용, 특정 브랜드 톤앤매너 등)\n"
        "비워두면 기본 설정으로 진행합니다.",
        width=600, height=250
    ) or ""

    # ── 콘텐츠 길이 설정 ──
    content_length = simpledialog.askstring(
        "7/7 글 길이 설정",
        "원하는 글 길이를 선택하세요:\n\n"
        "1. 짧게 (1000~1500자, 문단 4개)\n"
        "2. 보통 (1500~2500자, 문단 4~6개) — 추천\n"
        "3. 길게 (2500~4000자, 문단 6~8개)\n\n"
        "숫자 입력 (기본값: 2):",
        parent=root
    ) or "2"

    length_presets = {
        '1': ('1000', '1500', '4', '4'),
        '2': ('1500', '2500', '6', '6'),
        '3': ('2500', '4000', '8', '8'),
    }
    preset = length_presets.get(content_length.strip(), length_presets['2'])

    root.destroy()

    config = configparser.RawConfigParser()
    config['NAVER'] = {'id': values[0], 'pw': values[1], 'cafe_url': values[2]}
    config['GEMINI'] = {'api_key': values[3]}
    config['CTA'] = {
        'enabled': str(cta_enabled).lower(),
        'text': cta_text,
        'link_url': cta_link_url,
        'link_text': cta_link_text,
    }
    config['FORMATTING'] = {
        'bold_enabled': 'true',
        'highlight_enabled': 'true',
        'highlight_color': '#FFFF00',
    }
    config['PROMPT'] = {
        'custom_instructions': custom_instructions,
    }
    config['CONTENT'] = {
        'min_length': preset[0],
        'max_length': preset[1],
        'max_paragraphs': preset[2],
        'image_count': preset[3],
    }

    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        config.write(f)

    print(f"[설정] config.ini 저장 완료: {CONFIG_FILE}")
    print("[설정] 나중에 설정을 변경하려면 config.ini를 직접 편집하세요.\n")
    return config


def load_optional_config(config):
    """[CTA], [FORMATTING], [PROMPT], [CONTENT] 등 선택적 설정을 안전한 기본값과 함께 로드합니다."""
    result = {}
    # CTA
    result['cta_enabled'] = config.getboolean('CTA', 'enabled', fallback=False)
    result['cta_text'] = config.get('CTA', 'text', fallback='')
    result['cta_link_url'] = config.get('CTA', 'link_url', fallback='')
    result['cta_link_text'] = config.get('CTA', 'link_text', fallback='')
    # 서식
    result['bold_enabled'] = config.getboolean('FORMATTING', 'bold_enabled', fallback=True)
    result['highlight_enabled'] = config.getboolean('FORMATTING', 'highlight_enabled', fallback=True)
    result['highlight_color'] = config.get('FORMATTING', 'highlight_color', fallback='#FFFF00')
    # 커스텀 프롬프트
    result['custom_instructions'] = config.get('PROMPT', 'custom_instructions', fallback='')
    # 콘텐츠 길이
    result['min_length'] = config.getint('CONTENT', 'min_length', fallback=1500)
    result['max_length'] = config.getint('CONTENT', 'max_length', fallback=2500)
    result['max_paragraphs'] = config.getint('CONTENT', 'max_paragraphs', fallback=6)
    result['image_count'] = config.getint('CONTENT', 'image_count', fallback=6)
    return result


# ===================================================================
# 5. 메타프롬프트 (동적 생성)
# ===================================================================

DEFAULT_WRITING_STYLE = r"""# 유튜브 영상 → 네이버 카페 칼럼 변환 메타프롬프트 v2

## 역할
당신은 매거진 B, 롱블랙, 퍼블리 스타일의 한국어 비즈니스 칼럼니스트입니다.
독자는 30~45세 창업자·마케터·직장인. 이들은 바쁩니다. 첫 문장에서 안 끌리면 닫습니다.
정보를 '전달'하지 말고, 이야기를 '펼쳐'내십시오.

---

## ❶ 제목 — 카피라이팅 원칙

제목은 '정보 요약'이 아니라 '클릭 욕구'를 만드는 것입니다.

**반드시 아래 4가지 중 하나의 긴장 구조를 사용하십시오:**

A) 역설형: 상식을 뒤집는 대립 구도
   예) "마케터 출신 창업자가 '초기에 마케팅하지 말라'고 말한 이유"
   예) "설탕을 팔아야 살아남는 소다 시장에서 설탕을 뺀 회사의 결말"

B) 결과 선공개형: 숫자로 충격을 주고, 이유로 끌어당김
   예) "18개월 만에 월매출 100억. 그들이 버린 건 광고가 아니라 '설명'이었다"
   예) "월마트 입점 제안을 거절했다. 6개월 뒤 그들은 월마트에 입점했다"

C) 불편한 질문형: 독자 자신의 상황을 건드림
   예) "당신 제품, 설명 없이도 팔리나요?"
   예) "투자자 10명이 전부 거절한 아이디어가 2조 원이 된 과정"

D) 내부자 폭로형: '비밀' 또는 '뒤에서 일어난 일'의 뉘앙스
   예) "소다 업계 1위들이 올리팝을 처음 봤을 때 무시한 이유, 그리고 5년 후"

부제는 본제의 긴장을 '해소'하지 말고 '심화'시키십시오.
나쁜 예) "소다 시장을 뒤흔든 단 하나의 전략" (너무 평범)
좋은 예) "규칙을 하나만 깼다. 나머지는 철저히 지켰다" (역설이 남음)

---

## ❷ 도입부 — 독자를 낚는 3단 구조

**[1문장] 장면 또는 역설로 시작**
뉴스 헤드라인도, 정의도 아닙니다. 독자가 머릿속에 그림을 그리게 하십시오.
나쁜 예) "전 세계 소다 시장은 거대하지만 건강에 해롭다는 약점이 있습니다."
좋은 예) "콜라 한 캔에 설탕이 39g 들어 있다는 걸 알면서도, 사람들은 오늘도 콜라를 삽니다."

**[2~3문장] 긴장 고조**
주인공이 등장하기 직전, '왜 이게 말이 안 되는지'를 보여주십시오.
숫자를 쓸 때는 맥락과 함께. "18.5억 달러"보다 "코카콜라가 100년 넘게 지킨 시장에, 5년 된 신생 브랜드가 18.5억 달러 가치로 들어왔다"가 훨씬 강합니다.

**[1문장] 이 글이 줄 것**
"이 칼럼에서는 ~을 분석합니다" 금지.
대신: "어떻게 가능했는지, 지금부터 뜯어봅니다." / "그 답이 생각보다 단순해서 오히려 불편합니다."

---

## ❸ 본문 — 나열이 아닌 서사

섹션은 3~5개. 각 섹션은 독립된 '미니 이야기'입니다.

**섹션 제목 규칙**
번역투 금지: "전략적 피벗", "제품-시장 적합성 확보", "단계적 확장 전략" (금지)
말하듯 쓰기: "두 번째 도전에서 달라진 딱 한 가지", "잘 팔릴 때까지 버티는 법", "돈 쓰기 전에 먼저 할 일" (권장)

**섹션 내부 구조 (매 섹션마다)**

① 훅 문장: 이 섹션에서 가장 반직관적이거나 흥미로운 사실 한 줄
② 배경·맥락: 왜 이게 중요한지 (2~3문장, 독자의 언어로)
③ 핵심 장면 또는 발화: 영상 속 구체적 일화나 발언을 재구성
   - 직접 인용은 따옴표 없이 "그는 이렇게 말했습니다" 형식으로
   - 장면 묘사: "투자자 앞에서 그가 꺼낸 것은 PPT가 아니라 캔 하나였습니다"
④ 적용 또는 반전: 독자가 '나한테도 해당되네' 느끼는 한 문장

**표는 서사가 끊길 때만 사용**
표 앞에 반드시 맥락 문장을 쓰십시오. 표를 그냥 툭 던지지 마십시오.

---

## ❹ 문체 — 번역투 교정 사전

아래 왼쪽 표현은 절대 쓰지 마십시오. 오른쪽으로 바꾸십시오.

| 번역투 (금지) | 자연스러운 한국어 (사용) |
|---|---|
| 핵심 발화자 | 이 영상의 주인공 / 그 |
| 전략적 목표 | 노린 것 / 바라던 것 |
| 보편적 교훈 | 누구에게나 통하는 이유 |
| 제품-시장 적합성 | 시장이 원하는 제품인지 확인 (첫 등장 시에만 PMF 병기) |
| 해당 분야 | 이 시장 / 이 업계 |
| ~라는 점에서 주목할 만합니다 | ~이래서 흥미롭습니다 / ~가 포인트입니다 |
| 본 칼럼에서는 | 지금부터 |
| ~함으로써 | ~해서 |
| 명확히 인지하고 | 알고 |
| 도출할 수 있습니다 | 나옵니다 / 알 수 있습니다 |

**추가 문체 원칙:**
- 문장은 짧게. 한 문장에 하나의 생각.
- "~했습니다. ~입니다." 단조로운 연속 금지 → 짧은 문장과 긴 문장을 교차.
- 숫자 앞에는 항상 맥락: "5년" 아니라 "코카콜라가 100년 걸린 일을 5년 만에".
- 섹션 끝에 다음 섹션을 살짝 예고하는 연결 문장 권장.

---

## ❺ 결론과 CTA

**결론 (3~4문장)**
요약 금지. 독자가 읽고 나서 느껴야 할 감정을 설계하십시오.
"결국 올리팝의 성공은 ~의 균형에서 탄생했습니다" → 이런 총정리 문장 금지.
대신: 가장 핵심적인 역설 하나를 다시 꺼내, 독자 자신에게 돌려주십시오.
예) "가장 무서운 경쟁자는 코카콜라가 아니었습니다. 너무 많은 걸 바꾸려는 자기 자신이었습니다."

**CTA (1~2문장)**
링크나 서비스 홍보 전에, 독자의 현재 상황을 찌르는 질문 하나.
나쁜 예) "올리팝의 전략을 당신의 비즈니스에 대입해 보십시오."
좋은 예) "지금 당신 제품에서, 딱 하나만 바꾼다면 무엇입니까?"

---

## ❻ 최종 출력 전 자가 검토

- 제목을 읽었을 때 '왜?' 또는 '어떻게?'가 궁금해지는가?
- 도입부 첫 문장이 장면 또는 역설로 시작하는가?
- 각 섹션에 번역투 표현이 하나도 없는가?
- 정보를 나열한 섹션이 있는가? 있다면 일화나 장면으로 교체하라.
- 결론이 요약인가? 그렇다면 다시 쓰라.
- 소리 내어 읽었을 때 어색한 부분이 없는가?
"""


def build_meta_prompt(optional_config):
    """설정값을 반영한 메타프롬프트를 동적으로 생성합니다."""
    min_len = optional_config.get('min_length', 1500)
    max_len = optional_config.get('max_length', 2500)
    max_para = optional_config.get('max_paragraphs', 6)
    custom = optional_config.get('custom_instructions', '')

    # 사용자 커스텀이 있으면 그것을 사용, 없으면 기본 v2 스타일 사용
    writing_style = custom.strip() if custom.strip() else DEFAULT_WRITING_STYLE

    # 기술적 출력 형식 규칙 (항상 적용)
    technical_rules = f"""
---

## 출력 형식 규칙 (반드시 준수)

### 분량
- 전체 글 길이: **{min_len}~{max_len}자**
- 섹션(문단) 수: **3~{max_para}개**. 자료가 충분하면 섹션을 늘리세요.

### BLOCKQUOTE 태그 규칙 (절대 위반 금지)
- 각 섹션의 소제목은 반드시 [BLOCKQUOTE]소제목[/BLOCKQUOTE] 태그로 감싸주세요.
- [BLOCKQUOTE] 안에는 **오직 한 줄짜리 짧은 소제목만** 넣으세요.
- 소제목 길이: **최소 5자, 최대 25자** (띄어쓰기 포함)
- 절대로 본문, 설명, 부연 내용을 [BLOCKQUOTE] 안에 넣지 마세요.
- [BLOCKQUOTE] 태그 안에 줄바꿈을 넣지 마세요. 반드시 한 줄이어야 합니다.

올바른 예시:
[BLOCKQUOTE]변화의 시작은 작은 습관[/BLOCKQUOTE]
매일 아침 10분의 루틴이 하루 전체를 바꿀 수 있다. 작은 행동이 쌓여 큰 변화를 만들어낸다.

잘못된 예시 (절대 이렇게 하지 마세요):
[BLOCKQUOTE]변화의 시작은 작은 습관이다. 매일 아침 10분의 루틴이 하루 전체를 바꿀 수 있다.[/BLOCKQUOTE]

### 제약 사항
- 전체 길이는 {min_len}~{max_len}자 범위를 지키세요.
- 과도한 이모티콘이나 불필요한 미사여구를 빼세요.
"""

    return writing_style + "\n" + technical_rules


# ===================================================================
# 6. 소스 감지 및 추출
# ===================================================================

def extract_video_id(url):
    """유튜브 URL에서 영상 ID(11자리)를 추출합니다."""
    match = re.search(r'(?:v=|/)([0-9A-Za-z_-]{11}).*', url)
    return match.group(1) if match else None


def detect_source_type(user_input):
    """입력값을 'youtube' / 'article' / 'text' 로 분류합니다."""
    user_input = user_input.strip()

    if not re.match(r'^https?://', user_input, re.IGNORECASE):
        return 'text', user_input

    youtube_patterns = [
        r'youtube\.com/watch', r'youtu\.be/',
        r'youtube\.com/shorts/', r'youtube\.com/embed/',
    ]
    for pattern in youtube_patterns:
        if re.search(pattern, user_input, re.IGNORECASE):
            return 'youtube', user_input

    return 'article', user_input


def get_transcript(video_id):
    """유튜브 영상의 자막을 텍스트로 추출합니다."""
    print("[1/4] 유튜브 자막 추출 중...")
    try:
        transcript = YouTubeTranscriptApi().fetch(video_id, languages=['ko', 'en'])
        text = " ".join([t.text for t in transcript])
        return text
    except Exception as e:
        print(f"  -> 자막 추출 실패: {e}")
        return None


def scrape_article(url):
    """뉴스기사/웹페이지에서 본문 텍스트를 추출합니다."""
    print("[1/4] 웹 페이지에서 텍스트 추출 중...")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            charset = response.headers.get_content_charset() or 'utf-8'
            html_content = response.read().decode(charset, errors='replace')
    except Exception as e:
        print(f"  -> 웹 페이지 접근 실패: {e}")
        return None

    # beautifulsoup4가 있으면 우선 사용 (더 정확한 추출)
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()

        # 한국 뉴스사이트 공통 셀렉터
        selectors = [
            'article', '#articleBodyContents', '#articeBody',
            '.news_end', '#harmonyContainer', '.article_body',
            '#mArticle', '.view_article', '.post-content', 'main',
        ]
        text = None
        for sel in selectors:
            el = soup.select_one(sel)
            if el and len(el.get_text(strip=True)) > 100:
                text = el.get_text(separator='\n', strip=True)
                break

        if not text:
            paragraphs = soup.find_all('p')
            text = '\n'.join(
                p.get_text(strip=True) for p in paragraphs
                if len(p.get_text(strip=True)) > 20
            )

        if not text or len(text) < 50:
            text = soup.get_text(separator='\n', strip=True)

        print(f"  -> BeautifulSoup로 {len(text)}자 추출 완료")
        return text

    except ImportError:
        pass

    # beautifulsoup4 없으면 표준 라이브러리 폴백
    class _ArticleParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text_parts = []
            self._skip_tags = {'script', 'style', 'nav', 'footer', 'header'}
            self._skip_depth = 0

        def handle_starttag(self, tag, attrs):
            if tag in self._skip_tags:
                self._skip_depth += 1

        def handle_endtag(self, tag):
            if tag in self._skip_tags and self._skip_depth > 0:
                self._skip_depth -= 1

        def handle_data(self, data):
            if self._skip_depth == 0:
                stripped = data.strip()
                if stripped:
                    self.text_parts.append(stripped)

    parser = _ArticleParser()
    parser.feed(html_content)
    text = '\n'.join(parser.text_parts)
    print(f"  -> html.parser로 {len(text)}자 추출 완료")
    return text


def ask_for_images():
    """tkinter 파일 다이얼로그로 이미지 파일을 선택받습니다."""
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)

    answer = messagebox.askyesno(
        "이미지 추가",
        "글에 첨부할 이미지를 선택하시겠습니까?\n\n"
        "'아니오'를 누르면 이미지 없이 진행합니다.",
        parent=root
    )

    if not answer:
        root.destroy()
        return []

    file_paths = filedialog.askopenfilenames(
        title="첨부할 이미지 선택 (여러 장 가능)",
        filetypes=[("이미지 파일", "*.jpg *.jpeg *.png *.bmp *.gif"), ("모든 파일", "*.*")],
        parent=root
    )
    root.destroy()

    result = [os.path.abspath(p) for p in file_paths]
    if result:
        print(f"  -> 사용자 이미지 {len(result)}장 선택됨")
    else:
        print("  -> 이미지 선택 안 함. 텍스트만 포스팅합니다.")
    return result


# ===================================================================
# 7. AI 글 생성
# ===================================================================

def generate_blog_post(source_text, source_type='youtube', has_images=True,
                       formatting_config=None, image_count=4):
    """Gemini AI를 이용해 소스 텍스트를 블로그 칼럼으로 변환합니다."""
    print("[2/4] Gemini AI로 블로그 글 작성 중...")
    client = genai.Client(api_key=GEMINI_API_KEY)

    meta_prompt = build_meta_prompt(formatting_config or {})

    source_intros = {
        'youtube': '유튜브 영상 스크립트는 다음과 같습니다:',
        'article': '뉴스 기사/웹 페이지 내용은 다음과 같습니다:',
        'text':    '다음 텍스트를 기반으로 글을 작성해주세요:',
    }
    source_intro = source_intros.get(source_type, source_intros['text'])

    if has_images:
        image_instruction = (
            f"3. 본문 내용 사이사이에 정확히 {image_count}개의 `[IMAGE_HERE]` 이라는 텍스트를 "
            "문단과 문단 사이에 균등하게 배치해주세요. 절대 누락하지 마세요.\n"
            "   ★주의: 본문의 맨 처음에 [IMAGE_HERE]를 넣지 마세요. "
            "반드시 첫 번째 섹션(인용구+본문) 이후에 첫 [IMAGE_HERE]를 배치하세요.★"
        )
    else:
        image_instruction = "3. 이 글에는 이미지가 없으므로 `[IMAGE_HERE]` 마커를 넣지 마세요."

    # 볼드/하이라이트 AI 자동 선별 지시
    bold_enabled = formatting_config.get('bold_enabled', False) if formatting_config else False
    highlight_enabled = formatting_config.get('highlight_enabled', False) if formatting_config else False

    formatting_instruction = ""
    if bold_enabled or highlight_enabled:
        formatting_instruction = "\n4. 본문에서 독자의 시선을 끌 핵심 키워드 3~5개를 선별하여 [BOLD]키워드[/BOLD] 태그로 감싸주세요."
        if highlight_enabled:
            formatting_instruction += (
                "\n5. 그 중 가장 중요한 키워드 1~2개는 [HIGHLIGHT]키워드[/HIGHLIGHT] 태그로도 감싸주세요."
                "\n   ★주의: HIGHLIGHT 태그 안에는 순수 텍스트만 넣으세요. [BOLD] 태그를 중첩하지 마세요.★"
                "\n   올바른 예: [HIGHLIGHT]핵심 키워드[/HIGHLIGHT]"
                "\n   잘못된 예: [HIGHLIGHT][BOLD]핵심 키워드[/BOLD][/HIGHLIGHT]"
            )

    prompt = f"""
{meta_prompt}

{source_intro}
{source_text}

위 메타프롬프트 가이드에 따라 칼럼을 작성해주세요.

★중요 요청사항★:
1. 글의 제목은 무조건 <TITLE>제목내용</TITLE> 태그로 감싸주세요.
2. 본문 내용은 무조건 <BODY>본문내용</BODY> 태그로 감싸주세요.
{image_instruction}{formatting_instruction}
"""

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
    )

    text = response.text
    title_match = re.search(r'<TITLE>(.*?)</TITLE>', text, re.DOTALL)
    body_match = re.search(r'<BODY>(.*?)</BODY>', text, re.DOTALL)

    title = title_match.group(1).strip() if title_match else "제목을 생성하지 못했습니다."
    body = body_match.group(1).strip() if body_match else text

    # AI가 <BLOCKQUOTE> (HTML식)로 출력하는 경우 [BLOCKQUOTE]로 통일
    body = re.sub(r'<BLOCKQUOTE>', '[BLOCKQUOTE]', body, flags=re.IGNORECASE)
    body = re.sub(r'</BLOCKQUOTE>', '[/BLOCKQUOTE]', body, flags=re.IGNORECASE)

    # 디버그: AI 생성 본문의 첫 500자 출력
    print(f"  -> AI 생성 본문 (첫 500자):\n{body[:500]}\n{'='*50}")

    return title, body


# ===================================================================
# 8. 영상 프레임 추출
# ===================================================================

def extract_frames(url, image_count=4):
    """유튜브 영상을 다운로드하고 균등 간격 지점에서 이미지를 캡처합니다."""
    print(f"[3/4] 유튜브 영상 다운로드 및 이미지({image_count}장) 캡처 중...")
    format_list = [
        '18',
        'bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]/best[ext=mp4][height<=480]',
        'worst[ext=mp4]',
    ]

    download_success = False
    downloaded_filename = ""
    for idx, fmt in enumerate(format_list):
        if download_success:
            break
        print(f"  -> 포맷 '{fmt}' (으)로 다운로드 시도 중...")
        current_filename = os.path.join(SCRIPT_DIR, f'temp_video_{idx}.mp4')
        options = {
            'format': fmt, 'outtmpl': current_filename,
            'quiet': True, 'no_warnings': True, 'nocheckcertificate': True,
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([url])
            download_success = True
            downloaded_filename = current_filename
        except Exception as e:
            print(f"  -> 해당 포맷 실패: {e}. 다른 포맷으로 재시도합니다.")

    if not download_success:
        print("[오류] 모든 포맷으로 유튜브 영상 다운로드에 실패했습니다.")
        return []

    cap = cv2.VideoCapture(downloaded_filename)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    image_paths = []
    if total_frames > 0:
        for i in range(1, image_count + 1):
            # 균등 간격: 10%, 30%, 50%, 70%, 90% ... 식으로 배분
            ratio = i / (image_count + 1)
            target_frame = int(total_frames * ratio)
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            ret, frame = cap.read()
            if ret:
                path = os.path.join(SCRIPT_DIR, f'frame_{i}.jpg')
                cv2.imwrite(path, frame)
                image_paths.append(os.path.abspath(path))
    cap.release()

    try:
        os.remove(downloaded_filename)
    except OSError:
        pass
    return image_paths


def cleanup_temp_files():
    """작업 후 임시 파일을 정리합니다."""
    patterns = ['temp_video*.mp4', 'temp_video*.mp4.part', 'temp_video*.m4a',
                'temp_video*.temp.*', 'frame_*.jpg']
    for pattern in patterns:
        for f in glob.glob(os.path.join(SCRIPT_DIR, pattern)):
            try:
                os.remove(f)
            except OSError:
                pass


# ===================================================================
# 9. 텍스트 서식 헬퍼
# ===================================================================

def extract_highlight_keywords(text):
    """[HIGHLIGHT]...[/HIGHLIGHT] 키워드를 추출하고 마커를 제거합니다."""
    keywords = re.findall(r'\[HIGHLIGHT\](.*?)\[/HIGHLIGHT\]', text)
    # BOLD 태그가 섞여 들어온 경우 제거 (Gemini가 중첩 생성할 수 있음)
    keywords = [re.sub(r'\[/?BOLD\]', '', kw).strip() for kw in keywords]
    # 빈 문자열 제거
    keywords = [kw for kw in keywords if kw]
    cleaned = re.sub(r'\[HIGHLIGHT\](.*?)\[/HIGHLIGHT\]', r'\1', text)
    return cleaned, keywords


def paste_with_formatting(text, formatting_config):
    """[BOLD] 마커가 있으면 Ctrl+B 토글로 볼드 적용하며 붙여넣습니다."""
    bold_enabled = formatting_config.get('bold_enabled', False)

    if not bold_enabled or '[BOLD]' not in text:
        clean = re.sub(r'\[BOLD\](.*?)\[/BOLD\]', r'\1', text)
        pyperclip.copy(clean)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.3)
        return

    parts = re.split(r'(\[BOLD\].*?\[/BOLD\])', text)
    for part in parts:
        if not part:
            continue
        bold_match = re.match(r'\[BOLD\](.*?)\[/BOLD\]', part)
        if bold_match:
            clean = bold_match.group(1)
            pyautogui.hotkey('ctrl', 'b')
            time.sleep(0.15)
            pyperclip.copy(clean)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.2)
            pyautogui.hotkey('ctrl', 'b')
            time.sleep(0.15)
        else:
            pyperclip.copy(part)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.2)


def _is_cursor_inside_blockquote(driver):
    """JS로 현재 커서가 인용구 내부에 있는지 확인합니다."""
    try:
        return driver.execute_script("""
            var sel = window.getSelection();
            if (!sel.rangeCount) return false;
            var node = sel.anchorNode;
            while (node && node !== document.body) {
                if (node.nodeType === 1 && (
                    node.classList.contains('se-section-quotation') ||
                    node.classList.contains('se-quotation') ||
                    node.classList.contains('se-quotation-content') ||
                    node.classList.contains('se-quotation-source')))
                    return true;
                node = node.parentNode;
            }
            return false;
        """)
    except Exception:
        return False


def _move_cursor_to_end(driver):
    """에디터 커서를 문서 최하단 + 인용구 밖으로 이동합니다 (JS Range API)."""
    try:
        result = driver.execute_script("""
            var editor = document.querySelector('[contenteditable="true"]');
            if (!editor) return 'no_editor';

            // 스크롤 맨 아래
            editor.scrollTop = editor.scrollHeight;

            // 마지막 섹션이 인용구인지 확인
            var lastChild = editor.lastElementChild;
            var isLastQuote = lastChild && lastChild.classList &&
                lastChild.classList.contains('se-section-quotation');

            if (isLastQuote) {
                // 인용구가 마지막이면 바로 뒤에 빈 텍스트 섹션을 삽입
                var newSection = document.createElement('div');
                newSection.className = 'se-section se-section-text se-l-default';
                newSection.setAttribute('contenteditable', 'true');
                newSection.innerHTML =
                    '<div class="se-module se-module-text">' +
                    '<p class="se-text-paragraph se-text-paragraph-align-">' +
                    '<span class="se-ff-system se-fs15">\\u200B</span></p></div>';
                editor.appendChild(newSection);

                // 새 문단에 커서 배치
                var newSpan = newSection.querySelector('span');
                if (newSpan) {
                    var range = document.createRange();
                    range.setStart(newSpan, 1);
                    range.collapse(true);
                    var sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                }
                return 'created_section_after_quote';
            }

            // 일반 경우: 에디터 끝으로 커서 이동
            var sel = window.getSelection();
            var range = document.createRange();
            range.selectNodeContents(editor);
            range.collapse(false);
            sel.removeAllRanges();
            sel.addRange(range);

            // 커서가 인용구 안에 있는지 확인
            var node = sel.anchorNode;
            var quotationSection = null;
            while (node && node !== editor) {
                if (node.nodeType === 1 &&
                    node.classList.contains('se-section-quotation')) {
                    quotationSection = node;
                    break;
                }
                node = node.parentNode;
            }

            if (quotationSection) {
                // 인용구 바로 뒤에 커서 배치
                range = document.createRange();
                range.setStartAfter(quotationSection);
                range.collapse(true);
                sel.removeAllRanges();
                sel.addRange(range);
                return 'escaped_quotation';
            }
            return 'ok';
        """)
    except Exception:
        pyautogui.hotkey('ctrl', 'End')
    time.sleep(0.3)

    # JS 탈출 후에도 인용구 안이면 키보드로 강제 탈출
    if _is_cursor_inside_blockquote(driver):
        pyautogui.press('escape')
        time.sleep(0.2)
        pyautogui.press('down', presses=5, interval=0.1)
        time.sleep(0.2)
        pyautogui.press('enter')
        time.sleep(0.2)
        pyautogui.press('enter')
        time.sleep(0.3)
        # 최종 확인
        if _is_cursor_inside_blockquote(driver):
            pyautogui.hotkey('ctrl', 'End')
            time.sleep(0.2)
            pyautogui.press('enter')
            time.sleep(0.3)


def insert_blockquote(driver, heading_text):
    """SmartEditor에 인용구 블록을 삽입하고 탈출합니다."""
    print(f"  -> 인용구 삽입 시작: '{heading_text}'")

    # 줄바꿈 제거 (한 줄짜리 소제목만 허용)
    heading_text = heading_text.split('\n')[0].strip()

    # 소제목 30자 초과 안전장치
    if len(heading_text) > 30:
        first_sentence = re.split(r'[.?!。]\s*', heading_text, maxsplit=1)
        if len(first_sentence) >= 2 and first_sentence[0].strip():
            heading_text = first_sentence[0].strip()
        else:
            heading_text = heading_text[:30].strip()

    # (1) 문서 끝으로 이동 + 인용구 밖 보장
    _move_cursor_to_end(driver)

    # (2) 현재 인용구 개수 기록 (새로 만든 인용구를 식별하기 위해)
    quote_count_before = len(driver.find_elements(By.CSS_SELECTOR, '.se-section-quotation'))

    # (3) 인용구 버튼 클릭
    try:
        driver.find_element(By.CSS_SELECTOR, ".se-quote-toolbar-button").click()
    except Exception:
        driver.find_element(
            By.XPATH,
            "//button[contains(@class, 'quote') or .//span[contains(text(), '인용구')]]"
        ).click()
    time.sleep(1.5)

    # (4) 인용구 스타일 선택 (첫 번째 스타일)
    try:
        menu_btn = driver.find_element(By.CSS_SELECTOR, ".se-quote-toolbar-menu li button")
        if menu_btn.is_displayed():
            menu_btn.click()
            time.sleep(1)
    except Exception:
        pass

    # (5) 새로 생긴 인용구의 본문(content) 영역 클릭 (이전 것이 아닌 새것만)
    try:
        driver.execute_script(f"""
            var quotes = document.querySelectorAll('.se-section-quotation');
            if (quotes.length > {quote_count_before}) {{
                var newQuote = quotes[quotes.length - 1];
                var contentPara = newQuote.querySelector(
                    '.se-quotation-content .se-text-paragraph'
                );
                if (contentPara) {{
                    contentPara.click();
                }} else {{
                    var firstPara = newQuote.querySelector('.se-text-paragraph');
                    if (firstPara) firstPara.click();
                }}
            }}
        """)
        time.sleep(0.5)
    except Exception:
        pass

    # (6) 소제목 붙여넣기
    pyperclip.copy(heading_text)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(1)

    # (7) 인용구 탈출: 문서 끝으로 이동
    _move_cursor_to_end(driver)
    print(f"  -> 인용구 삽입 완료: '{heading_text}'")


def paste_chunk_with_blockquotes(driver, chunk_text, formatting_config):
    """텍스트 청크를 블록쿼트/일반텍스트로 분리하여 에디터에 삽입합니다."""
    parts = re.split(r'(\[BLOCKQUOTE\].*?\[/BLOCKQUOTE\])', chunk_text, flags=re.DOTALL)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        bq_match = re.match(r'\[BLOCKQUOTE\](.*?)\[/BLOCKQUOTE\]', part, re.DOTALL)
        if bq_match:
            raw_content = bq_match.group(1).strip()
            if not raw_content:
                continue

            # AI가 BLOCKQUOTE 안에 본문까지 넣은 경우: 첫 줄만 소제목, 나머지는 본문
            lines = raw_content.split('\n', 1)
            heading_text = lines[0].strip()
            extra_body = lines[1].strip() if len(lines) > 1 else ""

            try:
                insert_blockquote(driver, heading_text)
            except Exception as e:
                print(f"  -> 인용구 삽입 실패: {e}, 일반 텍스트로 대체")
                _move_cursor_to_end(driver)
                pyperclip.copy(f"■ {heading_text}")
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(0.5)
                pyautogui.press('enter')
                time.sleep(0.3)

            # 인용구 탈출 확인 후 잔여 본문 삽입
            if extra_body:
                _move_cursor_to_end(driver)
                # 인용구 밖인지 재확인
                if _is_cursor_inside_blockquote(driver):
                    print("  -> [경고] 인용구 내부 감지, 강제 탈출 시도")
                    pyautogui.press('escape')
                    time.sleep(0.1)
                    pyautogui.press('down', presses=5, interval=0.05)
                    time.sleep(0.1)
                    pyautogui.press('enter', presses=2, interval=0.1)
                    time.sleep(0.3)
                paste_with_formatting(extra_body, formatting_config)
                time.sleep(0.5)
                pyautogui.press('enter', presses=2, interval=0.2)
                time.sleep(0.5)
        else:
            # 일반 텍스트: 인용구 밖 확인 후 삽입
            _move_cursor_to_end(driver)
            if _is_cursor_inside_blockquote(driver):
                print("  -> [경고] 일반텍스트 삽입 전 인용구 내부 감지, 강제 탈출")
                pyautogui.press('escape')
                time.sleep(0.1)
                pyautogui.press('down', presses=5, interval=0.05)
                time.sleep(0.1)
                pyautogui.press('enter', presses=2, interval=0.1)
                time.sleep(0.3)
            paste_with_formatting(part, formatting_config)
            time.sleep(0.5)
            pyautogui.press('enter', presses=2, interval=0.2)
            time.sleep(0.5)


def apply_highlight_js(driver, keywords, color):
    """JavaScript로 에디터 본문의 키워드에 배경색 하이라이트를 적용합니다."""
    if not keywords:
        return

    for keyword in keywords:
        escaped = keyword.replace("\\", "\\\\").replace("'", "\\'")
        kw_len = len(keyword)

        js_code = f"""
        (function() {{
            var editor = document.querySelector('[contenteditable="true"]');
            if (!editor) return 'no editor';
            var walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, null, false);
            var found = 0;
            var nodes = [];
            while (walker.nextNode()) {{
                if (walker.currentNode.textContent.indexOf('{escaped}') >= 0) {{
                    nodes.push(walker.currentNode);
                }}
            }}
            for (var i = nodes.length - 1; i >= 0; i--) {{
                var node = nodes[i];
                var idx = node.textContent.indexOf('{escaped}');
                if (idx >= 0) {{
                    var range = document.createRange();
                    range.setStart(node, idx);
                    range.setEnd(node, idx + {kw_len});
                    var sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                    document.execCommand('hiliteColor', false, '{color}');
                    found++;
                }}
            }}
            window.getSelection().removeAllRanges();
            return found;
        }})();
        """
        try:
            result = driver.execute_script(js_code)
            if result and result != 'no editor' and int(result) > 0:
                print(f"  -> 하이라이트 적용 완료: '{keyword}' ({result}개소)")
            else:
                print(f"  -> 하이라이트 대상 못찾음: '{keyword}'")
        except Exception as e:
            print(f"  -> 하이라이트 실패 ('{keyword}'): {e}")


# ===================================================================
# 10. 네이버 카페 포스팅
# ===================================================================

def post_to_naver_cafe(title, body, image_paths, optional_config):
    """Selenium + pyautogui로 네이버 카페에 글을 자동 등록합니다."""
    print("[4/4] 네이버 카페 포스팅 시작...")

    # 본문에서 하이라이트 키워드 추출 (마커 제거)
    highlight_keywords = []
    if optional_config.get('highlight_enabled', False):
        body, highlight_keywords = extract_highlight_keywords(body)
        if highlight_keywords:
            print(f"  -> 하이라이트 대상 키워드: {highlight_keywords}")

    driver = webdriver.Chrome()

    try:
        # ── 1단계: 네이버 로그인 ──
        driver.get("https://nid.naver.com/nidlogin.login")
        time.sleep(2)

        pyperclip.copy(NAVER_ID)
        driver.find_element(By.CSS_SELECTOR, "#id").click()
        driver.find_element(By.CSS_SELECTOR, "#id").send_keys(Keys.CONTROL, 'v')
        time.sleep(1)

        pyperclip.copy(NAVER_PW)
        driver.find_element(By.CSS_SELECTOR, "#pw").click()
        driver.find_element(By.CSS_SELECTOR, "#pw").send_keys(Keys.CONTROL, 'v')
        time.sleep(1)

        driver.find_element(By.CSS_SELECTOR, "#log\\.login").click()
        time.sleep(3)

        # 로그인 성공 여부 확인 — 캡챠/2차 인증이 뜨면 수동 처리 대기
        login_ok = False
        for _ in range(60):  # 최대 60초 대기
            current = driver.current_url
            if 'nidlogin' not in current and 'login' not in current.split('/')[-1]:
                login_ok = True
                break
            time.sleep(1)

        if not login_ok:
            print("  -> ⚠ 로그인 페이지에서 벗어나지 못했습니다. 캡챠/보안인증을 직접 완료해주세요.")
            # 추가 60초 대기
            for _ in range(60):
                current = driver.current_url
                if 'nidlogin' not in current and 'login' not in current.split('/')[-1]:
                    login_ok = True
                    break
                time.sleep(1)

        if not login_ok:
            raise Exception("로그인 실패: 2분 내에 로그인이 완료되지 않았습니다.")

        print("  -> 로그인 성공. 카페로 이동합니다.")

        # ── 2단계: 카페 글쓰기 페이지 진입 ──
        decoded_url = urllib.parse.unquote(CAFE_URL)
        club_match = re.search(r'clubid[=&](\d+)|cafes/(\d+)', decoded_url)
        menu_match = re.search(r'menuid[=&](\d+)|menus/(\d+)', decoded_url)

        clubid = (club_match.group(1) or club_match.group(2)) if club_match else None
        menuid = (menu_match.group(1) or menu_match.group(2)) if menu_match else None

        editor_ready = False
        tabs_before = len(driver.window_handles)

        # ── 방법 1: 카페 페이지에서 글쓰기 버튼 클릭 (SPA 대응) ──
        print("  -> 카페 페이지로 이동합니다.")
        driver.get(CAFE_URL)
        time.sleep(5)

        # 오버레이/팝업/모달 제거
        try:
            driver.execute_script("""
                document.querySelectorAll(
                    '[class*="level_alert"], [class*="popup"], [class*="modal"], ' +
                    '[class*="overlay"], [class*="dimmed"], [class*="toast"], ' +
                    '[class*="Alert"], [class*="Layer"]'
                ).forEach(function(el) { el.style.display = 'none'; });
            """)
            time.sleep(0.5)
        except Exception:
            pass

        # 글쓰기 버튼 찾아 클릭 — 텍스트 기반 + 셀렉터 기반
        write_clicked = False
        try:
            write_clicked = driver.execute_script("""
                // 전략 1: 텍스트로 검색
                var all = document.querySelectorAll('a, button, [role="button"], [role="link"]');
                for (var i = 0; i < all.length; i++) {
                    var el = all[i];
                    var text = (el.textContent || '').trim();
                    if (text === '글쓰기' || text === '카페글쓰기') {
                        el.click();
                        return 'text:' + text;
                    }
                }
                // 전략 2: CSS 셀렉터로 검색
                var selectors = [
                    '#writeFormBtn', 'a[href*="write"]', 'a[href*="Write"]',
                    '[class*="WriteButton"]', '[class*="write_btn"]', '[class*="btn_write"]',
                    '[class*="Sidebar"] a[class*="btn"]'
                ];
                for (var j = 0; j < selectors.length; j++) {
                    var el = document.querySelector(selectors[j]);
                    if (el) { el.click(); return 'sel:' + selectors[j]; }
                }
                // 전략 3: XPath로 검색
                var xpath = "//a[contains(., '글쓰기')] | //button[contains(., '글쓰기')]";
                var xr = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                if (xr.singleNodeValue) { xr.singleNodeValue.click(); return 'xpath'; }
                return false;
            """)
        except Exception as e:
            print(f"  -> 글쓰기 버튼 검색 중 오류: {e}")

        if write_clicked:
            print(f"  -> 글쓰기 버튼 클릭 성공 ({write_clicked})")
        else:
            print("  -> 글쓰기 버튼을 찾지 못했습니다.")

        # 새 탭이 열릴 때까지 대기 (최대 10초)
        for _ in range(10):
            if len(driver.window_handles) > tabs_before:
                break
            time.sleep(1)

        if len(driver.window_handles) > tabs_before:
            driver.switch_to.window(driver.window_handles[-1])
            print(f"  -> 새 탭으로 전환. URL: {driver.current_url}")
            time.sleep(3)
            # 알림 처리
            try:
                alert = driver.switch_to.alert
                print(f"  -> 알림: {alert.text}")
                alert.accept()
                time.sleep(1)
            except (NoAlertPresentException, Exception):
                pass
            # 에디터 확인
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".textarea_input")))
                editor_ready = True
                print("  -> 에디터 진입 성공!")
            except Exception:
                print(f"  -> 새 탭에서 에디터 못찾음. URL: {driver.current_url}")

        # ── 방법 2: window.open으로 에디터 직접 열기 (카페 세션 유지) ──
        if not editor_ready and clubid and menuid:
            print("  -> window.open으로 에디터 직접 열기 시도...")
            write_urls = [
                f"https://cafe.naver.com/ca-fe/cafes/{clubid}/articles/write?boardId={menuid}",
                f"https://cafe.naver.com/ArticleWrite.nhn?m=write&clubid={clubid}&menuid={menuid}",
            ]
            for url in write_urls:
                try:
                    # 카페 페이지 탭으로 복귀
                    driver.switch_to.window(driver.window_handles[0])
                    driver.execute_script(f"window.open('{url}', '_blank');")
                    time.sleep(3)
                    if len(driver.window_handles) > tabs_before:
                        driver.switch_to.window(driver.window_handles[-1])
                        # 알림 처리
                        try:
                            alert = driver.switch_to.alert
                            print(f"  -> 알림: {alert.text}")
                            alert.accept()
                            time.sleep(1)
                            if len(driver.window_handles) <= tabs_before:
                                continue
                        except (NoAlertPresentException, Exception):
                            pass
                        # 에디터 확인
                        try:
                            WebDriverWait(driver, 8).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, ".textarea_input")))
                            editor_ready = True
                            print(f"  -> 에디터 진입 성공: {url}")
                            break
                        except Exception:
                            print(f"  -> 에디터 없음: {driver.current_url}")
                            driver.close()
                            driver.switch_to.window(driver.window_handles[0])
                except UnexpectedAlertPresentException:
                    try:
                        driver.switch_to.alert.accept()
                    except Exception:
                        pass
                except Exception as e:
                    print(f"  -> URL 열기 실패: {e}")

        # ── 방법 3: iframe 기반 구 UI 탐색 ──
        if not editor_ready:
            print("  -> iframe 기반 구 UI 탐색 시도...")
            try:
                driver.switch_to.window(driver.window_handles[0])
                driver.switch_to.default_content()
                WebDriverWait(driver, 3).until(
                    EC.frame_to_be_available_and_switch_to_it("cafe_main"))
                WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".textarea_input")))
                editor_ready = True
                print("  -> iframe 내 에디터 발견!")
            except Exception:
                pass

        # ── 3단계: 에디터 확인 및 제목 입력 ──
        print(f"  -> 현재 URL: {driver.current_url}")
        print(f"  -> 열린 탭 수: {len(driver.window_handles)}")

        if not editor_ready:
            # 모든 탭에서 에디터 찾기
            for handle in driver.window_handles:
                driver.switch_to.window(handle)
                driver.switch_to.default_content()
                try:
                    WebDriverWait(driver, 3).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".textarea_input")))
                    editor_ready = True
                    print(f"  -> 탭에서 에디터 발견: {driver.current_url}")
                    break
                except Exception:
                    # iframe 내부도 탐색
                    iframes = driver.find_elements(By.TAG_NAME, "iframe")
                    for iframe in iframes:
                        try:
                            driver.switch_to.default_content()
                            driver.switch_to.frame(iframe)
                            driver.find_element(By.CSS_SELECTOR, ".textarea_input")
                            editor_ready = True
                            iframe_id = iframe.get_attribute('id') or iframe.get_attribute('name') or ''
                            print(f"  -> iframe '{iframe_id}'에서 에디터 발견!")
                            break
                        except Exception:
                            continue
                    if editor_ready:
                        break

        if not editor_ready:
            page_src = driver.page_source[:2000]
            print(f"  -> 페이지 소스 (앞 2000자):\n{page_src}")
            raise Exception("글쓰기 에디터의 제목 입력란(.textarea_input)을 찾을 수 없습니다.")

        title_input = driver.find_element(By.CSS_SELECTOR, ".textarea_input")
        title_input.send_keys(title)
        time.sleep(1)

        # ── 4단계: 본문 + 이미지 교차 삽입 ──
        chunks = body.split("[IMAGE_HERE]")
        title_input.send_keys(Keys.TAB)
        time.sleep(1)

        # 에디터 본문 영역 활성화
        time.sleep(0.5)

        for i, chunk in enumerate(chunks):
            chunk = chunk.strip()
            if chunk:
                paste_chunk_with_blockquotes(driver, chunk, optional_config)

            # 이미지 업로드
            if i < len(image_paths):
                # 이미지 삽입 전 문서 끝으로 이동
                _move_cursor_to_end(driver)

                try:
                    driver.switch_to.window(driver.current_window_handle)
                except Exception:
                    pass
                time.sleep(0.5)

                try:
                    driver.find_element(By.CSS_SELECTOR, ".se-image-toolbar-button").click()
                except Exception:
                    driver.find_element(
                        By.XPATH,
                        "//button[contains(@class, 'image') or .//span[contains(text(), '사진')]]"
                    ).click()

                time.sleep(3)
                pyperclip.copy(image_paths[i])
                time.sleep(0.5)
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(1)
                pyautogui.press('enter')
                time.sleep(5)

                # 이미지 업로드 후 문서 끝으로 이동
                _move_cursor_to_end(driver)
                pyautogui.press('enter')
                time.sleep(0.5)

        # ── 5단계: CTA 삽입 ──
        if optional_config.get('cta_enabled'):
            cta_text = optional_config.get('cta_text', '')
            cta_link_url = optional_config.get('cta_link_url', '')
            cta_link_text = optional_config.get('cta_link_text', '')

            if cta_text or cta_link_text:
                print("  -> CTA 문구 삽입 중...")
                pyautogui.hotkey('ctrl', 'End')
                time.sleep(0.5)
                pyautogui.press('enter', presses=3, interval=0.2)
                time.sleep(0.5)

                # 안내 문구
                if cta_text:
                    pyperclip.copy(cta_text)
                    pyautogui.hotkey('ctrl', 'v')
                    time.sleep(0.5)
                    pyautogui.press('enter', presses=2, interval=0.2)
                    time.sleep(0.3)

                # 링크 텍스트 (URL 포함)
                if cta_link_text and cta_link_url:
                    link_line = f"{cta_link_text}\n{cta_link_url}"
                    pyperclip.copy(link_line)
                    pyautogui.hotkey('ctrl', 'v')
                    time.sleep(0.5)
                elif cta_link_url:
                    pyperclip.copy(cta_link_url)
                    pyautogui.hotkey('ctrl', 'v')
                    time.sleep(0.5)

                time.sleep(1)

        # ── 6단계: 하이라이트 적용 (JS) ──
        if highlight_keywords and optional_config.get('highlight_enabled'):
            print("  -> 하이라이트 서식 적용 중...")
            apply_highlight_js(driver, highlight_keywords, optional_config.get('highlight_color', '#FFFF00'))

        # ── 7단계: 등록 ──
        print("  -> 전체공개/퍼가기 여부는 카페 게시판 기본 설정을 따릅니다.")
        try:
            driver.find_element(By.CSS_SELECTOR, "button.btn_register").click()
        except Exception:
            driver.find_element(
                By.XPATH,
                "//*[contains(text(), '등록') and not(contains(text(), '임시'))]"
                " | //*[contains(@class, 'register') or contains(@class, 'publish')]"
            ).click()
        time.sleep(3)

        driver.close()
        driver.switch_to.window(driver.window_handles[0])
        print("\n완료! 네이버 카페에 글이 등록되었습니다.")

    except Exception as e:
        traceback.print_exc()
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        messagebox.showerror(
            'Selenium 에러 발생',
            f'자동화 중 오류가 발생했습니다:\n{e}\n\n'
            '확인을 누르면 브라우저가 5분간 유지됩니다.\n터미널 로그를 확인해주세요.'
        )
        root.destroy()
        time.sleep(300)


# ===================================================================
# 11. 메인 실행
# ===================================================================
if __name__ == "__main__":
    import tkinter as tk

    # (1) 설정 로드
    config = load_or_create_config()
    NAVER_ID = config['NAVER']['id']
    NAVER_PW = config['NAVER']['pw']
    CAFE_URL = config['NAVER']['cafe_url']
    GEMINI_API_KEY = config['GEMINI']['api_key']
    optional_config = load_optional_config(config)

    # (2) 소스 입력 (멀티라인 입력창)
    print("\n" + "=" * 60)
    print("  입력창에 유튜브 링크, 뉴스 URL, 또는 텍스트를 입력하세요.")
    print("=" * 60 + "\n")

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)

    user_input = _ask_multiline(
        root,
        '소스 입력',
        '아래 중 하나를 입력하세요:\n\n'
        '• 유튜브 영상 링크\n'
        '• 뉴스/웹페이지 URL\n'
        '• 직접 작성한 텍스트 (여러 줄 가능)\n',
        width=600, height=300
    )
    root.destroy()

    if not user_input or not user_input.strip():
        print("입력값이 없습니다. 프로그램을 종료합니다.")
        sys.exit(0)

    user_input = user_input.strip()

    # (3) 소스 유형 감지
    source_type, normalized_input = detect_source_type(user_input)
    source_labels = {'youtube': '유튜브 영상', 'article': '웹 기사/페이지', 'text': '직접 텍스트'}
    print(f"[소스 유형] {source_labels.get(source_type, source_type)}")

    # (4) 이미지 수 결정
    image_count = optional_config.get('image_count', 6)

    # (5) 소스별 콘텐츠 + 이미지 추출
    source_text = None
    image_paths = []

    if source_type == 'youtube':
        v_id = extract_video_id(normalized_input)
        if not v_id:
            print("[오류] 유튜브 URL이 올바르지 않습니다.")
            sys.exit(1)
        source_text = get_transcript(v_id)
        if not source_text:
            print("[오류] 자막이 없어 진행할 수 없습니다.")
            sys.exit(1)
        image_paths = extract_frames(normalized_input, image_count)

    elif source_type == 'article':
        source_text = scrape_article(normalized_input)
        if not source_text:
            print("[오류] 웹 페이지에서 텍스트를 추출할 수 없습니다.")
            sys.exit(1)
        image_paths = ask_for_images()

    elif source_type == 'text':
        source_text = normalized_input
        image_paths = ask_for_images()

    # (6) AI 글 생성
    has_images = len(image_paths) > 0
    actual_image_count = len(image_paths)
    title, body = generate_blog_post(
        source_text, source_type, has_images, optional_config, actual_image_count
    )

    if has_images and "[IMAGE_HERE]" not in body:
        print("[경고] Gemini가 [IMAGE_HERE] 마커를 생성하지 않았습니다. "
              "텍스트 뒤에 이미지를 몰아서 업로드합니다.")

    # (7) 네이버 카페 포스팅
    post_to_naver_cafe(title, body, image_paths, optional_config)

    # (8) 임시 파일 정리
    cleanup_temp_files()
    print("[정리] 임시 파일이 삭제되었습니다.")
