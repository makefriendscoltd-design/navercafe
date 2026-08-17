# 나민수 AI 쇼츠 제작 규격 (정본)

이 파일이 쇼츠 제작의 기준이다. 텔레그램 쇼츠 작업은 항상 이 규격대로 만든다.
v1~v4 에서 어긴 항목들이 여기 전부 들어 있다. 임의로 바꾸지 마라.

마지막 검증 통과본: `outputs/KUeW3zzF49A/KUeW3zzF49A_shorts_v5.mp4`

---

## 하드 규칙 (하나라도 못 지키면 blocked 로 보고)

1. **나레이션은 ElevenLabs 민수 보이스** `voice_id 34bevfaPHev7LXnjGAlA`.
   Edge TTS 등으로 대체 금지. 키가 없으면 blocked.
2. **자막은 어절(단어) 단위.** "뭐가 위험할까요" → "뭐가" / "위험할까요".
   문장·구문으로 묶지 마라.
3. **자막에 문장부호를 넣지 않는다.** `. , 。 、 : ; ! ?` 전부 제거.
   ("바꿉니다." 가 아니라 "바꿉니다")
4. **자막 블록끼리 시간이 겹치면 안 된다.** 맞물림 0개.
5. **대본 마지막에 CTA.**
   `이 영상을 정리했습니다. / 자료가 궁금하신 분들은 채널 구독 후 프로필 링크를 확인해주세요.`
6. **배경음과 효과음이 반드시 들어간다.** 아래 지정 파일만 쓴다.
7. **배경음이 목소리를 묻지 않는다.** 나레이션 대비 분리도 12dB 이상.

---

## 고정 자산 (다른 파일로 대체 금지)

| 용도 | 파일 |
|---|---|
| 배경음 | `assets/bgm/DSGNBass-Millitary_Action_Tri-Elevenlabs.mp3` |
| 효과음(전환) | `assets/sfx/WHSH-Whoosh_Short_Clean-Elevenlabs.mp3` |
| 진행자 클립 | `outputs/2026-08-05_18-42-44/2026-08-05_18-42-44_edited.mp4` |
| 자막 폰트 | `assets/fonts/Cafe24Ohsquare.ttf` |
| 제목 폰트 | `assets/fonts/BMHANNA_11yrs_ttf.ttf` |

- **riser / pop 등 다른 효과음은 쓰지 않는다.** 레퍼런스 편집본(캡컷)에 whoosh 만 쓴다.
- `assets/sfx/whoosh.wav`, `riser.wav`, `pop.wav` 는 레퍼런스가 아닌 임시 생성물이다. 쓰지 마라.
  (레퍼런스 whoosh 는 최대 -1.6dB, 임시본은 -18.5dB 로 16.9dB 차이가 나서 안 들린다.)

## 고정 믹스 값

```
voice_volume       0.82
music_volume       0.10   * music_volume_scale 1.85  = 0.185
효과음 volume       0.50   * sfx_volume_scale   2.2   = 1.10
master_lufs -14.0 / master_lra 3.0 / master_true_peak -1.8
```

효과음 게인 1.10 은 레퍼런스 캡컷 편집본의 whoosh 볼륨(0.88~1.41) 한가운데다.

---

## 제작 순서

### 0. 준비물 확인 (없으면 여기서 만든다)

새 job 은 보통 이 둘이 없다. 없다고 blocked 로 보고하지 말고 만들어라.

**원본 영상** `assets/youtube/<VIDEO_ID>.mp4` — 화면 레이어로 쓴다.
```bash
yt-dlp -f "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b" \
  --merge-output-format mp4 -o "assets/youtube/<VIDEO_ID>.mp4" "<원본 URL>"
```

**쇼츠 대본** `outputs/<VIDEO_ID>/aimax_script_ko_short.txt` — 스크립트 단계 산출물
(`notebooklm_script/outputs/<VIDEO_ID>/key_points.md`, `clean_script.md`)에서 압축해 쓴다.
- **공백 제외 520~560자**를 목표로 한다. 이 분량이 70~78초로 나온다(어절 자막 기준).
- 문단(빈 줄) 하나가 나레이션 구문 하나가 되고, 그 경계마다 whoosh 가 들어간다.
  8~10문단이 적당하다.
- 첫 줄은 결론부터. 짧은 단문, 존댓말(`~합니다`).
- 마지막 문단은 반드시 아래 CTA 두 문장.

### 1. 대본에 CTA 확인
`outputs/<VIDEO_ID>/aimax_script_ko_short.txt` 끝에 CTA 두 문장이 있어야 한다. 없으면 붙인다.

### 2. 나레이션 타이밍 마스터
키는 환경변수 `ELEVENLABS_API_KEY`, 없으면 `D:\coding\ccidainsta\config.yaml` 의
`api_keys.elevenlabs` 를 읽어 넘긴다. **키 값을 로그나 화면에 출력하지 마라.**

