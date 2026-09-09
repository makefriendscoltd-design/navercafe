# 레퍼런스 YouTube 링크 수집 루틴

## 현재 자동화 경로 — 2026-09-10

실제 매일 수집 정본은 Orca `매일 11시 YouTube 롱폼 레퍼런스`
(`03ea536f-81b6-46e4-9879-d76a7fe452bb`)의 지시문이다.
8분 이상, 최근 90일(후보 5개 미만이면 180일까지 표시 후 확대), 같은 채널 최대 5개,
유사 채널/주제 최소 5개 목표, 총 최대 10개와 메타데이터·중복 검증을 적용한다.
원래 사용자/제작 원본 시드를 바탕으로 하며, 수집 후보와 직접 첨부 링크는 구분한다.
조건 변경은 이 자동화 정본에서 시작한다. 아래 수동 수집기와
`reference_discovery_config.json`은 현재 11시 자동화의 조건 정본이 아니다.

수집기는 전체 후보 보고를 마친 뒤 `~/.agent-reach/navercafe-longform-seen.json`에
누적 병합한다. 제작기는 매일 12:30 launchd `com.aimax.navercafe-reference-production`에서
`reference_daily_production.py`를 실행해 이 기록을 읽는다.
`duration_seconds`/`duration`, `seen_at`/`first_seen` 형식을 모두 읽으며,
실패한 작업은 기존 산출물 루트에서 채널별로 재개한다.
카페는 별도 매시 큐(10~23시, 일 2건·5시간 간격)에서 처리한다.
큐 등록은 발행 완료가 아니며, 실행별 보고는 `outputs/reference-daily-production/`에 보존한다.

아래는 파일에 직접 붙여 넣은 링크를 찾는 수동 보조 도구다.

유튜브 링크를 `/Users/apple/orca/workspaces/navercafe/레퍼런스` 아래의 `.md`, `.txt`, `.csv`, `.json`, `.yaml`, `.html` 파일에 붙여 넣으면 다음 명령으로 수집합니다.

```bash
./find_reference_links.command
```

결과는 `outputs/reference-link-routine/`에 저장됩니다.

- `YOUTUBE_LINK_QUEUE.md`: 사람이 읽는 큐. URL, source_key, 발견 위치, 중복 수, 다음 실행 명령을 표시합니다.
- `youtube_links.json`: 기계가 읽는 전체 결과입니다.
- `state.json`: 이전 발견 시각과 위치를 보존해 `new`/`seen`을 구분합니다.

기본 스캔은 실행 코드, `.git`, 가상환경, 기존 `outputs`, `_workspace`를 제외합니다. 유튜브 URL은 영상 ID 기준으로 정규화하므로 `watch`, `youtu.be`, `shorts`, `live`, `embed` 링크가 하나로 합쳐집니다.

이 루틴은 외부 사이트를 조회하거나 발행하지 않습니다. `new` 항목을 확인한 뒤
직접 첨부 작업은 이 Orca 작업방에서 AGENTS.md의 채널별 경로로 요청합니다.
`run_content_link.command`는 폐기된 단일 제작 경로를 차단하는 가드이므로 제작 명령으로 사용하지 않습니다.

다른 폴더를 쓸 때는 일회성으로 다음처럼 지정할 수 있습니다.

```bash
./find_reference_links.command --reference-dir "/path/to/reference"
```
