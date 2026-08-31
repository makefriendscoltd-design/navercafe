# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

네이버 카페 자동 포스팅 도구. 유튜브 영상/뉴스기사/직접 텍스트를 Gemini AI로 블로그 칼럼으로 변환한 뒤 네이버 카페에 자동 발행한다. macOS에서는 Aside CLI와 Aside 브라우저 로그인 상태가 기본이며, Windows의 기존 Selenium 경로는 fallback으로 유지한다.

## Running

```bash
# CLI 모드 (터미널 기반, tkinter 팝업으로 입력)
python youtube_cafe_auto.py

# GUI 모드 (Tkinter 2탭 인터페이스: 실행/설정)
python cafe_gui.py

# YouTube 커뮤니티 작성창 채우기 (게시 버튼은 누르지 않음)
python youtube_community_auto.py --text-file post.txt --images card1.png card2.png

# YouTube 링크 → 카페/카드뉴스/실제 YouTube Shorts 3종 제작·발행
./run_content_link.command "https://youtu.be/VIDEO_ID"
```

## Dependencies

```bash
# 기본(Aside)
pip install -r requirements.txt

# Windows Selenium fallback만
pip install -r requirements-selenium.txt

# 선택 (뉴스 스크래핑 품질 향상)
pip install beautifulsoup4
```

Python 3.12+. Aside CLI는 Aside 앱의 Settings > Developers에서 설치하며, 코드는 비밀번호 대신 Aside 브라우저의 기존 로그인 상태만 재사용한다.

NotebookLM과 카드뉴스 통합 경로는 `.venv312`를 사용한다. 로컬 `config.ini`가
없으면 마이그레이션된 `~/orca/projects/ccidacafe/config.ini`를 읽는다.
`run_content_link.command`는 카페, YouTube 카드뉴스/커뮤니티, 실제 Shorts MP4를
모두 검증한 뒤 즉시 발행한다. 세 채널의 provider 완료 확인이 모두 있어야 성공으로
종료하며, 중간 실패는 `outputs/<run>/publish_status.json`에서 이어서 재시도한다.

## Architecture

**두 개의 진입점이 하나의 엔진을 공유하는 구조:**

- `youtube_cafe_auto.py` — 핵심 엔진 + CLI 진입점 (`if __name__ == "__main__"`)
- `cafe_gui.py` — Tkinter GUI 래퍼. `youtube_cafe_auto`를 import하여 동일 함수 호출

**파이프라인 흐름:**
```
소스 입력 → NotebookLM → 웹검색 사실확인 → 채널별 변환 → Aside 발행
```

**핵심 함수 체인 (youtube_cafe_auto.py):**

| 단계 | 함수 | 역할 |
|------|------|------|
| 설정 | `load_or_create_config()` → `load_optional_config()` | config.ini 로드/생성 마법사 |
| 감지 | `detect_source_type()` | URL 패턴으로 youtube/article/text 분류 |
| 추출 | `get_transcript()`, `scrape_article()`, `extract_frames()` | 소스별 텍스트/이미지 추출 |
| AI | `build_meta_prompt()` → `generate_blog_post()` | 메타프롬프트 조립 + Gemini 호출 |
| 포스팅 | `post_to_naver_cafe()` | 기본 Aside CLI, 선택적 Selenium fallback |
| YouTube 커뮤니티 | `youtube_community_auto.py` | Aside로 본문/이미지 입력, `--publish`일 때만 게시 |
| 첫 발행 승인 | `telegram_publish_approval.py` | 댓글봇 보고방과 분리된 `@ccida_bot` 개인 채팅에서 검수 후 카페·YouTube 발행 |
| 쇼츠 대본 | `notebooklm_shorts.py` | 전용 노트북 대본의 5번째 항목까지 + 영상 분 수 CTA |
| 쇼츠 영상 | `shorts_video.py` | 원본 프레임·한국어 내레이션·자막으로 1080x1920 MP4 생성/검증 |
| 쇼츠 업로드 | `aside_browser.upload_youtube_short()` | Aside headless로 Studio 업로드 후 video ID/Shorts URL 확인 |

## 채널별 고정 규칙

상세 수치와 fail-closed 검증은 `SHORTS_SPEC.md` 및 `content_production_policy.py`가 정본이다.