```bash
python build_minsoo_timing_master.py \
  --script   outputs/<VIDEO_ID>/aimax_script_ko_short.txt \
  --out      outputs/<VIDEO_ID>/minsoo_timing_master.mp3 \
  --srt      outputs/<VIDEO_ID>/minsoo_timing_master.srt \
  --manifest outputs/<VIDEO_ID>/minsoo_timing_master.manifest.json \
  --voice-id 34bevfaPHev7LXnjGAlA --overlap 0.1 --target-lufs -14.0 --true-peak -1.8
```

### 3. 어절 자막 (부호 제거 + 겹침 제거 포함)
```bash
python make_sentence_srt.py \
  --manifest outputs/<VIDEO_ID>/minsoo_timing_master.manifest.json \
  --out      outputs/<VIDEO_ID>/minsoo_word.srt --mode word
```
출력의 **맞물림 0개** 를 확인한다. 0 이 아니면 진행하지 마라.

### 4. 렌더 설정
`aimax_video_config_shorts.template.json` 을 복사해
`outputs/<VIDEO_ID>/aimax_video_config_shorts.json` 으로 쓰고, `audio.effects` 만 채운다.

효과음은 **manifest 의 구문 시작 시각(첫 구문 제외) 마다 하나씩**:
```python
effects = [{"path": "assets/sfx/WHSH-Whoosh_Short_Clean-Elevenlabs.mp3",
            "start": round(p["start"], 3), "volume": 0.5}
           for p in manifest["phrases"][1:]]
```
나레이션 길이가 바뀌면 좌표도 반드시 다시 계산한다. 이전 job 의 좌표를 재사용하지 마라.

### 5. 렌더
```bash
python aimax_video_pipeline.py \
  --config    outputs/<VIDEO_ID>/aimax_video_config_shorts.json \
  --talking   "outputs/2026-08-05_18-42-44/2026-08-05_18-42-44_edited.mp4" \
  --screen    "assets/youtube/<VIDEO_ID>.mp4" \
  --voiceover outputs/<VIDEO_ID>/minsoo_timing_master.mp3 \
  --srt       outputs/<VIDEO_ID>/minsoo_word.srt \
  --skip-whisper \
  --out-dir   outputs/<VIDEO_ID>/render \
  --title-text '<2줄 헤드라인>' --source-text '출처: <원본 채널>'
```
- 결과 2160x3840 / 30fps.
- 템플릿의 `silence_db -99`, `max_segment_duration 0` 은 진행자 클립을 다시 자르지
  않기 위한 값이다. 건드리면 영상이 나레이션보다 짧아진다.
- 템플릿의 `export.threads` 는 x264 메모리 상한용이다. 2160x3840 인코딩에서
  기본 스레드 수(코어 수만큼)로 돌리면 램이 모자랄 때 몇 분 돌다가
  `x264 [error]: malloc of size ... failed` 로 죽고 1초짜리 빈 mp4 가 남는다.
  이 실패는 설정이 아니라 자원 문제이므로, 여유 램을 확인하고
  (`Get-CimInstance Win32_OperatingSystem`) 값을 더 낮춰 다시 돌리면 된다.
  **완성본은 반드시 길이와 해상도를 확인해라.** exit code 만 보면 이 실패를 놓친다.
- 완성본을 `outputs/<VIDEO_ID>/<VIDEO_ID>_shorts_vN.mp4` 로 복사한다. 이전 버전은 지우지 않는다.

### 6. 텔레그램 전송본
원본은 4K 라 텔레그램 48MB 한도를 넘는다. 축소본을 따로 만든다.
```bash
ffmpeg -y -i <원본> -vf scale=1080:1920 -c:v libx264 -preset veryfast \
  -b:v 1000k -maxrate 1200k -bufsize 2000k -c:a aac -b:a 128k \
  -movflags +faststart <원본 스템>_tg.mp4
```
마커에는 **원본 4K 경로**를 적는다. 업로드는 원본으로 한다.

---

## 검증 (보고에 수치를 넣는다)

| 항목 | 기준 |
|---|---|
| manifest `voice_id` | `34bevfaPHev7LXnjGAlA` |
| 자막 | 어절 단위 / 부호 0개 / 맞물림 0개 |
| CTA | 대본과 마지막 구문에 존재 |
| 배경음 | 나레이션 공백 구간에서 무음 대비 +6dB 이상 |
| 목소리 vs 배경음 | 분리도 12dB 이상 |
| 효과음 | 구문 전환 지점마다 존재 |
| 라우드니스 | -14 LUFS 부근 / true peak -1.8dBFS 이하 |

배경음이 목소리를 묻으면 `music_volume` 을 낮춰 다시 뽑는다.
규격을 못 맞추면 임시본을 완료로 보고하지 말고 blocked 로 사유를 밝힌다.

---

## 제목 정책

- 주제 명확성 우선. 후킹·은유·궁금증 유발형 금지.
- 핵심 키워드를 앞에. 자연스러운 한국어 20~35자.
- 3안을 내고 그중 가장 주제 전달이 분명한 것을 추천으로 표시한다.

> 마커의 `title=` 은 컨트롤러 파서(`(\w+)=(\S+)`)가 공백에서 끊는다.
> 제목이 잘려 보이면 텔레그램에서 `/title <job_id> <제목>` 으로 고친다.
