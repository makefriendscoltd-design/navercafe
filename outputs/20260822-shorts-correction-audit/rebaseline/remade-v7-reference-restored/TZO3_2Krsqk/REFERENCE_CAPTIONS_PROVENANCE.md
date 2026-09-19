# captions.srt 출처

원래 이 폴더의 `captions.srt`(sha256 `6386a91dc1d21ba94294c78363d0eda8f9c523852f093cacb7a1fcc3c395e7b5`)는
git에 추적되지 않은 채 로컬에만 있었고, 2026-09-17 12:16 KST 무렵 폴더가 추적 파일만 남긴 채
다시 만들어지면서 사라졌다. 이 맥의 `.srt` 763개와 저장소 전체를 해시로 대조했지만 같은 파일은 없었고,
Time Machine 백업 대상도 설정돼 있지 않아 원본은 복구할 수 없다.

2026-09-19에 `outputs/_KJxVugQzA0-20260917/shorts/captions.srt`를 복사해 채웠다.
2026-09-17에 V7 정본 경로로 렌더·검수를 통과한 실제 쇼츠의 자막이다.

이 파일은 발화 속도를 정하지 않는다. `shorts_narration_tempo.retime()`은 이 파일을 파싱하고 해시를
출처로 기록할 뿐이며, 목표 속도는 `content_production_policy.SHORTS_NARRATION_TARGET_CPS`에서 온다.
실제 음성 속도를 목표값의 ±1% 안으로 묶는 검사는 이 파일과 무관하게 그대로다.

같은 일이 반복되지 않도록 이 파일은 이제 git으로 추적한다.
