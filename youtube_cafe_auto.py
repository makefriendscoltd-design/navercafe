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
import base64
import configparser
import hashlib
import json
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
from selenium.webdriver.common.action_chains import ActionChains

# ===================================================================
# 4. 경로 및 설정 관리
# ===================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.ini")
COOKIES_FILE = os.path.join(SCRIPT_DIR, "cookies.txt")
PUBLISHED_RECORD_FILE = os.path.join(SCRIPT_DIR, "published_posts.json")
PUBLISH_LOCK_DIR = os.path.join(SCRIPT_DIR, ".publish_locks")

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
    # 글을 올릴 게시판 이름 (글쓰기 화면 좌측 상단 드롭다운에 뜨는 그대로)
    result['board_name'] = config.get('NAVER', 'board_name', fallback='').strip()
    # CTA
    result['cta_enabled'] = config.getboolean('CTA', 'enabled', fallback=False)
    result['cta_text'] = config.get('CTA', 'text', fallback='')
    result['cta_link_url'] = config.get('CTA', 'link_url', fallback='')
    result['cta_link_text'] = config.get('CTA', 'link_text', fallback='')
    # 원본 출처 링크 (맨 하단)
    result['source_label'] = config.get('CTA', 'source_label', fallback='▶ 원본 영상')
    result['source_link_card'] = config.getboolean('CTA', 'source_link_card', fallback=False)
    # 서식
    result['bold_enabled'] = config.getboolean('FORMATTING', 'bold_enabled', fallback=True)
    result['highlight_enabled'] = config.getboolean('FORMATTING', 'highlight_enabled', fallback=True)
    result['highlight_color'] = config.get('FORMATTING', 'highlight_color', fallback='#FFFF00')
    # 글씨체 (네이버 스마트에디터 툴바 기준. font_family가 비면 건드리지 않음)
    result['font_family'] = config.get('FORMATTING', 'font_family', fallback='').strip()
    result['font_size'] = config.get('FORMATTING', 'font_size', fallback='').strip()
    result['font_apply_at_end'] = config.getboolean('FORMATTING', 'font_apply_at_end', fallback=True)
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

DEFAULT_WRITING_STYLE = r"""# 유튜브 영상 → 칼럼 변환 메타프롬프트

## 역할
당신은 비즈니스 및 AI 분야의 전문 콘텐츠 작가입니다. 유튜브 영상의 내용을 분석하여 독자에게 가치 있고 실용적인 인사이트를 제공하는 한국어 칼럼으로 재구성하는 것이 당신의 임무입니다.

## 출력 형식

### 1. 제목
- 명확하고 구체적인 제목 작성
- 핵심 가치 제안이나 숫자를 포함
- 예: "AI로 리스크 없이 수익화 하는 법", "직원 없이 AI로 100만 달러를 벌 수 있는 5가지 비즈니스 모델"

### 2. 도입부
- 2-3 문단으로 구성
- 영상의 핵심 내용과 가치를 요약
- 독자의 관심을 끄는 문제 제기 또는 통계 제시
- 왜 이 내용이 중요한지 설명

### 3. 본문 구조

**섹션 번호 체계:**
- 대주제: 1, 2, 3, 4... (숫자만)
- 소주제: 2.1, 2.2, 2.3... (필요시)

**각 섹션 구성:**
- 명확한 제목: 핵심 메시지를 담은 설명적 제목
- 본문: 2-5 문단으로 구성
- 구체적 예시: 실제 사례, 도구명, 회사명, 수치 등
- 실용적 조언: 독자가 실행할 수 있는 구체적인 방법

**표 활용 (필요시):**
- 비교 정보는 표로 정리
- 예: 비즈니스 모델 비교, 비용/노력/수익 분석, 단계별 프로세스

### 4. 결론
- 핵심 메시지 재강조
- 행동 촉구 (Action Call)
- 독자에게 다음 단계 제시

### 5. CTA (Call-to-Action)
- 자연스럽게 추가 자료나 커뮤니티 안내
- 과도하지 않게, 간결하게

## 작성 원칙

### 스타일
1. 전문적이면서도 접근 가능한 톤: 지나치게 학술적이거나 딱딱하지 않게
2. 명확하고 간결한 문장: 불필요한 수식어 최소화
3. 구체적인 정보 우선: 추상적인 표현보다 구체적인 예시와 수치
4. 실용성 강조: 독자가 바로 적용할 수 있는 정보 제공

### 피해야 할 것
- 과도한 볼드체 사용 금지
- 이모티콘 사용 금지
- 과장된 표현이나 클릭베이트성 문구 금지
- bullet point나 리스트 과다 사용 금지 (표와 번호 섹션으로 대체)
- 영상 내용을 그대로 나열하기 금지

### 반드시 포함할 것
- 번호가 매겨진 명확한 구조
- 각 섹션의 설명적 제목
- 구체적인 도구명, 회사명, 수치
- 실제 사례와 예시
- 독자가 실행할 수 있는 단계별 가이드
- 적절한 표를 활용한 정보 정리

### 톤 & 보이스
- 권위 있지만 친근함
- 교육적이지만 지루하지 않음
- 긍정적이지만 현실적
- 독자를 존중하며 대화하듯
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
        r'youtube\.com/live/',
    ]
    for pattern in youtube_patterns:
        if re.search(pattern, user_input, re.IGNORECASE):
            return 'youtube', user_input

    return 'article', user_input


def _cookie_configs():
    """쿠키 설정 후보 목록 반환. cookies.txt 파일이 있으면 최우선."""
    configs = []
    if os.path.exists(COOKIES_FILE):
        configs.append(('file', {'cookiefile': COOKIES_FILE}))
    for browser in ['chrome', 'edge', 'firefox']:
        configs.append((browser, {'cookiesfrombrowser': (browser, None, None, None)}))
    return configs


def _download_audio_ytdlp(video_id, cookie_opts):
    """yt-dlp + 지정된 쿠키 설정으로 오디오 파일을 다운로드합니다."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_base = os.path.join(SCRIPT_DIR, f"temp_audio_{video_id}")

    # 라이브 진행 중 여부 확인
    with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, **cookie_opts}) as ydl:
        try:
            info_only = ydl.extract_info(url, download=False)
        except Exception:
            info_only = None

    if info_only and info_only.get('is_live'):
        raise ValueError("현재 진행 중인 라이브 방송입니다. 방송 종료 후 다시 시도해주세요.")

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        'outtmpl': out_base + '.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        **cookie_opts,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info:
            ext = info.get('ext', 'mp4')
            p = f"{out_base}.{ext}"
            if os.path.exists(p):
                return p

    candidates = [f for f in glob.glob(f"{out_base}.*")
                  if not f.endswith(('.vtt', '.json', '.py'))]
    return candidates[0] if candidates else None


def _transcribe_with_gemini_audio(audio_path):
    """Gemini API로 오디오 파일을 한국어 텍스트로 변환합니다.
    18MB 이하: inline bytes / 초과: File API 사용.
    """
    from google.genai import types as gtypes

    ext = os.path.splitext(audio_path)[1].lower()
    mime_map = {
        '.m4a': 'audio/mp4',  '.mp4': 'audio/mp4',  '.aac': 'audio/aac',
        '.webm': 'audio/webm', '.mp3': 'audio/mpeg',
        '.opus': 'audio/ogg',  '.ogg': 'audio/ogg',  '.wav': 'audio/wav',
    }
    mime_type = mime_map.get(ext, 'audio/mp4')
    file_size = os.path.getsize(audio_path)
    mb = file_size // (1024 * 1024)
    print(f"  -> 오디오 크기: {mb}MB, Gemini 음성 변환 시작...")

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = ('이 오디오를 한국어로 전사해주세요. '
              '발화 내용을 그대로 텍스트로 변환하고, '
              '타임스탬프·화자 구분 없이 순수 텍스트만 출력하세요.')

    if file_size <= 18 * 1024 * 1024:
        with open(audio_path, 'rb') as f:
            encoded = base64.b64encode(f.read()).decode('utf-8')
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[{
                'parts': [
                    {'inline_data': {'mime_type': mime_type, 'data': encoded}},
                    {'text': prompt},
                ]
            }]
        )
    else:
        print(f"  -> File API 업로드 중 ({mb}MB)...")
        uploaded = client.files.upload(
            path=audio_path,
            config=gtypes.UploadFileConfig(mime_type=mime_type),
        )
        for _ in range(30):
            fi = client.files.get(name=uploaded.name)
            if fi.state.name == 'ACTIVE':
                break
            time.sleep(2)

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[uploaded, prompt],
        )
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

    return response.text.strip() if response.text else ''


