# Naver Cafe YouTube Publisher

YouTube 링크를 NotebookLM 원고로 변환하고, 영상 장면 이미지를 캡처해 Naver Cafe 글쓰기 화면에 자동으로 넣는 Windows용 자동화 도구입니다.

## What This Does

- YouTube URL을 입력으로 받습니다.
- NotebookLM에서 카페글 원고를 생성합니다.
- 이미 내려받은 라이브 원본을 우선 사용하고, 각 시간 구간에서 선명도와 장면 차이를 비교해 대표 장면을 캡처합니다.
- Naver Cafe Smart Editor에 제목, 본문, 이미지, CTA, 원본 링크를 입력합니다.
- 기본 모드는 `draft`입니다. 즉, 카페에 임시저장하고 사람이 검수한 뒤 직접 발행합니다.
- `--publish`를 붙이면 바로 발행합니다.

## Main Files

- `notebook_cafe_auto.py`: YouTube 링크 -> NotebookLM 원고 -> 이미지 배치 -> 카페 저장/발행 진입점
- `youtube_cafe_auto.py`: Naver Cafe Selenium 자동화, 이미지 업로드, 중복 발행 guard, 로그인 세션 처리
- `notebooklm_source.py`: NotebookLM 소스 추가와 원고 가져오기
- `run_notebook_cafe.bat`: Windows 더블클릭 실행용 배치 파일
- `config.ini`: 로컬 설정 파일입니다. 계정/API 키가 들어가므로 Git에 올리지 않습니다.

## Setup

운영 자동화는 **Python 3.12**를 사용합니다. 기본 `python`이 3.13/3.14라면 전체 경로로 3.12를 지정하세요.

```powershell
python -m pip install -r requirements.txt
```

이 환경에서 `python`이 PATH에 없다면 전체 경로로 실행합니다.

```powershell
& 'C:\Users\likim\AppData\Local\Programs\Python\Python312\python.exe' -m pip install -r requirements.txt
```

Windows 설치·단위 테스트·의존성 import 확인을 한 번에 하려면 저장소 루트에서 실행합니다.

```powershell
.\install-publisher-windows.ps1
```

`notebooklm-py`는 Google의 공식 소비자 API가 아닌 비공식 클라이언트라 UI/RPC 변경으로 깨질 수 있습니다. 이 저장소는 검증한 `0.7.3`과 Python 3.12를 고정합니다. 최초 1회 같은 Windows 계정에서 Edge의 기존 Google·YouTube 로그인 쿠키를 가져와 인증 파일을 만듭니다.

```powershell
& 'C:\Users\likim\AppData\Local\Programs\Python\Python312\python.exe' -m notebooklm login --browser-cookies edge --include-domains youtube
& 'C:\Users\likim\AppData\Local\Programs\Python\Python312\python.exe' -m notebooklm auth check --test --json
```

첫 명령은 쿠키 값이나 계정 이메일을 로그에 남기지 않는 운영 셸에서 실행합니다. 두 번째 명령은 `status=ok`와 `checks.token_fetch=true`가 모두 확인되어야 합니다. Edge 쿠키를 읽을 수 없을 때만 `-m notebooklm login --browser msedge`로 visible 로그인을 진행합니다.

## Configuration

처음 실행하면 `config.ini`가 없을 때 설정 창이 뜹니다. 이미 운영 환경에 `config.ini`가 있다면 아래 항목들이 필요합니다.

```ini
[NAVER]
id = your_naver_id
pw = your_naver_password
cafe_url = https://cafe.naver.com/f-e/cafes/<club_id>/menus/<menu_id>?viewType=L
board_name = target board name

[GEMINI]
api_key = your_gemini_api_key

[NOTEBOOKLM]
enabled = true
notebook_id = optional_existing_notebook_id

[PUBLISH_NOTIFY]
enabled = false
telegram_token = local_secret_only
telegram_chat_id = target_group_id
telegram_thread_id = optional_topic_id
```

`config.ini`, `cookies.txt`, `chrome_profile_publisher/`, `published_posts.json`, `naver_session.json`은 로컬 상태 또는 인증 정보라서 커밋하지 않습니다.
처음에는 `config.example.ini`를 `config.ini`로 복사한 뒤 로컬에서만 실제 값을 채웁니다.

## Usage

카페에 올리지 않고 원고와 이미지 preview만 확인:

