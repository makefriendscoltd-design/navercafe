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


def _download_audio_ytdlp(video_id, browser='chrome'):
    """yt-dlp + 브라우저 쿠키로 오디오 파일을 다운로드합니다."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_base = os.path.join(SCRIPT_DIR, f"temp_audio_{video_id}")

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        'outtmpl': out_base + '.%(ext)s',
        'cookiesfrombrowser': (browser, None, None, None),
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info:
            ext = info.get('ext', 'mp4')
            p = f"{out_base}.{ext}"
            if os.path.exists(p):
                return p

    # ext를 모를 때 glob으로 탐색
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
            line = re.sub(r'<[^>]+>', '', line)  # HTML 태그 제거
            if line:
                text_parts.append(line)
    except Exception:
        pass
    return ' '.join(text_parts)


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

    # 2차: yt-dlp + 브라우저 쿠키 (회원전용/로그인 필요 영상)
    print("  -> yt-dlp로 재시도 (브라우저 쿠키 사용)...")
    url = f"https://www.youtube.com/watch?v={video_id}"
    sub_base = os.path.join(SCRIPT_DIR, f"temp_sub_{video_id}")

    for browser in ['chrome', 'edge', 'firefox']:
        try:
            ydl_opts = {
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['ko', 'en'],
                'skip_download': True,
                'quiet': True,
                'no_warnings': True,
                'cookiesfrombrowser': (browser, None, None, None),
                'outtmpl': sub_base,
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
                        print(f"  -> yt-dlp({browser}) 자막 추출 완료 ({len(text)}자)")
                        return text
        except Exception as e:
            print(f"  -> yt-dlp({browser}) 실패: {e}")
            continue

    # 임시 자막 파일 정리
    for f in glob.glob(f"{sub_base}*.vtt"):
        try:
            os.remove(f)
        except Exception:
            pass

    # 3차: yt-dlp 오디오 다운로드 + Gemini 음성 변환
    # (회원전용·일부공개 영상처럼 캡션 파일이 없는 경우)
    print("  -> 오디오 다운로드 + Gemini 음성 변환 시도...")
    for browser in ['chrome', 'edge', 'firefox']:
        audio_path = None
        try:
            print(f"  -> [{browser}] 오디오 다운로드 중...")
            audio_path = _download_audio_ytdlp(video_id, browser)
            if not audio_path or not os.path.exists(audio_path):
                print(f"  -> [{browser}] 오디오 파일 없음, 다음 브라우저 시도")
                continue
            text = _transcribe_with_gemini_audio(audio_path)
            if text and len(text) > 50:
                print(f"  -> Gemini 음성 변환 완료 ({len(text)}자)")
                return text
            print(f"  -> [{browser}] 변환 결과 너무 짧음, 다음 시도")
        except Exception as e:
            print(f"  -> [{browser}] 오디오 변환 실패: {e}")
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


def _js_insert_text(driver, text):
    """포커스된 contenteditable에 OS 포커스 없이 텍스트를 삽입합니다."""
    driver.execute_script("document.execCommand('insertText', false, arguments[0]);", text)
    time.sleep(0.1)


def paste_with_formatting(driver, text, formatting_config):
    """[BOLD] 마커가 있으면 execCommand bold로 적용하며 JS로 삽입합니다."""
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
            driver.execute_script("document.execCommand('bold', false, null);")
            time.sleep(0.1)
            _js_insert_text(driver, clean)
            driver.execute_script("document.execCommand('bold', false, null);")
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
        driver.execute_script("""
            // 가장 바깥쪽 contenteditable(메인 에디터)을 찾는다
            var allEditable = document.querySelectorAll('[contenteditable="true"]');
            var editor = allEditable[0];
            for (var i = 1; i < allEditable.length; i++) {
                if (allEditable[i].contains(editor)) editor = allEditable[i];
            }
            if (!editor) return;

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

    # JS 탈출 후에도 인용구 안이면 ActionChains로 강제 탈출
    if _is_cursor_inside_blockquote(driver):
        ActionChains(driver).key_down(Keys.CONTROL).send_keys(Keys.END).key_up(Keys.CONTROL).perform()
        time.sleep(0.2)
        ActionChains(driver).send_keys(Keys.ARROW_DOWN * 5).perform()
        time.sleep(0.2)
        ActionChains(driver).send_keys(Keys.RETURN).perform()
        time.sleep(0.2)
        ActionChains(driver).send_keys(Keys.RETURN).perform()
        time.sleep(0.3)
        if _is_cursor_inside_blockquote(driver):
            ActionChains(driver).key_down(Keys.CONTROL).send_keys(Keys.END).key_up(Keys.CONTROL).perform()
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

    # (6) 소제목 삽입 (OS 포커스 불필요)
    _js_insert_text(driver, heading_text)
    time.sleep(0.8)

    # (7) 인용구 탈출 — 3단 전략
    time.sleep(0.2)

    # 전략 A: 인용구 다음 섹션을 ActionChains로 클릭
    try:
        quotes = driver.find_elements(By.CSS_SELECTOR, '.se-section-quotation')
        if len(quotes) > quote_count_before:
            last_q = quotes[-1]
            next_sec = driver.execute_script("return arguments[0].nextElementSibling;", last_q)
            if next_sec:
                ActionChains(driver).move_to_element(next_sec).click().perform()
            else:
                # 다음 섹션이 없으면 인용구 아래 50px 위치 클릭
                ActionChains(driver).move_to_element_with_offset(
                    last_q, 10, last_q.size['height'] // 2 + 30).click().perform()
    except Exception:
        pass
    time.sleep(0.4)

    # 전략 B: 아직 인용구 안이면 ActionChains 방향키로 탈출
    if _is_cursor_inside_blockquote(driver):
        ActionChains(driver).send_keys(Keys.END).perform()
        time.sleep(0.1)
        ActionChains(driver).send_keys(Keys.ARROW_DOWN * 5).perform()
        time.sleep(0.3)

    # 전략 C: JS로 새 섹션 생성 후 커서 이동
    if _is_cursor_inside_blockquote(driver):
        _move_cursor_to_end(driver)
        time.sleep(0.3)

    # 최후 수단: Enter 두 번
    if _is_cursor_inside_blockquote(driver):
        ActionChains(driver).send_keys(Keys.RETURN * 2).perform()
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
    """Selenium으로 네이버 카페에 글을 자동 등록합니다 (OS 포커스 불필요)."""
    print("[4/4] 네이버 카페 포스팅 시작...")

    # 본문에서 하이라이트 키워드 추출 (마커 제거)
    highlight_keywords = []
    if optional_config.get('highlight_enabled', False):
        body, highlight_keywords = extract_highlight_keywords(body)
        if highlight_keywords:
            print(f"  -> 하이라이트 대상 키워드: {highlight_keywords}")

    # Chrome을 우측 하단에 작게 띄워 다른 작업 방해 최소화
    options = webdriver.ChromeOptions()
    options.add_argument("--window-size=900,700")
    options.add_argument("--window-position=9999,0")   # 오른쪽 화면 밖
    driver = webdriver.Chrome(options=options)

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
                _move_cursor_to_end(driver)
                if _is_cursor_inside_blockquote(driver):
                    ActionChains(driver).send_keys(Keys.ARROW_DOWN * 5).perform()
                    time.sleep(0.3)

                img_abs = os.path.abspath(image_paths[i])
                upload_ok = False

                # 숨겨진 file input 직접 사용 (OS 다이얼로그 우회)
                try:
                    file_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
                    if file_inputs:
                        fi = file_inputs[0]
                        driver.execute_script(
                            "arguments[0].style.cssText='display:block!important;"
                            "opacity:0.01!important;position:fixed;top:0;left:0;"
                            "width:1px;height:1px;';", fi)
                        fi.send_keys(img_abs)
                        upload_ok = True
                        print(f"  -> 이미지 직접 업로드: {os.path.basename(img_abs)}")
                        time.sleep(5)
                        driver.execute_script("arguments[0].style.cssText='';", fi)
                except Exception as e:
                    print(f"  -> 직접 파일 입력 실패: {e}")

                if not upload_ok:
                    # Fallback: 툴바 버튼 → OS 파일 다이얼로그
                    try:
                        driver.find_element(By.CSS_SELECTOR, ".se-image-toolbar-button").click()
                    except Exception:
                        driver.find_element(
                            By.XPATH,
                            "//button[contains(@class, 'image') or .//span[contains(text(), '사진')]]"
                        ).click()
                    time.sleep(3)
                    pyperclip.copy(img_abs)
                    pyautogui.hotkey('ctrl', 'v')
                    time.sleep(1)
                    pyautogui.press('enter')
                    time.sleep(5)

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

                if cta_link_text and cta_link_url:
                    _js_insert_text(driver, f"{cta_link_text}\n{cta_link_url}")
                    time.sleep(0.5)
                elif cta_link_url:
                    _js_insert_text(driver, cta_link_url)
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
