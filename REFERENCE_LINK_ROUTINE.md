# 레퍼런스 YouTube 링크 수집 루틴

유튜브 링크를 `/Users/apple/orca/workspaces/navercafe/레퍼런스` 아래의 `.md`, `.txt`, `.csv`, `.json`, `.yaml`, `.html` 파일에 붙여 넣으면 다음 명령으로 수집합니다.

```bash
./find_reference_links.command
```

결과는 `outputs/reference-link-routine/`에 저장됩니다.

- `YOUTUBE_LINK_QUEUE.md`: 사람이 읽는 큐. URL, source_key, 발견 위치, 중복 수, 다음 실행 명령을 표시합니다.
- `youtube_links.json`: 기계가 읽는 전체 결과입니다.
- `state.json`: 이전 발견 시각과 위치를 보존해 `new`/`seen`을 구분합니다.

기본 스캔은 실행 코드, `.git`, 가상환경, 기존 `outputs`, `_workspace`를 제외합니다. 유튜브 URL은 영상 ID 기준으로 정규화하므로 `watch`, `youtu.be`, `shorts`, `live`, `embed` 링크가 하나로 합쳐집니다.

이 루틴은 외부 사이트를 조회하거나 발행하지 않습니다. `new` 항목을 확인한 뒤 필요한 링크만 기존 제작기 명령으로 실행합니다.

```bash
./run_content_link.command "https://youtu.be/VIDEO_ID"
```

다른 폴더를 쓸 때는 일회성으로 다음처럼 지정할 수 있습니다.

```bash
./find_reference_links.command --reference-dir "/path/to/reference"
```
