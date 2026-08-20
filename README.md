# Naver Cafe YouTube Publisher

YouTube 링크를 NotebookLM 원고로 변환하고, 영상 장면 이미지를 캡처해 Naver Cafe 글쓰기 화면에 자동으로 넣는 Windows용 자동화 도구입니다.

## What This Does

- YouTube URL을 입력으로 받습니다.
- NotebookLM에서 카페글 원고를 생성합니다.
- 영상에서 섹션 수에 맞춰 이미지를 캡처합니다.
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

Python 3.12 이상을 권장합니다.

```powershell
python -m pip install -r requirements.txt
```

이 환경에서 `python`이 PATH에 없다면 전체 경로로 실행합니다.

```powershell
& 'C:\Users\hey_m\AppData\Local\Programs\Python\Python312\python.exe' -m pip install -r requirements.txt
```

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
```

`config.ini`, `cookies.txt`, `chrome_profile_publisher/`, `published_posts.json`, `naver_session.json`은 로컬 상태 또는 인증 정보라서 커밋하지 않습니다.

## Usage

카페에 올리지 않고 원고와 이미지 preview만 확인:

```powershell
python notebook_cafe_auto.py "https://youtu.be/VIDEO_ID" --dry
```

카페 글쓰기 화면에 넣고 임시저장:

```powershell
python notebook_cafe_auto.py "https://youtu.be/VIDEO_ID"
```

바로 발행:

```powershell
python notebook_cafe_auto.py "https://youtu.be/VIDEO_ID" --publish
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
- 같은 YouTube source key가 `published_posts.json`에 있으면 중복 발행하지 않습니다.
- Naver 로그인이 필요하면 visible Chrome에서 사람이 CAPTCHA, 2차 인증, 새 기기 확인을 처리할 수 있게 기다립니다.
- 로그인 쿠키는 `naver_session.json`에 저장해 다음 실행에서 재로그인을 줄입니다.
- 발행 성공 후에만 `published_posts.json`에 source URL과 cafe article URL을 기록합니다.

## Handoff Notes

특정 Telegram 작업의 운영 인계는 `HANDOFF_TELEGRAM_CAFE_KUeW3zzF49A.md`를 참고하세요.

새 개발자가 이어받을 때는 먼저 아래 순서로 확인하면 됩니다.

1. `git status -sb`로 로컬 실행 로그가 섞여 있는지 확인합니다.
2. `config.ini`가 있는지 확인합니다.
3. `python notebook_cafe_auto.py "<youtube_url>" --dry`로 원고와 이미지 캡처가 되는지 확인합니다.
4. Naver 로그인이 뜨면 visible Chrome에서 직접 완료합니다.
5. 검수 없는 자동 발행은 `--publish`가 붙었을 때만 수행합니다.