```powershell
python notebook_cafe_auto.py "https://youtu.be/VIDEO_ID" --dry
```

기준글 5854와 같은 `텍스트 6구간 + 장면 5장 + 마지막 YouTube OG 카드` 포맷으로, 기존 다운로드 영상을 재사용하고 결과 JSON을 남기기:

```powershell
python notebook_cafe_auto.py "https://youtu.be/ORIGINAL_MEMBERS_ONLY_ID" `
  --notebook-url "https://youtu.be/UNLISTED_REPLAY_ID" `
  --video-file "C:\path\downloaded-live.mp4" `
  --title "2026-08-18 강의 제목" `
  --template reference-5854 --images 5 --no-keywords --unattended `
  --expected-club-id 26321967 --expected-menu-id 315 `
  --result "C:\path\result.json" --dry
```

카페 글쓰기 화면에 넣고 임시저장:

```powershell
python notebook_cafe_auto.py "https://youtu.be/VIDEO_ID"
```

바로 발행:

```powershell
python notebook_cafe_auto.py "https://youtu.be/VIDEO_ID" --publish
```

발행 글을 다시 읽어 제목·텍스트 6구간·이미지 5장·인용구 0개·원본 YouTube OG 카드 1개를 확인한 뒤 Telegram까지 전송:

```powershell
python notebook_cafe_auto.py "https://youtu.be/VIDEO_ID" `
  --title "강의 제목" --template reference-5854 --images 5 `
  --no-keywords --unattended --publish --notify --result result.json
```

이미지 수를 직접 지정:

```powershell
python notebook_cafe_auto.py "https://youtu.be/VIDEO_ID" --images 5
```

NotebookLM을 건너뛰고 원고 파일을 직접 사용:

```powershell
python notebook_cafe_auto.py "https://youtu.be/VIDEO_ID" --file manuscript.txt
```

## Safety Guards

자동화에는 운영 사고를 줄이기 위한 guard가 들어 있습니다.

- 이미지가 0장이면 카페 임시저장/발행을 하지 않습니다.
- `reference-5854`는 텍스트 6구간·이미지 5장과 구간별 4·8·11·8·6·4 문단 배열, 기준글의 고정 전개 문구가 모두 맞지 않으면 저장/발행하지 않습니다.
- 예약 실행은 승인된 카페 `26321967`·게시판 `315`와 `config.ini`의 URL이 다르면 쓰기 전에 중단합니다.
- 같은 YouTube source key가 `published_posts.json`에 있으면 새 글을 쓰지 않고 기존 글 URL만 재검증합니다.
- Naver 로그인이 필요하면 visible Chrome에서 사람이 CAPTCHA, 2차 인증, 새 기기 확인을 처리할 수 있게 기다립니다.
- 로그인 쿠키는 `naver_session.json`에 저장해 다음 실행에서 재로그인을 줄입니다.
- 등록 직후 카페 글 URL을 먼저 기록합니다. 이후 검증이 실패해도 재시도는 새 글을 만들지 않습니다.
- Telegram은 발행 글의 제목·이미지 수·원본 링크 검증이 모두 통과한 뒤에만 전송하고, 전송 성공 기록이 있으면 중복 발송하지 않습니다.
- Telegram 요청 뒤 응답이 끊겨 성공 여부가 불명확하면 자동 재전송하지 않고 수동 확인 대상으로 남깁니다.
- 헤드리스 Chrome 발행은 본문 누락이 재현되어 차단합니다. Windows 예약 작업은 `Interactive` 사용자 세션에서 실행합니다.

## 운영 전환 순서

첫 운영 전환은 아래 순서를 지킵니다.

1. `git status -sb`로 로컬 실행 로그가 섞여 있는지 확인합니다.
2. `config.ini`가 있는지 확인합니다.
3. `--template reference-5854 --dry --result ...`로 원고·5개 캡처·결과 JSON을 확인합니다.
4. `--draft`로 임시저장하고 실제 SmartEditor 구조를 확인합니다.
5. Naver 로그인이 뜨면 visible Chrome에서 최초 1회 직접 완료합니다.
6. 승인 후에만 `--publish`로 실제 글을 등록합니다.
7. 게시글 읽기 검증과 Telegram 전송까지 한 번 통과한 뒤 예약 설정을 `mode=publish`, `notify=true`로 바꿉니다.
