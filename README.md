# 네이버 카페 자동화 프로젝트

네이버 카페 운영을 보조하는 로컬 자동화 모음입니다. Gemini로 카페 게시글을 생성해 Selenium으로 게시하고, 가입인사 게시판 댓글 감시, 텔레그램 보고, 회원 ID 추출, watchdog 재기동까지 한 프로젝트에서 운영합니다.

## 자동화 세션 역할

| 역할 | 파일 | 실행 명령 | 설명 |
| --- | --- | --- | --- |
| 콘텐츠 생성/카페 게시 CLI | `youtube_cafe_auto.py` | `python youtube_cafe_auto.py` | 유튜브 URL, 기사 URL, 직접 입력 텍스트를 분석해 Gemini로 카페 글을 만들고 네이버 카페에 게시합니다. |
| 데스크톱 GUI | `cafe_gui.py` | `python cafe_gui.py` | `youtube_cafe_auto.py` 기능을 Tkinter 화면에서 실행하고 설정합니다. |
| 브라우저 웹 UI | `server.py`, `static/index.html` | `python server.py` | `http://localhost:8000` 웹 화면에서 소스 입력, 이미지/자막 업로드, 미리보기 확인 후 게시합니다. |
| 가입인사 댓글 봇 | `comment_bot.py` | `python comment_bot.py` | 지정 게시판을 주기적으로 감시하고, 봇 시작 이후 새 글에 랜덤 댓글을 작성합니다. |
| 댓글 봇 watchdog | `watchdog_comment_bot.ps1`, `watchdog_comment_bot.vbs` | 작업 스케줄러에서 `watchdog_comment_bot.ps1` 호출 | 댓글 봇이 꺼지거나 하트비트가 멈춘 경우 좀비 Chrome/로그 잠금을 정리하고 백그라운드로 재시작합니다. |
| 단순 재시작 루프 | `run_comment_bot.bat` | `run_comment_bot.bat` | 댓글 봇이 종료되면 30초 뒤 다시 실행하는 보조 배치입니다. 현재는 작업 스케줄러 + watchdog 방식이 더 안정적인 운영 방식입니다. |
| 회원 ID 추출 | `extract_members.py` | `python extract_members.py "<회원관리 URL>"` | 카페 회원관리 페이지에서 회원 ID, 이메일 후보, 닉네임을 `members.csv`로 추출합니다. |
| 테스트/진단 | `test_*.py`, `test.py` | 파일별 직접 실행 | iframe, blockquote, 자막 등 특정 기능 검증용 스크립트입니다. |

## 설치

Python 3.12 이상과 Chrome이 필요합니다.

```bash
pip install opencv-python youtube-transcript-api yt-dlp google-genai selenium pyperclip pyautogui
pip install fastapi uvicorn python-multipart
pip install beautifulsoup4
```

`beautifulsoup4`는 기사/웹페이지 스크래핑 품질 개선용 선택 패키지입니다.

## 기본 설정

처음 실행하면 `config.ini`가 생성되거나 부족한 섹션이 보강됩니다. 이 파일에는 로그인 정보와 API 키가 들어가므로 Git에 올리지 않습니다.

주요 섹션:

| 섹션 | 용도 |
| --- | --- |
| `[NAVER]` | 네이버 ID, 비밀번호, 카페 URL |
| `[GEMINI]` | Gemini API 키 |
| `[CTA]` | 게시글에 넣을 CTA 문구와 링크 |
| `[FORMATTING]` | 볼드, 하이라이트 등 AI 마커 적용 설정 |
| `[PROMPT]` | Gemini 글쓰기 스타일 커스텀 지시문 |
| `[CONTENT]` | 글 길이, 이미지 개수 등 콘텐츠 생성 설정 |
| `[COMMENT_BOT]` | 댓글 봇 게시판 URL, 댓글 문구, 폴링 주기, 텔레그램 보고 설정 |
| `[MEMBER_EXTRACT]` | 회원관리 URL 저장용 선택 설정 |

## 실행 흐름

### 1. 카페 게시 자동화

```bash
python youtube_cafe_auto.py
```

소스 유형은 자동 감지됩니다.

- 유튜브: 자막을 가져오고 프레임 이미지를 추출합니다.
- 기사/웹페이지: 본문 텍스트를 스크래핑합니다.
- 직접 텍스트: 입력한 내용을 그대로 글 생성 소스로 씁니다.

AI가 생성한 본문에는 `[IMAGE_HERE]`, `[BLOCKQUOTE]`, `[BOLD]`, `[HIGHLIGHT]` 같은 마커가 포함될 수 있고, 게시 단계에서 네이버 스마트에디터 서식으로 변환됩니다.

### 2. GUI 또는 웹 UI

데스크톱 GUI:

```bash
python cafe_gui.py
```

웹 UI:

```bash
python server.py
```

웹 UI는 실행 후 `http://localhost:8000`을 열고, `/api/run`, `/api/logs/{task_id}`, `/api/preview/{task_id}`, `/api/confirm/{task_id}` API로 작업 상태와 미리보기를 제어합니다.