def _parse_vtt(vtt_path):
    """VTT 자막 파일을 평문 텍스트로 변환합니다."""
    text_parts = []
    try:
        with open(vtt_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith('WEBVTT') or '-->' in line or re.match(r'^\d+$', line):
                continue
            line = re.sub(r'<[^>]+>', '', line)
            if line:
                text_parts.append(line)
    except Exception:
        pass
    return ' '.join(text_parts)


def _parse_srt(srt_path):
    """SRT 자막 파일을 평문 텍스트로 변환합니다."""
    text_parts = []
    try:
        with open(srt_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        # 번호 줄, 타임코드 줄 제거
        blocks = re.split(r'\n\s*\n', content)
        for block in blocks:
            lines = block.strip().splitlines()
            for line in lines:
                line = line.strip()
                if re.match(r'^\d+$', line):
                    continue
                if re.match(r'\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->', line):
                    continue
                line = re.sub(r'<[^>]+>', '', line)
                if line:
                    text_parts.append(line)
    except Exception:
        pass
    return ' '.join(text_parts)


def parse_subtitle_file(path):
    """업로드된 자막 파일(.vtt/.srt/.txt)을 텍스트로 변환합니다."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.vtt':
        return _parse_vtt(path)
    elif ext == '.srt':
        return _parse_srt(path)
    else:
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        except Exception:
            return ''


def get_transcript(video_id):
    """유튜브 영상의 자막을 텍스트로 추출합니다.
    공개/일부공개: YouTubeTranscriptApi 사용.
    회원전용 등 실패 시: yt-dlp + 브라우저 쿠키로 재시도.
    """
    print("[1/4] 유튜브 자막 추출 중...")

    # 1차: 표준 API (공개·일부공개 영상)
    try:
        transcript = YouTubeTranscriptApi().fetch(video_id, languages=['ko', 'en'])
        text = " ".join([t.text for t in transcript])
        print(f"  -> 자막 추출 완료 ({len(text)}자)")
        return text
    except Exception as e:
        print(f"  -> 표준 API 실패: {e}")

    # 2차: yt-dlp + 쿠키 (cookies.txt 우선 → 브라우저 순)
    print("  -> yt-dlp로 재시도 (쿠키 사용)...")
    url = f"https://www.youtube.com/watch?v={video_id}"
    sub_base = os.path.join(SCRIPT_DIR, f"temp_sub_{video_id}")

    for label, cookie_opts in _cookie_configs():
        try:
            ydl_opts = {
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['ko', 'en'],
                'skip_download': True,
                'quiet': True,
                'no_warnings': True,
                'outtmpl': sub_base,
                **cookie_opts,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            for lang in ['ko', 'en', 'ko-orig']:
                sub_file = f"{sub_base}.{lang}.vtt"
                if os.path.exists(sub_file):
                    text = _parse_vtt(sub_file)
                    try:
                        os.remove(sub_file)
                    except Exception:
                        pass
                    if text:
                        print(f"  -> yt-dlp({label}) 자막 추출 완료 ({len(text)}자)")
                        return text
        except Exception as e:
            print(f"  -> yt-dlp({label}) 실패: {e}")
            continue

    for f in glob.glob(f"{sub_base}*.vtt"):
        try:
            os.remove(f)
        except Exception:
            pass

    # 3차: 오디오 다운로드 + Gemini 음성 변환
    print("  -> 오디오 다운로드 + Gemini 음성 변환 시도...")
    for label, cookie_opts in _cookie_configs():
        audio_path = None
        try:
            print(f"  -> [{label}] 오디오 다운로드 중...")
            audio_path = _download_audio_ytdlp(video_id, cookie_opts)
            if not audio_path or not os.path.exists(audio_path):
                print(f"  -> [{label}] 오디오 파일 없음, 다음 시도")
                continue
            text = _transcribe_with_gemini_audio(audio_path)
            if text and len(text) > 50:
                print(f"  -> Gemini 음성 변환 완료 ({len(text)}자)")
                return text
            print(f"  -> [{label}] 변환 결과 너무 짧음, 다음 시도")
        except Exception as e:
            print(f"  -> [{label}] 오디오 변환 실패: {e}")
        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except Exception:
                    pass

    print("  -> 모든 방법으로 자막 추출 실패")
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


def generate_threads_post(source_text):
    """소스 텍스트로 스레드(Threads)용 연속 포스트를 생성합니다."""
    print("  -> 스레드용 글 생성 중...")
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""당신은 비즈니스/AI 분야 Threads 콘텐츠 작성자입니다.
아래 내용을 바탕으로 Threads에 올릴 연속 포스트(스레드)를 한국어로 작성하세요.

규칙:
- 3~5개의 연속 포스트로 구성
- 각 포스트는 500자 이내
- 첫 포스트: 독자의 시선을 잡는 핵심 질문 또는 충격적 사실로 시작
- 중간 포스트: 핵심 인사이트를 하나씩 전개 (번호 없이, 자연스럽게)
- 마지막 포스트: 독자가 바로 적용할 수 있는 한 가지 행동 제안
- 이모티콘 사용 금지
- 해시태그 금지
- 각 포스트는 "---" 구분선으로 분리

원본 내용:
{source_text[:3000]}

위 내용을 바탕으로 Threads 스레드를 작성하세요.
반드시 <THREADS>스레드 내용</THREADS> 태그로 감싸주세요."""

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        match = re.search(r'<THREADS>(.*?)</THREADS>', response.text, re.DOTALL)
        return match.group(1).strip() if match else response.text.strip()
    except Exception as e:
        print(f"  -> 스레드 글 생성 실패: {e}")
        return ""


# ===================================================================
# 8. 영상 프레임 추출
# ===================================================================

def _download_youtube_thumbnails(url, image_count=4):
    """영상 다운로드가 막혔을 때 YouTube 공개 썸네일을 이미지 폴백으로 사용합니다."""
    video_id = extract_video_id(url)
    if not video_id:
        return []

    thumbnail_names = [
        'maxresdefault.jpg',
        'sddefault.jpg',
        'hqdefault.jpg',
        'mqdefault.jpg',
        'default.jpg',
    ]
    image_paths = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    for name in thumbnail_names:
        if len(image_paths) >= image_count:
            break
        thumb_url = f"https://i.ytimg.com/vi/{video_id}/{name}"
        path = os.path.join(SCRIPT_DIR, f"frame_{len(image_paths) + 1}.jpg")
        try:
            req = urllib.request.Request(thumb_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read()
            if len(data) < 1024:
                continue
            with open(path, 'wb') as f:
                f.write(data)
            frame = cv2.imread(path)
            if frame is None:
                os.remove(path)
                continue
            image_paths.append(os.path.abspath(path))
        except Exception as e:
            print(f"     - 썸네일 폴백 실패({name}): {e}")

    if image_paths:
        print(f"  -> 영상 다운로드 대신 YouTube 썸네일 {len(image_paths)}장을 사용합니다.")
    return image_paths


def _capture_youtube_browser_frames(url, image_count=4):
    """Capture distinct YouTube player frames when yt-dlp video download is blocked."""
    if image_count <= 0:
        return []

    print(f"  -> yt-dlp 대체: 브라우저로 YouTube 장면 {image_count}장 캡처 시도")
    options = webdriver.ChromeOptions()
    options.add_argument("--window-size=1280,900")
    options.add_argument("--mute-audio")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = None
    image_paths = []
    try:
        driver = webdriver.Chrome(options=options)
        driver.get(url)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "video"))
        )
        time.sleep(3)

        try:
            driver.execute_script("""
                var btns = Array.from(document.querySelectorAll('button'));
                var accept = btns.find(function(b) {
                    return /동의|Accept|I agree/i.test((b.innerText || '').trim());
                });
                if (accept) accept.click();
            """)
            time.sleep(1)
        except Exception:
            pass

        duration = driver.execute_script("""
            var v = document.querySelector('video');
            return v && isFinite(v.duration) ? v.duration : 0;
        """) or 0
        if duration <= 0:
            duration = max(60, image_count * 20)

        for i in range(1, image_count + 1):
            ratio = i / (image_count + 1)
            second = max(3, min(duration - 3, duration * ratio))
            try:
                driver.execute_script("""
                    var v = document.querySelector('video');
                    if (!v) return;
                    v.muted = true;
                    v.pause();
                    v.currentTime = arguments[0];
                """, second)
                time.sleep(2)
                video = driver.find_element(By.CSS_SELECTOR, "video")
                path = os.path.join(SCRIPT_DIR, f"frame_{i}.jpg")
                video.screenshot(path)
                frame = cv2.imread(path)
                if frame is None or frame.size == 0:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    continue
                image_paths.append(os.path.abspath(path))
                print(f"     - 브라우저 캡처 {i}/{image_count}: {int(second)}초")
            except Exception as e:
                print(f"     - 브라우저 캡처 실패({i}/{image_count}): {e}")
    except Exception as e:
        print(f"  -> 브라우저 장면 캡처 실패: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    if image_paths:
        print(f"  -> 브라우저 장면 캡처 완료: {len(image_paths)}장")
    return image_paths


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
    errors = []
    for cookie_label, cookie_opts in _cookie_configs():
        if download_success:
            break
        print(f"  -> 쿠키 설정 '{cookie_label}'로 다운로드 시도")
        for idx, fmt in enumerate(format_list):
            if download_success:
                break
            print(f"     - 포맷 '{fmt}' 시도")
            current_filename = os.path.join(SCRIPT_DIR, f'temp_video_{cookie_label}_{idx}.mp4')
            options = {
                'format': fmt, 'outtmpl': current_filename,
                'quiet': True, 'no_warnings': True, 'nocheckcertificate': True,
                # quiet만으로는 진행바가 stderr로 계속 찍혀 로그가 도배된다
                'noprogress': True,
                **cookie_opts,
            }
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    ydl.download([url])
                if os.path.exists(current_filename) and os.path.getsize(current_filename) > 0:
                    download_success = True
                    downloaded_filename = current_filename
                    print(f"  -> 영상 다운로드 성공: {cookie_label} / {fmt}")
                else:
                    errors.append(f"{cookie_label}/{fmt}: output file missing")
            except Exception as e:
                errors.append(f"{cookie_label}/{fmt}: {e}")
                print(f"     - 실패: {e}")

    if not download_success:
        print("[오류] 모든 포맷으로 유튜브 영상 다운로드에 실패했습니다.")
        if errors:
            print("  -> 마지막 실패 원인:")
            for err in errors[-3:]:
                print(f"     - {err}")
        browser_frames = _capture_youtube_browser_frames(url, image_count)
        if browser_frames:
            return browser_frames
        return _download_youtube_thumbnails(url, image_count)

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


def _js_insert_text(driver, text):
    """클립보드 + ActionChains Ctrl+V로 텍스트 삽입 (OS 포커스 불필요)."""
    pyperclip.copy(text)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
    time.sleep(0.2)


def paste_with_formatting(driver, text, formatting_config):
    """[BOLD] 마커가 있으면 Ctrl+B로 볼드 적용하며 삽입합니다."""
    bold_enabled = formatting_config.get('bold_enabled', False)

    if not bold_enabled or '[BOLD]' not in text:
        clean = re.sub(r'\[BOLD\](.*?)\[/BOLD\]', r'\1', text)
        _js_insert_text(driver, clean)
        return

    parts = re.split(r'(\[BOLD\].*?\[/BOLD\])', text)
    for part in parts:
        if not part:
            continue
        bold_match = re.match(r'\[BOLD\](.*?)\[/BOLD\]', part)
        if bold_match:
            clean = bold_match.group(1)
            ActionChains(driver).key_down(Keys.CONTROL).send_keys('b').key_up(Keys.CONTROL).perform()
            time.sleep(0.1)
            _js_insert_text(driver, clean)
            ActionChains(driver).key_down(Keys.CONTROL).send_keys('b').key_up(Keys.CONTROL).perform()
            time.sleep(0.1)
        else:
            _js_insert_text(driver, part)


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
    """에디터 커서를 문서 최하단 + 인용구 밖으로 이동합니다."""
    try:
        driver.execute_script(_JS_SE_ROOT + """
            var editor = __seRoot();
            if (!editor) return;

            editor.focus();
            editor.scrollTop = editor.scrollHeight;

            // 마지막 섹션이 인용구면 그 다음에 빈 섹션 추가
            var sections = editor.querySelectorAll('.se-section');
            var lastSection = sections[sections.length - 1];
            if (lastSection && lastSection.classList.contains('se-section-quotation')) {
                var newSection = document.createElement('div');
                newSection.className = 'se-section se-section-text se-l-default';
                newSection.innerHTML =
                    '<div class="se-module se-module-text">' +
                    '<p class="se-text-paragraph se-text-paragraph-align-">' +
                    '<span class="se-ff-system se-fs15">​</span></p></div>';
                editor.appendChild(newSection);
                var span = newSection.querySelector('span');
                if (span) {
                    span.focus();
                    var r = document.createRange();
                    r.setStart(span, 0);
                    r.collapse(true);
                    var s = window.getSelection();
                    s.removeAllRanges();
                    s.addRange(r);
                }
                return;
            }

            // 마지막 인용구 다음 요소에 커서 배치
            var quotes = editor.querySelectorAll('.se-section-quotation');
            if (quotes.length > 0) {
                var lastQ = quotes[quotes.length - 1];
                var nextEl = lastQ.nextElementSibling;
                if (nextEl) {
                    var para = nextEl.querySelector('.se-text-paragraph') || nextEl;
                    para.focus();
                    var r2 = document.createRange();
                    r2.selectNodeContents(para);
                    r2.collapse(false);
                    var s2 = window.getSelection();
                    s2.removeAllRanges();
                    s2.addRange(r2);
                    return;
                }
            }

            // 에디터 끝으로 커서 이동
            var r3 = document.createRange();
            r3.selectNodeContents(editor);
            r3.collapse(false);
            var s3 = window.getSelection();
            s3.removeAllRanges();
            s3.addRange(r3);
        """)
    except Exception:
        ActionChains(driver).key_down(Keys.CONTROL).send_keys(Keys.END).key_up(Keys.CONTROL).perform()
    time.sleep(0.3)

    # JS 커서 설정 후 WebElement 클릭으로 WebDriver 포커스 획득
    try:
        all_sections = driver.find_elements(By.CSS_SELECTOR, '.se-section')
        for sec in reversed(all_sections):
            cls = sec.get_attribute('class') or ''
            if 'se-section-quotation' not in cls:
                paras = sec.find_elements(By.CSS_SELECTOR, '.se-text-paragraph')
                if paras:
                    paras[-1].click()
                    time.sleep(0.1)
                    break
    except Exception:
        pass

    # 아직 인용구 안이면 ActionChains로 강제 탈출
    if _is_cursor_inside_blockquote(driver):
        ActionChains(driver).key_down(Keys.CONTROL).send_keys(Keys.END).key_up(Keys.CONTROL).perform()
        time.sleep(0.2)
        ActionChains(driver).send_keys(Keys.ARROW_DOWN * 5).perform()
        time.sleep(0.2)
        ActionChains(driver).send_keys(Keys.RETURN).perform()
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

    # (5) 새로 생긴 인용구의 본문 영역을 WebElement 클릭으로 포커스
    try:
        time.sleep(0.5)
        new_quotes = driver.find_elements(By.CSS_SELECTOR, '.se-section-quotation')
        if len(new_quotes) > quote_count_before:
            new_quote = new_quotes[-1]
            paras = new_quote.find_elements(
                By.CSS_SELECTOR, '.se-quotation-content .se-text-paragraph')
            if not paras:
                paras = new_quote.find_elements(By.CSS_SELECTOR, '.se-text-paragraph')
            if paras:
                paras[0].click()
                time.sleep(0.3)
    except Exception:
        pass

    # (6) 소제목 삽입
    _js_insert_text(driver, heading_text)
    time.sleep(0.8)

    # (7) 인용구 탈출 — JS로 인용구 뒤에 새 텍스트 섹션 삽입 후 WebElement 클릭
    escaped = False
    try:
        quotes = driver.find_elements(By.CSS_SELECTOR, '.se-section-quotation')
        if len(quotes) > quote_count_before:
            last_q = quotes[-1]
            # 인용구 다음 텍스트 섹션을 찾거나 새로 DOM에 삽입
            target_para = driver.execute_script("""
                var lastQ = arguments[0];
                var next = lastQ.nextElementSibling;
                if (next && !next.classList.contains('se-section-quotation')) {
                    return next.querySelector('.se-text-paragraph') || next;
                }
                var newSec = document.createElement('div');
                newSec.className = 'se-section se-section-text se-l-default';
                newSec.innerHTML =
                    '<div class="se-module se-module-text">' +
                    '<p class="se-text-paragraph se-text-paragraph-align-">' +
                    '<span class="se-ff-system se-fs15">​</span></p></div>';
                lastQ.parentNode.insertBefore(newSec, lastQ.nextSibling);
                return newSec.querySelector('.se-text-paragraph');
            """, last_q)
            if target_para:
                target_para.click()
                time.sleep(0.3)
                escaped = not _is_cursor_inside_blockquote(driver)
    except Exception as e:
        print(f"  -> 인용구 탈출 전략 A 실패: {e}")

    if not escaped and _is_cursor_inside_blockquote(driver):
        _move_cursor_to_end(driver)
        time.sleep(0.3)

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
                _js_insert_text(driver, f"■ {heading_text}")
                ActionChains(driver).send_keys(Keys.RETURN).perform()
                time.sleep(0.3)

            # 인용구 탈출 확인 후 잔여 본문 삽입
            if extra_body:
                _move_cursor_to_end(driver)
                if _is_cursor_inside_blockquote(driver):
                    print("  -> [경고] 인용구 내부 감지, 강제 탈출 시도")
                    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                    time.sleep(0.1)
                    ActionChains(driver).send_keys(Keys.ARROW_DOWN * 5).perform()
                    time.sleep(0.1)
                    ActionChains(driver).send_keys(Keys.RETURN * 2).perform()
                    time.sleep(0.3)
                paste_with_formatting(driver, extra_body, formatting_config)
                time.sleep(0.5)
                ActionChains(driver).send_keys(Keys.RETURN * 2).perform()
                time.sleep(0.5)
        else:
            # 일반 텍스트: 인용구 밖 확인 후 삽입
            _move_cursor_to_end(driver)
            if _is_cursor_inside_blockquote(driver):
                print("  -> [경고] 일반텍스트 삽입 전 인용구 내부 감지, 강제 탈출")
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.1)
                ActionChains(driver).send_keys(Keys.ARROW_DOWN * 5).perform()
                time.sleep(0.1)
                ActionChains(driver).send_keys(Keys.RETURN * 2).perform()
                time.sleep(0.3)
            paste_with_formatting(driver, part, formatting_config)
            time.sleep(0.5)
            ActionChains(driver).send_keys(Keys.RETURN * 2).perform()
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
            var editor = document.querySelector('.se-components-wrap')
                      || document.querySelector('.se-content')
                      || document.querySelector('.se-container');
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
# 9-2. 글씨체(폰트/크기) 적용
# ===================================================================