- 네이버 카페: `AI 자동화&수익화 정보` 게시판. 기존 `[BLOCKQUOTE]`,
  `[BOLD]`, `[HIGHLIGHT]`, `[IMAGE_HERE]` 형식을 실제 SmartEditor 서식으로 재현한다.
  글 끝에는 AI 오프라인 스터디 안내와 패밀리데이 모집 공지
  `https://cafe.naver.com/westudyssat/4188`을 넣고, 그 아래 별도 문단에 원본 영상 링크를 넣는다.
  URL은 순차 타이핑하지 않고 실제 붙여넣기로 입력해 패밀리데이 OG 썸네일 카드와
  YouTube 미리보기 카드를 만들며, 원문 URL도 각각 본문에 남긴다.
- YouTube 커뮤니티: 긴 설명형 본문과 정사각 카드뉴스 10장을 한 번에 올린다.
  활성 채널이 `나민수 AI`인지 확인한 뒤 게시한다.
- 쇼츠: Aside `u0`의 기존 `민수대표님_숏폼` 응답만 쓰며 5번째 항목까지만 남긴다.
  원본 YouTube 영상 + 승인된 민수 촬영본 원형 PIP + 민수 ElevenLabs 단일 테이크의 V7
  구조만 허용한다. 카드뉴스/정지 프레임 혼합, 다른 얼굴·음성, 토큰 중간 자르기, 90px가
  아닌 헤드카피는 금지한다. 검증 증거가 없으면 Studio 업로드 전에 자동 중단한다.

## 발행 전 사실확인

원본의 모델명·버전·출시일·가격·벤치마크 수치·회사 주장과 `최신/최초/1위` 단정은
모두 미검증으로 보고 Google 검색으로 확인한다. 공식 벤더 페이지와 어긋나면 해당
주장만 고치거나 제거하며, 나머지 원고는 재작성하지 않는다. 수익 보장·허위 성과와
의학적 치료/완치 단정도 발행하지 않는다. 교정된 원고에서 카페 본문·카드뉴스·YouTube
본문을 다시 만들기 때문에 플랫폼 간 사실이 어긋나지 않아야 한다.

**AI 출력 마커 시스템:** Gemini가 생성하는 본문에는 커스텀 태그가 포함됨:
- `[IMAGE_HERE]` — 이미지 삽입 위치 (본문 split 기준)
- `[BLOCKQUOTE]...[/BLOCKQUOTE]` — 인용구 블록 (소제목)
- `[BOLD]...[/BOLD]` — 볼드 키워드
- `[HIGHLIGHT]...[/HIGHLIGHT]` — 하이라이트(음영) 키워드

**네이버 에디터 자동화 핵심:**
- macOS 기본 경로는 Aside CLI이며 제목·본문·이미지를 실제 SmartEditor 컨트롤에 넣는다.
- 이미지 경로 대신 바이트 payload를 넘겨 Aside 상대경로 업로드의 0바이트 문제를 피한다.
- 게시판·인용구·볼드·음영·이미지·원본 링크를 DOM에서 확인한 뒤에만 등록 버튼을 누른다.
- Selenium의 clipboard/pyautogui 경로는 Windows fallback으로만 유지한다.

## Configuration

`config.ini` (첫 실행 시 자동 생성). `[BROWSER] backend=aside|selenium`, `[NAVER]` 카페 URL(아이디/비밀번호는 Selenium fallback만), `[GEMINI]` API키, `[CTA]`, `[FORMATTING]`, `[PROMPT]`, `[CONTENT]`를 사용한다.

## Key Conventions

- 네이버 Smart Editor의 DOM 구조에 의존하는 CSS 셀렉터들이 많음 (`.textarea_input`, `.se-image-toolbar-button`, `.btn_register` 등). 네이버 에디터 업데이트 시 깨질 수 있음.
- Aside 경로는 쿠키·비밀번호를 읽지 않는다. 로그아웃 상태면 로그인 탭을 열고 중단한다.
- 외부 게시/댓글은 코드 검사만으로 검증하지 않는다. 실제 로그인 프로필에서 dry-run 후 별도 승인된 게시 테스트가 필요하다.
- `DEFAULT_WRITING_STYLE` 상수 (~120줄)가 기본 메타프롬프트. 사용자가 `[PROMPT].custom_instructions`를 설정하면 이것을 대체함.
- 임시 파일(`frame_*.jpg`, `temp_video*.mp4`)은 `cleanup_temp_files()`로 작업 후 정리.
- 모든 UI 텍스트는 한국어. 에러 메시지, 로그, tkinter 다이얼로그 포함.