### 3. 댓글 봇 상시 감시

```bash
python comment_bot.py
```

처음 실행 시 현재 게시판 글 목록을 기준선으로 저장하고, 이후 새 글부터 댓글을 작성합니다. 이미 처리한 글 ID는 `comment_bot_seen.json`에 저장되어 재시작 후에도 중복 댓글을 줄입니다.

운영/진단 모드:

```bash
python comment_bot.py inspect
python comment_bot.py inspect 123
python comment_bot.py probe 123
python comment_bot.py test 123
python comment_bot.py backfill 2026-06-08
python comment_bot.py backfill 2026-06-08 go
python comment_bot.py backfill 2026-06-08 go ignoreseen
python comment_bot.py report
```

- `inspect`: 댓글창/등록 버튼 셀렉터를 확인하고 작성하지 않습니다.
- `probe`: 붙여넣기 동작을 점검합니다.
- `test`: 특정 글에 테스트 댓글을 1회 작성합니다.
- `backfill`: 특정 날짜 이후 미처리 글을 미리보기 또는 실제 작성합니다.
- `report`: 전날 댓글/카페 통계 보고를 텔레그램으로 즉시 1회 발송합니다.

### 4. watchdog 운영

`watchdog_comment_bot.ps1`은 작업 스케줄러에서 5분마다 호출하는 것을 전제로 작성되어 있습니다.

역할:

- `comment_bot.py` 실행 여부 확인
- 하트비트 파일(`comment_bot_heartbeat.txt`) 정체 감지
- 고아 `chromedriver`, 댓글 봇 전용 Chrome 프로필 잠금 정리
- `comment_bot.log` 잠금 해제
- 숨김 창으로 댓글 봇 재기동

주의: 현재 `watchdog_comment_bot.ps1`과 `run_comment_bot.bat`에는 `D:\coding\ccidacafe` 절대 경로가 들어 있습니다. 다른 위치에서 운영할 때는 `$dir` 또는 `cd /d` 경로를 실제 프로젝트 위치로 바꿔야 합니다.

## 생성되는 로컬 파일

다음 파일은 실행 중 생성되며 `.gitignore`에 포함되어 있습니다.

| 파일/폴더 | 설명 |
| --- | --- |
| `config.ini` | 계정, API 키, 운영 설정 |
| `cookies.txt` | 네이버 로그인 쿠키 |
| `chrome_profile_commentbot/` | 댓글 봇 전용 Chrome 프로필 |
| `chrome_profile_extractor/` | 회원 추출 전용 Chrome 프로필 |
| `comment_bot_seen.json` | 댓글 처리 완료 글 ID |
| `comment_bot_events.json` | 댓글 시도 이력 |
| `comment_bot_lastreport.json` | 마지막 일일 보고 날짜 |
| `comment_bot_weekly.json` | 마지막 주간 보고 주차 |
| `comment_bot_heartbeat.txt` | watchdog용 생존 신호 |
| `comment_bot.log` | 댓글 봇 로그 |
| `members.csv` | 회원 ID 추출 결과 |
| `uploads/`, `frame_*.jpg`, `temp_video*.mp4` | 업로드/프레임/동영상 임시 파일 |

## 운영 주의사항

- 네이버 스마트에디터 DOM/CSS가 바뀌면 이미지, 인용구, 댓글 입력 셀렉터가 깨질 수 있습니다. 이때는 `inspect`, `probe` 모드로 먼저 확인합니다.
- 댓글 봇과 회원 추출기는 서로 다른 Chrome 프로필을 써서 충돌을 줄입니다.
- `comment_bot.py` watch 모드는 락 파일로 단일 인스턴스를 보장합니다.
- 텔레그램 보고를 쓰려면 `[COMMENT_BOT]`에 `telegram_enabled`, `telegram_token`, `telegram_chat_id`, `telegram_report_hour`를 설정합니다.
- 자동 댓글/회원 데이터 추출은 카페 운영 정책, 네이버 이용약관, 개인정보 관련 법규를 지키는 범위에서만 사용해야 합니다.

## 저장소 구성

```text
.
├── youtube_cafe_auto.py       # 콘텐츠 생성 및 네이버 카페 게시 핵심 엔진
├── cafe_gui.py                # Tkinter GUI
├── server.py                  # FastAPI 웹 UI 서버
├── static/index.html          # 웹 UI 화면
├── comment_bot.py             # 가입인사 댓글 감시 봇
├── watchdog_comment_bot.ps1   # 작업 스케줄러용 watchdog
├── watchdog_comment_bot.vbs   # PowerShell watchdog 숨김 실행 보조
├── run_comment_bot.bat        # 단순 재시작 루프
├── extract_members.py         # 회원 ID 추출기
├── 네이버_카페_자동화_완전초보_가이드북.md
├── CLAUDE.md
└── test_*.py
```