# 스마트에디터 본문 루트를 찾는 JS 조각.
#
# ★ 중요: 이 에디터에는 contenteditable 이 '숨겨진 입력 프록시' 하나뿐이고
#   (클래스 없는 17x790 빈 DIV) 그건 본문이 아니다. 실제 본문은
#   .se-content > section.se-canvas > article.se-components-wrap 아래에 있다.
#   예전 코드가 contenteditable 을 본문으로 착각해서 전체선택/폰트/이미지 개수가
#   전부 조용히 실패했다.
_JS_SE_ROOT = """
function __seRoot() {
    return document.querySelector('.se-components-wrap')
        || document.querySelector('.se-content')
        || document.querySelector('.se-container')
        || null;
}
"""


def _se_root_present(driver):
    """본문 루트를 찾을 수 있는지 확인한다."""
    try:
        return bool(driver.execute_script(_JS_SE_ROOT + "return !!__seRoot();"))
    except Exception:
        return False


def _dump_toolbar(driver, tag):
    """폰트 지정 실패 시 툴바 HTML을 파일로 덤프합니다 (셀렉터 수정용)."""
    try:
        html = driver.execute_script("""
            var tb = document.querySelector('[class*="se-toolbar"]')
                  || document.querySelector('[class*="toolbar"]');
            return tb ? tb.outerHTML : '(toolbar not found)';
        """)
        path = os.path.join(SCRIPT_DIR, f'editor_toolbar_dump_{tag}.html')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html or '')
        print(f"  -> [디버그] 툴바 HTML 덤프: {path}")
    except Exception as e:
        print(f"  -> [디버그] 툴바 덤프 실패: {e}")


