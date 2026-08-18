# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

네이버 카페 자동 포스팅 도구. 유튜브 영상/뉴스기사/직접 텍스트를 Gemini AI로 블로그 칼럼으로 변환한 뒤, Selenium으로 네이버 카페에 자동 발행한다. Windows 전용 (pyautogui, tkinter 파일 다이얼로그 의존).

## Running

```bash
# CLI 모드 (터미널 기반, tkinter 팝업으로 입력)
python youtube_cafe_auto.py

# GUI 모드 (Tkinter 2탭 인터페이스: 실행/설정)
python cafe_gui.py
```

## Dependencies

```bash
# 필수
pip install opencv-python youtube-transcript-api yt-dlp google-genai selenium pyperclip pyautogui

# 선택 (뉴스 스크래핑 품질 향상)
pip install beautifulsoup4
```

Python 3.12+. 별도의 requirements.txt/pyproject.toml 없음 — `youtube_cafe_auto.py` 상단 `check_dependencies()`가 런타임에 검사.

## Architecture

**두 개의 진입점이 하나의 엔진을 공유하는 구조:**

- `youtube_cafe_auto.py` — 핵심 엔진 + CLI 진입점 (`if __name__ == "__main__"`)
- `cafe_gui.py` — Tkinter GUI 래퍼. `youtube_cafe_auto`를 import하여 동일 함수 호출

**파이프라인 흐름:**
```
소스 입력 → 유형 감지(youtube/article/text) → 콘텐츠 추출 → Gemini AI 글 생성 → Selenium 카페 포스팅
```

**핵심 함수 체인 (youtube_cafe_auto.py):**

| 단계 | 함수 | 역할 |
|------|------|------|
| 설정 | `load_or_create_config()` → `load_optional_config()` | config.ini 로드/생성 마법사 |
| 감지 | `detect_source_type()` | URL 패턴으로 youtube/article/text 분류 |
| 추출 | `get_transcript()`, `scrape_article()`, `extract_frames()` | 소스별 텍스트/이미지 추출 |
| AI | `build_meta_prompt()` → `generate_blog_post()` | 메타프롬프트 조립 + Gemini 호출 |
| 포스팅 | `post_to_naver_cafe()` | Selenium + pyautogui로 카페 글 등록 |

**AI 출력 마커 시스템:** Gemini가 생성하는 본문에는 커스텀 태그가 포함됨:
- `[IMAGE_HERE]` — 이미지 삽입 위치 (본문 split 기준)
- `[BLOCKQUOTE]...[/BLOCKQUOTE]` — 인용구 블록 (소제목)
- `[BOLD]...[/BOLD]` — 볼드 키워드
- `[HIGHLIGHT]...[/HIGHLIGHT]` — 하이라이트(음영) 키워드

**네이버 에디터 자동화 핵심:**
- `pyperclip` + `Ctrl+V`로 입력 (send_keys는 봇 감지됨)
- `insert_blockquote()` — 인용구 버튼 클릭 → JS로 새 인용구 찾기 → 텍스트 붙여넣기 → 커서 탈출
- `apply_highlight_js()` — JS `document.execCommand('hiliteColor')`로 배경색 적용
- `_move_cursor_to_end()` — JS Range API로 커서를 인용구 밖 문서 끝으로 이동

## 발행 전 사실확인 (강제)

**어떤 경로로 요청이 들어오든 — 텔레그램 컨트롤러(`contents_master`), 직접 지시, 스크립트 실행 — 글을 저장하거나 발행하기 전에 웹검색으로 사실을 검증한다. 생략 불가.**

파이프라인은 노트북LM 원고를 그대로 받아쓰고, 원고는 원본 영상이 말한 걸 그대로 옮긴다. 몇 달 전 영상이 "최신 모델"이라고 부른 이름이 아무 검증 없이 발행까지 통과한다. 실제로 2026-08-17 발행분은 Opus 5·GPT-5.6이 이미 나온 뒤에 "Opus 4.6, GPT 5.4가 최신"이라고 나갔다.

- 원본에서 넘어온 **모델명·버전·출시일·가격·벤치마크 수치·회사 주장**은 전부 미검증으로 취급한다.
- 남길 항목마다 **실제 웹검색**을 돌린다. 기억으로 답하지 않는다 — 학습 컷오프가 글이 다루는 뉴스보다 오래됐다.
- 검색과 어긋나면 고치거나 뺀다. **오늘 벤더 공식 릴리스 페이지로 확인 안 한 버전 번호는 절대 남기지 않는다.**
- 그 외 문장은 원고 그대로 둔다. 재작성이 아니라 교정 패스다 ([[cafe-notebooklm-workflow]] 원칙 유지).
- 카드뉴스 슬라이드에도 같은 주장이 박혀 있다. 본문만 고치면 수정된 글 옆에 낡은 카드가 붙는다 — `04_cardnews_deck.json` 고치고 다시 렌더한다.
- 마커에 `factcheck=ok`(수정 없음) 또는 `factcheck=fixed`(수정함)를 실어 보고한다.
- **이미 승인된 본문**을 발행하는 단계에서 사실 오류를 발견하면, 임의로 고치지 말고 발행을 멈추고 `status=blocked reason=stale_fact:<내용>`으로 보고한다. 판단은 주인이 한다.

## Configuration

`config.ini` (첫 실행 시 자동 생성). 6개 섹션: `[NAVER]` 계정, `[GEMINI]` API키, `[CTA]` 유입링크, `[FORMATTING]` 볼드/하이라이트, `[PROMPT]` 커스텀 지시사항, `[CONTENT]` 글 길이/이미지 수.

## Key Conventions

- 네이버 Smart Editor의 DOM 구조에 의존하는 CSS 셀렉터들이 많음 (`.se-image-toolbar-button`, `.se-section-quotation`, `.se-quote-toolbar-button` 등). 네이버 에디터 업데이트 시 깨질 수 있음.
- `DEFAULT_WRITING_STYLE` 상수 (~120줄)가 기본 메타프롬프트. 사용자가 `[PROMPT].custom_instructions`를 설정하면 이것을 대체함.
- 임시 파일(`frame_*.jpg`, `temp_video*.mp4`)은 `cleanup_temp_files()`로 작업 후 정리.
- 모든 UI 텍스트는 한국어. 에러 메시지, 로그, tkinter 다이얼로그 포함.