def _native_click(driver, el):
    """실제 마우스 클릭으로 누른다.

    JS의 el.click()은 mousedown 을 발생시키지 않는다. 스마트에디터 툴바는
    mousedown 에서 본문 선택 영역을 보존하므로, JS 클릭으로 누르면
    '전체 선택'이 풀린 채 서식이 적용되어 아무 효과가 없다.
    """
    try:
        el.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            return False


def _open_toolbar_dropdown(driver, kind):
    """툴바의 글꼴('font') 또는 글자크기('size') 드롭다운을 엽니다."""
    js = """
    var kind = arguments[0];
    var sel = (kind === 'font')
        ? '.se-font-family-toolbar-button'
        : '.se-font-size-code-toolbar-button';
    var el = document.querySelector(sel);
    if (el) {
        var r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) return el;
    }
    // 클래스가 바뀐 경우를 대비한 폭넓은 탐색
    var pats = (kind === 'font')
        ? ['font-family', 'fontfamily', 'fontname']
        : ['font-size', 'fontsize'];
    var cands = document.querySelectorAll('button, a[role="button"], [role="combobox"]');
    for (var i = 0; i < cands.length; i++) {
        var c = cands[i];
        var sig = ((c.className || '') + ' ' +
                   (c.getAttribute('data-log') || '')).toLowerCase();
        for (var j = 0; j < pats.length; j++) {
            if (sig.indexOf(pats[j]) !== -1) {
                var rr = c.getBoundingClientRect();
                if (rr.width > 0 && rr.height > 0) return c;
            }
        }
    }
    return null;
    """
    try:
        el = driver.execute_script(js, kind)
        if el is None:
            return None
        return _native_click(driver, el)
    except Exception as e:
        print(f"  -> 드롭다운 열기 실패({kind}): {e}")
        return None


def _pick_dropdown_option(driver, label):
    """열려 있는 드롭다운에서 항목을 클릭하고, 실제로 선택됐는지까지 확인합니다.

    스마트에디터 옵션 버튼은 라벨 텍스트가 **두 번 반복**된다.
        <button data-value="nanumsquareneo">나눔스퀘어 네오나눔스퀘어 네오</button>
        <button data-value="fs15">1515선택됨</button>
    그래서 텍스트 완전일치로 찾으면 버튼은 못 찾고 안쪽 <span>을 클릭하게 되는데,
    span 클릭은 핸들러가 안 걸려서 '눌렀지만 아무 일도 안 일어나는' 상태가 된다.
    반드시 data-value 를 가진 button 만 클릭한다.
    """
    js = """
    var want = String(arguments[0]).replace(/\\s+/g, '');
    var btns = document.querySelectorAll('button[data-value]');
    for (var i = 0; i < btns.length; i++) {
        var el = btns[i];
        var r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;          // 닫힌 드롭다운
        var dv = (el.getAttribute('data-value') || '');
        var t = (el.textContent || '').replace(/\\s+/g, '').replace(/선택됨$/, '');
        // 라벨이 두 번 반복되므로 앞 절반도 후보로 본다
        var half = (t.length % 2 === 0) ? t.slice(0, t.length / 2) : t;
        if (t === want || half === want || dv === want || dv === 'fs' + want) {
            return el;
        }
    }
    return null;
    """
    try:
        el = driver.execute_script(js, label)
        if el is None:
            return None
        dv = el.get_attribute('data-value')
        # JS 클릭이 아니라 실제 마우스 클릭 — 선택 영역이 보존돼야 서식이 먹는다
        return dv if _native_click(driver, el) else None
    except Exception as e:
        print(f"  -> 드롭다운 항목 선택 실패('{label}'): {e}")
        return None


def _font_class_stats(driver):
    """본문 span 의 실제 서식 클래스 분포를 센다.

    스마트에디터는 글자에 se-ff-<글꼴값> / se-fs<크기> 클래스를 붙인다.
    툴바 라벨이 아니라 이 분포가 '진짜로 적용됐는지'의 근거다.
    """
    try:
        return driver.execute_script(_JS_SE_ROOT + """
            var ed = __seRoot();
            if (!ed) return null;
            var ff = {}, fs = {};
            ed.querySelectorAll('span').forEach(function(s) {
                var t = (s.textContent || '').trim();
                if (!t) return;                       // 빈 span 은 세지 않는다
                (s.className || '').split(/\\s+/).forEach(function(c) {
                    if (c.indexOf('se-ff-') === 0) ff[c] = (ff[c] || 0) + 1;
                    else if (/^se-fs\\d+$/.test(c)) fs[c] = (fs[c] || 0) + 1;
                });
            });
            return {ff: ff, fs: fs};
        """)
    except Exception:
        return None


def _close_editor_flyouts(driver):
    """에디터 위에 떠 있는 레이어를 닫는다.

    '글감 검색'(se-flayer-unified-search) 같은 패널이 열려 있으면 본문을 덮어서
    문단 클릭이 전부 가로채인다 → 전체선택·커서이동·인용구 탈출이 통째로 실패한다.
    ESC 로 먼저 닫아보고, 그래도 남아 있는 것만 숨긴다.
    """
    try:
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.3)
    except Exception:
        pass
    try:
        hidden = driver.execute_script("""
            var names = [];
            document.querySelectorAll('[class*="se-flayer"]').forEach(function(el) {
                var r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    el.style.display = 'none';
                    names.push((el.className || '').toString().slice(0, 40));
                }
            });
            return names;
        """)
        if hidden:
            print(f"  -> 에디터 위 레이어 {len(hidden)}개 닫음: {hidden}")
    except Exception:
        pass


def _selectall_editor_body(driver):
    """본문 전체를 선택한다.

    ★ 반드시 '실제 마우스 클릭 + 실제 Ctrl+A' 여야 한다.
      JS Range 로 만든 선택은 스마트에디터가 자기 선택으로 인정하지 않아서
      서식이 하나도 안 붙는다 (버튼은 눌리고 툴바 표시도 바뀌는데 글자는 그대로).

    ※ window.getSelection() 은 0 을 돌려준다 — 에디터가 자체 선택 모델을 쓰기
      때문이며, 선택 실패가 아니다. 성공 여부는 결과 클래스로만 판정할 것.
    """
    _close_editor_flyouts(driver)   # 덮고 있는 패널부터 치운다
    try:
        paras = driver.find_elements(By.CSS_SELECTOR, '.se-text-paragraph')
        if not paras:
            print("  -> [주의] 본문 문단(.se-text-paragraph)을 못 찾음")
            return 0

        # 문단끼리 겹쳐 있어 클릭이 가로채이는 경우가 있다.
        # 뒤에서부터 여러 후보를 시도하고, 그래도 안 되면 JS 포커스로 폴백한다.
        clicked = False
        for p in list(reversed(paras))[:6]:
            try:
                if not p.is_displayed():
                    continue
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", p)
                time.sleep(0.3)
                p.click()
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            # 클릭이 전부 막히면 JS 로 포커스만 준다.
            # (선택 자체는 이어지는 실제 Ctrl+A 가 만든다)
            try:
                driver.execute_script("""
                    var ps = document.querySelectorAll('.se-text-paragraph');
                    if (ps.length) ps[ps.length - 1].focus();
                """)
                print("  -> 문단 클릭이 모두 막혀 JS 포커스로 대체")
            except Exception:
                pass

        time.sleep(0.4)
        ActionChains(driver).key_down(Keys.CONTROL).send_keys('a') \
                            .key_up(Keys.CONTROL).perform()
        time.sleep(0.5)
        return len(paras)
    except Exception as e:
        print(f"  -> [주의] 본문 전체 선택 실패: {str(e)[:100]}")
        return 0


def _current_toolbar_label(driver, kind):
    """툴바 토글 버튼에 현재 표시된 값(=적용된 글꼴/크기)을 읽는다."""
    sel = ('.se-font-family-toolbar-button' if kind == 'font'
           else '.se-font-size-code-toolbar-button')
    try:
        return driver.execute_script("""
            var el = document.querySelector(arguments[0]);
            if (!el) return null;
            var t = (el.textContent || '').replace(/\\s+/g, '');
            return t.replace(/서체변경$/, '').replace(/글자크기변경$/, '');
        """, sel)
    except Exception:
        return None


def apply_editor_font(driver, family, size, select_all=False, tag='start'):
    """에디터 글씨체/크기를 지정합니다.

    select_all=False → 지금부터 입력되는 텍스트에 적용 (본문 쓰기 직전에 호출)
    select_all=True  → 본문 전체를 선택해 일괄 적용 (글 다 쓴 뒤 호출)

    붙여넣기(Ctrl+V)로 본문을 넣으면 입력 전에 잡아둔 서식이 풀린다.
    그래서 '다 쓴 뒤 전체 선택 → 일괄 적용'이 실제로 효과를 내는 경로다.
    """
    if not family and not size:
        return

    where = '전체 선택 후 일괄' if select_all else '입력 전 기본값'
    print(f"  -> 글씨체 적용({where}): {family or '(유지)'} / {size or '(유지)'}")

    try:
        for kind, want in (('font', family), ('size', size)):
            if not want:
                continue
            name = '글꼴' if kind == 'font' else '글자크기'

            # 전체선택이 클릭 가로채기로 실패할 때가 있어 최대 3회 시도한다.
            attempts = 3 if select_all else 1
            for n_try in range(1, attempts + 1):
                # 서식은 '선택된 범위'에 적용된다. 항목을 고를 때마다 선택이
                # 풀릴 수 있으므로 글꼴/크기 각각 직전에 다시 전체 선택한다.
                if select_all:
                    _close_editor_flyouts(driver)
                    _selectall_editor_body(driver)
                    time.sleep(0.3)

                picked = None
                if _open_toolbar_dropdown(driver, kind):
                    time.sleep(0.6)
                    picked = _pick_dropdown_option(driver, want)
                    time.sleep(0.6)

                shown = (_current_toolbar_label(driver, kind) or '')

                if not select_all:
                    # 입력 전 단계에서는 본문이 비어 있어 클래스로 검증할 수 없다
                    print(f"  -> {name} 선택: {picked or '실패'} (툴바 '{shown}')")
                    break

                # ★ 진짜 검증: 본문 span 의 실제 클래스 분포를 본다
                stats = _font_class_stats(driver) or {'ff': {}, 'fs': {}}
                if kind == 'font':
                    buckets = stats['ff']
                    key = f"se-ff-{picked}" if picked else None
                else:
                    buckets = stats['fs']
                    key = f"se-fs{want}"

                total = sum(buckets.values())
                hit = buckets.get(key, 0) if key else 0

                if total and hit == total:
                    print(f"  -> {name} 적용 확인: {want} — 본문 {total}개 span 전부 {key}")
                    break
                if hit:
                    others = {k: v for k, v in buckets.items() if k != key}
                    print(f"  -> [부분적용] {name} {hit}/{total} 만 {key}. 나머지: {others}")
                    break   # 인용구가 자체 서체를 고수하는 정상 케이스
                if n_try < attempts:
                    print(f"  -> [{n_try}/{attempts}] {name} 적용 안 됨. 다시 시도...")
                    time.sleep(1.0)
                    continue

                print(f"  -> [실패] {name} '{want}' 본문에 안 붙음 "
                      f"(클릭={picked!r}, 툴바='{shown}', 실제 분포={buckets})")
                _dump_toolbar(driver, tag)

    except Exception as e:
        print(f"  -> 글씨체 적용 중 예외(무시하고 계속): {e}")
    finally:
        # 전체 선택 상태를 반드시 푼다 — 선택된 채로 키 입력이 들어가면 본문이 통째로 날아감
        if select_all:
            try:
                driver.execute_script("window.getSelection().removeAllRanges();")
            except Exception:
                pass
            _move_cursor_to_end(driver)


def _count_editor_images(driver):
    """에디터 본문에 들어간 이미지 개수. 셀 수 없으면 -1 (절대 None 아님).

    파일 선택 레이어가 떠 있는 동안 execute_script 가 undefined 를 돌려줄 때가
    있어서, 숫자가 아니면 무조건 -1 로 눌러 담는다.
    """
    try:
        n = driver.execute_script(_JS_SE_ROOT + """
            var ed = __seRoot();
            if (!ed) return -1;
            // .se-section-image 만 센다. .se-image-resource 를 같이 세면
            // 이미지 1장이 2로 잡혀 개수가 두 배로 보고된다.
            var c = ed.querySelectorAll('.se-section-image').length;
            return (typeof c === 'number') ? c : -1;
        """)
        return int(n) if isinstance(n, (int, float)) else -1
    except Exception:
        return -1


def _suppress_native_file_dialog(driver):
    """파일 input 의 click() 을 가로채 OS '열기' 대화상자가 뜨지 않게 한다.

    '사진' 툴바 버튼을 누르면 에디터가 내부적으로 input.click() 을 호출해
    네이티브 파일 선택 창을 띄운다. 그 창은 브라우저의 자바스크립트 실행을
    통째로 멈춰세우기 때문에, 셀레니움이 아무것도 못 하고 화면에 창만 남는다.
    여기서 file input 의 click 만 무력화하고 그 엘리먼트를 잡아둔다.
    """
    try:
        driver.execute_script("""
            if (!window.__seFileClickPatched) {
                window.__seFileClickPatched = true;
                var orig = HTMLInputElement.prototype.click;
                window.__seOrigInputClick = orig;
                HTMLInputElement.prototype.click = function() {
                    if (this.type === 'file') {
                        window.__seLastFileInput = this;   // 창은 띄우지 않고 잡아만 둔다
                        return;
                    }
                    return orig.apply(this, arguments);
                };
            }
            window.__seLastFileInput = null;
        """)
        return True
    except Exception as e:
        print(f"  -> [주의] 파일 대화상자 차단 실패: {str(e)[:80]}")
        return False


def _find_file_input(driver):
    """업로드용 file input 을 찾는다 (클릭으로 가로챈 것 우선)."""
    try:
        el = driver.execute_script("return window.__seLastFileInput || null;")
        if el:
            return el
    except Exception:
        pass
    inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
    return inputs[-1] if inputs else None


def upload_image(driver, img_abs, attempts=2, wait=20):
    """이미지를 업로드하고 실제로 본문에 들어갔는지 확인한다.

    OS 파일 대화상자를 띄우지 않는다 — 그게 뜨면 JS가 멈춰서 복구가 안 된다.
    """
    name = os.path.basename(img_abs)
    before = _count_editor_images(driver)

    for n in range(1, attempts + 1):
        try:
            _suppress_native_file_dialog(driver)

            # 이미 DOM에 input 이 있으면 버튼을 아예 누르지 않는다
            fi = _find_file_input(driver)
            if fi is None:
                try:
                    driver.find_element(By.CSS_SELECTOR, ".se-image-toolbar-button").click()
                except Exception:
                    driver.find_element(
                        By.XPATH,
                        "//button[contains(@class, 'image') or "
                        ".//span[contains(text(), '사진')]]"
                    ).click()
                time.sleep(1.0)
                fi = _find_file_input(driver)

            if fi is None:
                print(f"  -> [{n}/{attempts}] file input을 못 찾음 ({name}). 재시도...")
                time.sleep(1.5)
                continue

            driver.execute_script(
                "arguments[0].style.cssText='display:block!important;"
                "opacity:0.01!important;position:fixed;top:0;left:0;"
                "width:1px;height:1px;';", fi)
            fi.send_keys(img_abs)
        except Exception as e:
            print(f"  -> [{n}/{attempts}] 업로드 시도 실패 ({name}): {str(e)[:100]}")
            time.sleep(1.5)
            continue

        # 본문에 실제로 반영될 때까지 기다린다
        for _ in range(wait):
            time.sleep(1)
            now = _count_editor_images(driver)
            if now < 0:
                continue                      # 지금은 셀 수 없음 — 다음 초에 다시
            if now > before or (before < 0 and now > 0):
                print(f"  -> 이미지 업로드 확인: {name} (본문 이미지 {now}장)")
                return True

        print(f"  -> [{n}/{attempts}] {name} 이 본문에 안 들어갔음. 재시도...")

    print(f"  -> [실패] 이미지 업로드 포기: {name}")
    return False


def select_board(driver, board_name):
    """글을 올릴 게시판(카테고리)을 고른다.

    글쓰기 화면은 기본이 '게시판을 선택해 주세요.' 상태다. 이걸 안 고르면
    발행이 막히거나 의도치 않은 게시판으로 올라간다.
    목록이 길어 화면 밖에 있는 항목은 스크롤해서 클릭한다.
    """
    if not board_name:
        return True

    want = board_name.replace(' ', '')
    print(f"  -> 게시판 선택: {board_name}")

    # 1) 게시판 드롭다운 열기 (첫 번째 FormSelectButton = 게시판, 두 번째는 말머리)
    try:
        trigger = driver.execute_script("""
            var boxes = document.querySelectorAll('.FormSelectButton');
            if (!boxes.length) return null;
            return boxes[0].querySelector('button');
        """)
        if trigger is None:
            print("  -> [실패] 게시판 선택 버튼을 못 찾았습니다.")
            return False
        _native_click(driver, trigger)
        time.sleep(1.0)
    except Exception as e:
        print(f"  -> [실패] 게시판 드롭다운 열기: {str(e)[:100]}")
        return False

    # 2) 목록에서 이름이 일치하는 항목 찾기 (보이지 않아도 찾는다)
    try:
        opt = driver.execute_script("""
            var want = arguments[0];
            var opts = document.querySelectorAll('button.option, li.item button');
            for (var i = 0; i < opts.length; i++) {
                var t = (opts[i].textContent || '').replace(/\\s+/g, '');
                if (t === want) return opts[i];
            }
            return null;
        """, want)
    except Exception as e:
        print(f"  -> [실패] 게시판 목록 탐색: {str(e)[:100]}")
        return False

    if opt is None:
        names = []
        try:
            names = driver.execute_script("""
                var out = [];
                document.querySelectorAll('button.option').forEach(function(b){
                    out.push((b.textContent||'').trim());
                });
                return out;
            """)
        except Exception:
            pass
        print(f"  -> [실패] '{board_name}' 게시판이 목록에 없습니다.")
        if names:
            print(f"  -> 선택 가능한 게시판: {names}")
        return False

    # 3) 화면 안으로 스크롤한 뒤 실제 클릭
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opt)
        time.sleep(0.4)
        _native_click(driver, opt)
        time.sleep(1.0)
    except Exception as e:
        print(f"  -> [실패] 게시판 항목 클릭: {str(e)[:100]}")
        return False

    # 4) 실제로 바뀌었는지 확인
    try:
        label = driver.execute_script("""
            var boxes = document.querySelectorAll('.FormSelectButton');
            if (!boxes.length) return '';
            var b = boxes[0].querySelector('button');
            return b ? (b.textContent || '').trim() : '';
        """)
    except Exception:
        label = ''

    if label.replace(' ', '') == want:
        print(f"  -> 게시판 확인: {label}")
        return True

    print(f"  -> [실패] 게시판이 바뀌지 않았습니다 (현재 '{label}')")
    return False


def publish_post(driver, wait=20):
    """글을 실제로 발행(등록)한다.

    ★ '임시등록' 을 절대 누르지 않는다. 예전 폴백은 class 에 register 가 들어간
      아무 버튼이나 눌렀는데, 임시저장 버튼 클래스에도 register 가 들어가서
      발행하려다 임시저장될 수 있었다.
    """
    _close_editor_flyouts(driver)
    before_url = driver.current_url

    js = """
    var cands = document.querySelectorAll('button, a, [role="button"]');
    for (var i = 0; i < cands.length; i++) {
        var el = cands[i];
        var t = (el.textContent || '').replace(/\\s+/g, '');
        // '등록' 정확히 일치만. '임시등록'/'임시저장'/'저장' 은 제외.
        if (t !== '등록') continue;
        var r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) return el;
    }
    return null;
    """
    try:
        el = driver.execute_script(js)
    except Exception as e:
        print(f"  -> 발행 버튼 검색 실패: {str(e)[:100]}")
        el = None

    if el is None:
        print("  -> [실패] '등록' 버튼을 찾지 못했습니다.")
        _dump_toolbar(driver, 'publish')
        return False

    if not _native_click(driver, el):
        print("  -> [실패] '등록' 버튼 클릭 실패")
        return False

    print("  -> '등록' 클릭. 발행 확인 대기 중...")
    for _ in range(wait):
        time.sleep(1)
        try:
            now = driver.current_url
        except Exception:
            continue
        # 글쓰기 화면을 벗어나면 발행된 것
        if 'write' not in now and now != before_url:
            print(f"  -> 발행 확인: {now}")
            return now

    print(f"  -> [주의] 발행 여부를 확인하지 못했습니다 (URL 그대로: {driver.current_url})")
    return False


def _publish_source_key(source_url):
    if not source_url:
        return ""
    try:
        video_id = extract_video_id(source_url)
    except Exception:
        video_id = None
    if video_id:
        return f"youtube:{video_id}"
    return "url:" + source_url.strip().lower()


def _publish_record_id(source_key):
    return hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:24]


def _load_published_records():
    try:
        with open(PUBLISHED_RECORD_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"  -> [warning] could not read published record file: {e}")
        return {}


def _save_published_records(records):
    tmp_path = PUBLISHED_RECORD_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, PUBLISHED_RECORD_FILE)


def _begin_publish_guard(source_url):
    source_key = _publish_source_key(source_url)
    if not source_key:
        return {"enabled": False}

    record_id = _publish_record_id(source_key)
    records = _load_published_records()
    existing = records.get(record_id)
    if existing:
        print("[중단] 이미 발행한 원본 URL이라 카페 중복 발행을 하지 않습니다.")
        print(f"  -> 기존 글: {existing.get('article_url', '')}")
        return None

    os.makedirs(PUBLISH_LOCK_DIR, exist_ok=True)
    lock_path = os.path.join(PUBLISH_LOCK_DIR, record_id + ".lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time.time() - os.path.getmtime(lock_path)
            if age > 6 * 60 * 60:
                os.remove(lock_path)
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                print("[중단] 같은 원본 URL의 카페 발행 작업이 이미 진행 중입니다.")
                print(f"  -> lock: {lock_path}")
                return None
        except FileExistsError:
            print("[중단] 같은 원본 URL의 카페 발행 작업이 이미 진행 중입니다.")
            return None

    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"pid={os.getpid()}\n")
        f.write(f"time={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"source={source_key}\n")

    records = _load_published_records()
    existing = records.get(record_id)
    if existing:
        try:
            os.remove(lock_path)
        except OSError:
            pass
        print("[중단] 이미 발행한 원본 URL이라 카페 중복 발행을 하지 않습니다.")
        print(f"  -> 기존 글: {existing.get('article_url', '')}")
        return None

    return {
        "enabled": True,
        "record_id": record_id,
        "source_key": source_key,
        "source_url": source_url,
        "lock_path": lock_path,
    }


def _mark_published_source(publish_guard, title, article_url):
    if not publish_guard or not publish_guard.get("enabled"):
        return
    records = _load_published_records()
    records[publish_guard["record_id"]] = {
        "source_key": publish_guard["source_key"],
        "source_url": publish_guard["source_url"],
        "title": title,
        "article_url": article_url,
        "published_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_published_records(records)


def _release_publish_guard(publish_guard):
    if not publish_guard or not publish_guard.get("enabled"):
        return
    try:
        os.remove(publish_guard["lock_path"])
    except OSError:
        pass


def save_as_draft(driver):
    """작성 중인 글을 임시저장한다.

    '등록' 옆의 '저장'(임시저장) 버튼을 누른다. 셀렉터가 확실치 않아
    텍스트 기반으로 넓게 찾고, 실패하면 툴바를 덤프해 원인을 남긴다.
    """
    print("  -> 임시저장 중...")
    js = """
    var cands = document.querySelectorAll('button, a, [role="button"]');
    for (var i = 0; i < cands.length; i++) {
        var el = cands[i];
        var t = (el.textContent || '').replace(/\\s+/g, '');
        var cls = (el.className || '') + ' ' + (el.getAttribute('data-log') || '');
        // '임시등록'/'임시저장'/'저장' — 단 '등록'(발행)은 절대 누르지 않는다
        var isDraft = (t === '임시저장' || t === '임시등록' || t === '저장'
                       || /save|temp|draft/i.test(cls));
        var isPublish = (t === '등록' || /register|publish/i.test(cls));
        if (isDraft && !isPublish) {
            var r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) { el.click(); return t || cls; }
        }
    }
    return null;
    """
    try:
        hit = driver.execute_script(js)
    except Exception as e:
        print(f"  -> [주의] 임시저장 버튼 검색 실패: {e}")
        hit = None

    if hit:
        print(f"  -> 임시저장 버튼 클릭: '{hit}'")
        time.sleep(3)
        # 확인 레이어가 뜨면 닫아준다
        try:
            driver.execute_script("""
                var b = document.querySelector('.btn_confirm, .se-popup-button-confirm');
                if (b) b.click();
            """)
        except Exception:
            pass
        return True

    print("  -> [주의] 임시저장 버튼을 찾지 못했습니다. 창에서 직접 눌러주세요.")
    _dump_toolbar(driver, 'draft')
    return False


def insert_link_block(driver, url, as_card=True, wait=4):
    """URL을 붙여넣어 네이버 자동 링크(as_card=True면 미리보기 카드)로 만듭니다."""
    if not url:
        return
    _js_insert_text(driver, url)
    time.sleep(0.3)
    if as_card:
        # 줄 끝에서 Enter → 네이버가 OG 카드로 자동 변환
        ActionChains(driver).send_keys(Keys.RETURN).perform()
        time.sleep(wait)
    else:
        # 스페이스 → 카드 없이 하이퍼링크만
        ActionChains(driver).send_keys(Keys.SPACE).perform()
        time.sleep(0.5)


# ===================================================================
# 9-3. 브라우저 / 네이버 로그인
# ===================================================================

# 댓글봇(chrome_profile_commentbot)과 반드시 분리한다.
# 같은 user-data-dir 를 두 프로세스가 잡으면 프로필이 잠겨 드라이버 생성이 실패한다.
PUBLISHER_PROFILE_DIR = os.path.join(SCRIPT_DIR, 'chrome_profile_publisher')

# 창 없이 돌릴지 여부. 진입점에서 --headless 로 켠다.
PUBLISHER_HEADLESS = False


def _kill_orphan_publisher_chrome():
    """발행용 프로필을 점유 중인 고아 크롬만 종료한다 (자가치유).

    비정상 종료로 남은 크롬이 프로필을 잠그면 다음 실행이 통째로 죽는다.
    ※ 사용자의 일반 크롬은 건드리지 않는다 — publisher 프로필 프로세스만.
    """
    if os.name != 'nt':
        return
    import subprocess
    profile_name = os.path.basename(PUBLISHER_PROFILE_DIR)
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -like '*" + profile_name + "*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps],
            timeout=20, capture_output=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except Exception as e:
        print(f"  -> [경고] 고아 크롬 정리 실패(무시하고 진행): {e}")


def make_publisher_driver():
    """발행 전용 프로필로 Chrome 드라이버를 만든다.

    프로필 폴더에 네이버 세션 쿠키가 남으므로, 최초 1회 로그인 후에는
    재로그인도 캡챠도 거의 없다. (댓글봇이 몇 달째 쓰는 것과 같은 방식)

    ※ 좀비 크롬 정리는 '드라이버 생성이 실패했을 때만' 한다.
      크롬은 종료할 때 쿠키를 디스크에 flush 하는데, 시작할 때마다 선제적으로
      강제 종료하면 직전 실행에서 만든 로그인 세션이 저장되기 전에 날아가서
      매번 다시 로그인해야 한다.
    """
    def _build():
        options = webdriver.ChromeOptions()
        options.add_argument("--window-size=1100,900")
        if PUBLISHER_HEADLESS:
            # ★ 쓰지 말 것 (2026-08 실측): 헤드리스 크롬은 시스템 클립보드를
            #   읽지 못해 pyperclip + Ctrl+V 로 넣는 본문이 통째로 누락된다.
            #   창 모드 31개 span vs 헤드리스 18개 (인용구만 들어가고 본문 소실).
            #   플래그는 나중에 크롬/네이버가 바뀌면 재검증하려고 남겨둔 것.
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
        options.add_argument(f"--user-data-dir={PUBLISHER_PROFILE_DIR}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        return webdriver.Chrome(options=options)

    try:
        driver = _build()
    except Exception as e:
        print(f"  -> 드라이버 생성 실패({str(e)[:80]}). 프로필 점유 크롬 정리 후 재시도...")
        _kill_orphan_publisher_chrome()
        time.sleep(2)
        driver = _build()
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        })
    except Exception:
        pass
    return driver


def _logged_in_ui_visible(driver):
    """현재 떠 있는 페이지에서 로그인 흔적을 찾는다. 페이지를 이동하지 않는다."""
    try:
        return bool(driver.execute_script("""
            var text = (document.body && document.body.innerText) || '';
            if (/로그아웃|내정보/.test(text)) return true;
            return !!document.querySelector(
                'a[href*="nid.naver.com/user2/help/myInfo"], ' +
                'a[href*="nid.naver.com/nidlogin.logout"]'
            );
        """))
    except Exception:
        return False


def _naver_session_alive(driver):
    """NID cookies first, then visible logged-in Naver UI.

    naver.com 으로 이동해서 확인하므로 '로그인 전 1회 점검'에만 쓴다.
    사람이 로그인하는 동안 반복 호출하면 입력 화면을 계속 날려버린다.
    """
    try:
        driver.get("https://www.naver.com")
        time.sleep(2)
        cookies = {c["name"] for c in driver.get_cookies()}
        if "NID_AUT" in cookies or "NID_SES" in cookies:
            return True
        return _logged_in_ui_visible(driver)
    except Exception:
        return False


def _handle_naver_device_confirm(driver):
    """Continue past Naver's new-device confirmation without registration."""
    try:
        return bool(driver.execute_script("""
            var text = (document.body && document.body.innerText) || '';
            var onConfirm = location.href.indexOf('deviceConfirm') >= 0
                || text.indexOf('새로운 기기') >= 0
                || text.indexOf('자주 사용하는 기기') >= 0;
            if (!onConfirm) return false;

            var nodes = Array.from(document.querySelectorAll('a, button, input[type="button"], input[type="submit"]'));
            for (var i = 0; i < nodes.length; i++) {
                var el = nodes[i];
                var label = (el.innerText || el.textContent || el.value || '').replace(/\\s+/g, '');
                if (label === '등록안함' || label === '등록하지않음' || label === '나중에') {
                    el.click();
                    return true;
                }
            }
            return false;
        """))
    except Exception:
        return False


def ensure_naver_login(driver, wait_minutes=5):
    """로그인 상태를 보장한다.

    반환값: 이번에 '새로' 로그인했으면 True, 저장된 세션을 쓴 거면 False.
    호출자는 True 일 때 브라우저를 한 번 깨끗이 닫아 쿠키를 디스크에 flush 해야 한다.

    로그인 버튼 셀렉터에 의존하지 않는다 — 네이버가 자주 바꾼다.
    """
    if _naver_session_alive(driver):
        print("  -> 저장된 세션으로 이미 로그인됨 (캡챠 불필요).")
        return False

    print("  -> 로그인 세션이 없습니다. 로그인 페이지로 이동합니다.")
    driver.get("https://nid.naver.com/nidlogin.login")
    time.sleep(2)

    # 아이디/비밀번호는 채워준다 (send_keys는 봇 감지되므로 클립보드 붙여넣기)
    for sel, val in (("#id", NAVER_ID), ("#pw", NAVER_PW)):
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            pyperclip.copy(val)
            el.click()
            el.send_keys(Keys.CONTROL, 'v')
            time.sleep(0.5)
        except Exception:
            print(f"  -> [주의] {sel} 입력란을 찾지 못했습니다. 직접 입력해주세요.")

    # '로그인 상태 유지' — 다음 실행부터 로그인 자체를 건너뛰게 해준다
    try:
        driver.execute_script("""
            var k = document.querySelector('#keep, input[id="keep"], .keep_check input');
            if (k && !k.checked) { (k.closest('label') || k).click(); }
        """)
    except Exception:
        pass

    # 로그인 버튼은 셀렉터가 자주 바뀌므로 텍스트/타입으로 폭넓게 찾는다
    try:
        clicked = driver.execute_script("""
            var el = document.querySelector('#log\\\\.login, button[type="submit"], .btn_login');
            if (!el) {
                var all = document.querySelectorAll('button, a, input[type="submit"]');
                for (var i = 0; i < all.length; i++) {
                    var t = (all[i].textContent || all[i].value || '').trim();
                    if (t === '로그인') { el = all[i]; break; }
                }
            }
            if (el) { el.click(); return true; }
            return false;
        """)
        if not clicked:
            print("  -> [주의] 로그인 버튼을 못 찾았습니다. 창에서 직접 눌러주세요.")
    except Exception as e:
        print(f"  -> [주의] 로그인 버튼 클릭 실패: {e}. 창에서 직접 눌러주세요.")

    print(f"  -> 로그인 완료를 기다립니다 (최대 {wait_minutes}분). "
          "캡챠/2차 인증이 뜨면 창에서 직접 처리해주세요.")
    # 대기 중에는 절대 페이지를 이동시키지 않는다.
    # _naver_session_alive() 는 첫 줄에서 naver.com 으로 driver.get() 을 하므로,
    # 대기 루프에서 부르면 사람이 캡챠/2차 인증을 입력하는 도중에 매번 화면을
    # 날려버려 로그인을 영영 끝낼 수 없다. 쿠키와 현재 DOM 만 본다.
    started = time.time()
    deadline = started + wait_minutes * 60
    notified = False
    while time.time() < deadline:
        try:
            if _handle_naver_device_confirm(driver):
                print("  -> 새 기기 확인 화면에서 '등록안함'을 자동 선택했습니다.")
                time.sleep(2)
            cookies = {c["name"] for c in driver.get_cookies()}
            if "NID_AUT" in cookies or "NID_SES" in cookies:
                print("  -> 로그인 성공.")
                return True   # 새로 로그인함 → 호출자가 세션을 디스크에 저장해야 함
            if _logged_in_ui_visible(driver):
                print("  -> 로그인 UI 확인 완료.")
                return True
        except Exception:
            pass
        if not notified and time.time() - started > 20:
            print("  -> 아직 로그인 대기 중입니다... (창에서 직접 완료해주세요)")
            notified = True
        time.sleep(2)

    raise Exception(f"로그인 실패: {wait_minutes}분 내에 로그인이 완료되지 않았습니다.")


# ===================================================================
# 10. 네이버 카페 포스팅
# ===================================================================

def post_to_naver_cafe(title, body, image_paths, optional_config, source_url=None,
                       draft=False):
    """Selenium으로 네이버 카페에 글을 자동 등록합니다 (OS 포커스 불필요)."""
    print("[4/4] 네이버 카페 포스팅 시작...")
    if not image_paths:
        print("[중단] 카페 이미지가 0장이라 임시등록/발행을 하지 않습니다.")
        return False

    publish_guard = _begin_publish_guard(source_url)
    if publish_guard is None:
        return False

    # 본문에서 하이라이트 키워드 추출 (마커 제거)
    highlight_keywords = []
    if optional_config.get('highlight_enabled', False):
        body, highlight_keywords = extract_highlight_keywords(body)
        if highlight_keywords:
            print(f"  -> 하이라이트 대상 키워드: {highlight_keywords}")

    try:
        driver = make_publisher_driver()

        # ── 1단계: 네이버 로그인 ──
        if ensure_naver_login(driver):
            # 크롬은 '정상 종료'할 때 쿠키를 디스크에 쓴다. 여기서 한 번 깨끗이
            # 닫아주지 않으면, 다음 실행 전에 크롬이 강제종료될 경우 방금 만든
            # 로그인 세션이 통째로 날아가 매번 다시 로그인하게 된다.
            print("  -> 로그인 세션을 디스크에 저장하는 중 (브라우저 재시작)...")
            driver.quit()
            time.sleep(2)
            driver = make_publisher_driver()
            if _naver_session_alive(driver):
                print("  -> 세션 저장 확인. 다음 실행부터는 로그인을 건너뜁니다.")
            else:
                print("  -> [주의] 세션이 저장되지 않았습니다. 다음 실행에서 "
                      "다시 로그인해야 할 수 있습니다.")

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

        # ── 게시판(카테고리) 선택 ── 제목보다 먼저. 안 고르면 발행이 막힌다.
        board = optional_config.get('board_name', '')
        if board and not select_board(driver, board):
            raise Exception(
                f"게시판 '{board}' 를 선택하지 못했습니다. "
                "config.ini 의 [NAVER] board_name 을 확인하세요.")

        title_input = driver.find_element(By.CSS_SELECTOR, ".textarea_input")
        title_input.send_keys(title)
        time.sleep(1)

        # ── 4단계: 본문 + 이미지 교차 삽입 ──
        chunks = body.split("[IMAGE_HERE]")
        title_input.send_keys(Keys.TAB)
        time.sleep(1)

        # 에디터 본문 영역 활성화
        time.sleep(0.5)

        # 본문 입력 전에 글씨체를 먼저 지정해두면 이후 입력이 그 서식을 따라간다
        apply_editor_font(
            driver,
            optional_config.get('font_family', ''),
            optional_config.get('font_size', ''),
            select_all=False, tag='start'
        )

        failed_images = []
        for i, chunk in enumerate(chunks):
            chunk = chunk.strip()
            if chunk:
                paste_chunk_with_blockquotes(driver, chunk, optional_config)

            # 이미지 업로드
            if i < len(image_paths):
                _move_cursor_to_end(driver)
                if _is_cursor_inside_blockquote(driver):
                    ActionChains(driver).send_keys(Keys.ARROW_DOWN * 5).perform()
                    time.sleep(0.3)

                img_abs = os.path.abspath(image_paths[i])
                # 검증+재시도 포함. 실패해도 글 전체를 죽이지는 않는다.
                # (구버전의 pyautogui 폴백은 제거했다 — 창이 포커스를 잃은 상태에서
                #  엉뚱한 프로그램에 경로를 붙여넣고 엔터를 치는 위험한 동작이었다)
                try:
                    ok = upload_image(driver, img_abs)
                except Exception as e:
                    print(f"  -> 이미지 처리 중 예외({os.path.basename(img_abs)}): "
                          f"{str(e)[:120]}")
                    ok = False
                if not ok:
                    failed_images.append(os.path.basename(img_abs))

                _move_cursor_to_end(driver)
                ActionChains(driver).send_keys(Keys.RETURN).perform()
                time.sleep(0.5)

        # ── 5단계: CTA 삽입 ──
        if optional_config.get('cta_enabled'):
            cta_text = optional_config.get('cta_text', '')
            cta_link_url = optional_config.get('cta_link_url', '')
            cta_link_text = optional_config.get('cta_link_text', '')

            if cta_text or cta_link_text:
                print("  -> CTA 문구 삽입 중...")
                _move_cursor_to_end(driver)
                ActionChains(driver).send_keys(Keys.RETURN * 3).perform()
                time.sleep(0.5)

                if cta_text:
                    _js_insert_text(driver, cta_text)
                    ActionChains(driver).send_keys(Keys.RETURN * 2).perform()
                    time.sleep(0.3)

                if cta_link_url:
                    if cta_link_text:
                        _js_insert_text(driver, cta_link_text)
                        ActionChains(driver).send_keys(Keys.RETURN).perform()
                        time.sleep(0.3)
                    # URL 뒤에 Enter → 네이버가 미리보기 카드로 자동 변환
                    insert_link_block(driver, cta_link_url, as_card=True)

                time.sleep(1)

        # ── 5.5단계: 맨 하단 원본 영상 링크 ──
        if source_url:
            print("  -> 원본 영상 링크 삽입 중...")
            _move_cursor_to_end(driver)
            ActionChains(driver).send_keys(Keys.RETURN * 2).perform()
            time.sleep(0.3)

            label = optional_config.get('source_label', '▶ 원본 영상')
            if label:
                _js_insert_text(driver, label)
                ActionChains(driver).send_keys(Keys.RETURN).perform()
                time.sleep(0.3)

            insert_link_block(
                driver, source_url,
                as_card=optional_config.get('source_link_card', False)
            )

        # ── 6단계: 글씨체 일괄 재적용 ──
        if optional_config.get('font_apply_at_end', True) and (
                optional_config.get('font_family') or optional_config.get('font_size')):
            apply_editor_font(
                driver,
                optional_config.get('font_family', ''),
                optional_config.get('font_size', ''),
                select_all=True, tag='end'
            )

        # ── 7단계: 하이라이트 적용 (JS) ──
        if highlight_keywords and optional_config.get('highlight_enabled'):
            print("  -> 하이라이트 서식 적용 중...")
            apply_highlight_js(driver, highlight_keywords, optional_config.get('highlight_color', '#FFFF00'))

        # ── 8단계: 임시저장 또는 등록 ──
        if failed_images:
            print(f"\n  ** [경고] 이미지 {len(failed_images)}장이 본문에 안 들어갔습니다: "
                  f"{', '.join(failed_images)}")
            print("  ** 임시저장본에서 직접 확인하고 필요하면 수동으로 넣어주세요.\n")
        else:
            total = _count_editor_images(driver)
            shown = total if total >= 0 else '확인불가'
            print(f"  -> 본문 이미지 최종 {shown}장 (요청 {len(image_paths)}장)")

        if draft:
            save_as_draft(driver)
            print("\n임시저장 완료. 카페에서 내용 확인하고 직접 발행하세요.")
            print("브라우저는 열어둡니다. 확인 끝나면 창을 닫으세요.")
            return

        print("  -> 전체공개/퍼가기 여부는 카페 게시판 기본 설정을 따릅니다.")
        article_url = publish_post(driver)
        if not article_url:
            print("\n  ** [경고] 발행 버튼을 누르지 못했습니다. 글은 에디터에 그대로 있으니")
            print("  ** 창에서 직접 '등록'을 눌러주세요. 브라우저는 열어둡니다.")
            return
        _mark_published_source(publish_guard, title, article_url)

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
    finally:
        _release_publish_guard(publish_guard)


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
