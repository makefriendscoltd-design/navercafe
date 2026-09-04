"""Fail-closed production policy for Minsoo's three-channel content workflow.

The values in this module are the machine-readable counterpart of
``SHORTS_SPEC.md``.  Provider mutations must never proceed from a bundle that
does not satisfy these gates.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin
from zoneinfo import ZoneInfo


ASIDE_ACCOUNT = "u0"
CAFE_NOTEBOOK = {
    "title": "민수대표님_카페글",
    "id": "c09a56d4-b87c-4f54-bfdb-93219326fbae",
}
SHORTS_NOTEBOOK = {
    "title": "민수대표님_숏폼",
    "id": "ed70fc3b-474b-423a-9ca8-d19934703f27",
}
FORBIDDEN_NOTEBOOK_PREFIXES = ("그지마케팅_",)

SHORTS_NOTEBOOK_PROMPT = "이 영상으로 숏폼 스크립트 만들어줘."
SHORTS_NOTEBOOK_INSTRUCTION_VERSION = "v14.0"
SHORTS_NOTEBOOK_INSTRUCTION = """# 유튜브 쇼츠 스크립트 작성 메타프롬프트 v14.0

## 작업 원칙

선택된 원본 영상 하나만 근거로 쓴다. 영상에 없는 사실, 숫자, 인과관계, 성과, 수익, 연봉, 지위, 경력은 만들거나 더 강하게 바꾸지 않는다. 영상 속 화자의 경험과 주장은 그대로 요약하되, 비교·가능성·의견을 확정 사실이나 개인의 보장된 결과로 바꾸지 않는다.

`무조건`, `보장`, `필승`, `대체 불가능`, `최고 연봉`, `돈을 복사`, `몸값 폭등`처럼 원본보다 강한 절대 표현을 쓰지 않는다. 강한 훅이 필요해도 새로운 결과나 수치를 발명하지 말고, 원본에서 직접 확인되는 사람·도구·행동·문제만 짧고 구어체로 표현한다. 근거가 부족하면 과장해서 채우지 말고 해당 문구를 빼거나 `확인 불가`로 분석에만 표시한다.

아래 다섯 유형은 원본의 제목이나 화자가 언급해도 헤드카피와 스크립트에 단정형으로 쓰지 않는다.

1. `모든 소스`, `어떤 자료든`처럼 지원 범위를 전체로 일반화하는 표현
2. `2클릭만으로 완성`, `두 번 클릭이면 완성`처럼 결과를 보장하는 표현
3. `무료`, `공짜`, `무제한`처럼 비용·사용량을 확정하는 표현
4. `5분에서 10분이면 완성`처럼 생성 시간을 고정하는 표현
5. NotebookLM이 인스타·틱톡·유튜브 쇼츠 등 여러 플랫폼에 자동·동시·무인 배포한다는 표현. 별도 도구의 역할을 NotebookLM 기능으로 합치지 않는다.

Repurpose 또는 플랫폼 배포를 스크립트에서 언급해야 한다면 아래 두 문장을 글자 그대로 모두 쓴다.

`Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.`
`별도 연결 설정과 각 플랫폼 공급자 지원이 확인된 채널에만 배포할 수 있습니다.`

이 두 문장 외에는 Repurpose 또는 플랫폼 배포에 관한 문장을 어떤 형태로도 덧붙이지 않는다. 긍정·부정·제작자 귀속·조건·이중부정 표현도 모두 금지한다. `한 번 연결`, `영상 하나를 올리는 즉시`, `모든 채널`, `자동 배포`, `동시 배포`, `무인 유포`처럼 한 번의 업로드가 곧 전체 플랫폼 발행으로 이어진다고 쓰지 않는다.

## STEP 1: 영상 분석

아래 항목을 먼저 채우고 `분석 완료`라고 선언한다.

1. 화자 성별: 남성/여성/확인 불가와 근거
2. 화자 이름: 정확한 이름/언급 없음과 언급 위치
3. 핵심 주제: 한 문장
4. 전략·팁 개수: 원본에서 직접 확인되는 개수
5. 각 전략·팁: 제목과 핵심 내용
6. 구체적 수치: 원본에 실제로 나온 수치와 맥락만 기록
7. 화자 배경·권위: 원본에서 직접 말한 경력·실적만 기록
8. 영상 길이

## STEP 2: 정확성 검증

- 화자 성별과 이름을 추측하지 않았는가?
- 전략·팁·수치·성과가 원본에 직접 있는가?
- 비교나 가능성을 확정·보장 표현으로 바꾸지 않았는가?
- 화자의 사례를 모든 사람에게 적용되는 결과로 일반화하지 않았는가?

하나라도 불확실하면 원본보다 강하게 쓰지 말고 분석에 `확인 불가`로 표시한다.

## STEP 3: 포맷 선택

- 스토리 중심이면 포맷 A
- 전략 6개 이상이면 포맷 B
- 실용 팁 10개 이상이면 포맷 C
- 원본의 정확한 수치가 핵심이면 포맷 D

수치 자체가 자극적이라는 이유로 포맷 D를 고르지 않는다.

## STEP 4: 헤드카피와 스크립트 작성

### 형식 규칙

1. 타임스탬프, `[훅]`·`[본문]`·`[결론]` 같은 레이블, 메타 주석, 이름의 영문 병기를 쓰지 않는다.
2. 스크립트는 문장이 끝날 때마다 줄을 바꾼다.
3. 본문은 자연스러운 한국어 구어체로 쓰고 볼드 강조를 쓰지 않는다.
4. 헤드카피 후보는 정확히 3개다. 각 후보는 `첫째 줄 / 둘째 줄` 형식의 정확히 2줄이고, 각 줄은 공백 포함 18자 이하다.
5. 헤드카피 3안의 모든 줄은 BM HANNA 11yrs old 폰트 90px 실측 폭 920px 이하여야 한다. 실측을 보장할 수 없으면 공백 포함 13자 이하로 줄여 안전폭을 확보한다.
6. 헤드카피 첫 줄은 질문·놀람·손해감·강한 단정의 구어체이며, 둘째 줄과 스크립트 첫 3문장이 같은 구체적 주제를 이어받아야 한다.
7. 헤드카피에도 원본에 없는 수익·성과·연봉·신분·인과·숫자를 넣지 않는다.

### 내용 규칙

1. 확인한 성별과 이름만 사용한다. 언급이 없으면 생략한다.
2. STEP 1에서 확인한 전략·팁과 수치만 사용하고 임의로 추가하거나 변형하지 않는다. 첫째부터 다섯째의 제목과 핵심 행동은 원본에서 확인한 다섯 지점을 실제 순서대로 각각 이어받는다. 출처의 구체적 행동을 `자료 준비`, `기능 활용`, `자동화하기` 같은 일반적인 이름으로 바꾸거나 서로 다른 항목으로 대체하지 않는다. 어느 항목인지 원본과 일대일로 대응할 수 없으면 스크립트를 출력하지 않는다.
3. 첫 문장은 `이 남자 미쳤습니다.` 또는 `이 프로그램 대박입니다.`처럼 짧고 강하게 시작할 수 있지만, 뒤 문장에서 원본에 없는 결과를 붙이지 않는다.
4. 스크립트는 도입과 원본 순서의 첫째부터 다섯째까지만 작성한다. `첫째,`부터 `다섯째,`까지를 각각 새 줄 첫 머리에 표시하고, 여섯째 이후는 출력하지 않는다.
5. 원본이나 임의의 CTA를 스크립트에 출력하지 않는다. 스크립트 본문은 후속 단계에서 글자 그대로 보존되고 고정 CTA만 붙는다.

## STEP 5: 최종 검증

- 분석, 헤드카피 3개, 스크립트가 모두 선택된 원본 하나에만 근거하는가?
- 원본에 없는 숫자·성과·수익·연봉·지위·인과·보장을 추가하지 않았는가?
- 헤드카피가 정확히 3개이며 각 후보가 2줄·줄당 18자 이하·90px 실측 920px 이하인가?
- 타임스탬프·섹션 레이블·메타 주석·영문 병기가 없는가?
- 도입과 첫째~다섯째만 있고, 각 항목이 새 줄에서 시작하며, 원본 CTA가 없는가?
- 지원 범위 일반화·보장된 2클릭·무료·고정 생성 시간·자동 다중 플랫폼 배포 표현이 없는가?
- Repurpose나 플랫폼 배포를 언급했다면 지정된 두 문장만 글자 그대로 썼고, 긍정·부정·제작자 귀속·조건·이중부정을 포함한 다른 관련 문장을 하나도 덧붙이지 않았는가?

하나라도 아니면 과장해서 고치지 말고, 원본 범위 안에서 다시 작성한다.

## 최종 출력 형식

### 영상 분석 결과
[STEP 1 내용 전체]

### 선택한 포맷
포맷 [A/B/C/D]

### 헤드카피라이팅
1. [첫째 줄] / [둘째 줄]
2. [첫째 줄] / [둘째 줄]
3. [첫째 줄] / [둘째 줄]

### 스크립트
[레이블 없이 스크립트 본문]

버전: v14.0 (Repurpose 외부 경계·90px 안전폭·금지 주장·다섯째/CTA 계약 고정)
"""
# Literal pin filled from normalize_notebook_instruction(SHORTS_NOTEBOOK_INSTRUCTION).
SHORTS_NOTEBOOK_INSTRUCTION_SHA256 = "503d5c7eb8564e5dd517154b876ecb8ad654f8092f3bf869f3f6b493f7a02f12"
SHORTS_NOTEBOOK_REQUIRED_MARKERS = (
    "BM HANNA 11yrs old 폰트 90px 실측 폭 920px 이하",
    "모든 소스",
    "2클릭만으로 완성",
    "`무료`, `공짜`, `무제한`",
    "생성 시간을 고정",
    "자동·동시·무인 배포",
    "Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.",
    "별도 연결 설정과 각 플랫폼 공급자 지원이 확인된 채널에만 배포할 수 있습니다.",
    "긍정·부정·제작자 귀속·조건·이중부정 표현도 모두 금지",
    "영상 하나를 올리는 즉시",
    "첫째부터 다섯째의 제목과 핵심 행동",
    "원본과 일대일로 대응할 수 없으면",
    "첫째부터 다섯째까지만",
    "고정 CTA만 붙는다",
)

SHORTS_FORBIDDEN_CLAIM_PATTERNS = {
    "all_or_any_source": (
        r"(?:모든|어떤)\s*(?:종류의\s*)?(?:웹\s*)?(?:원본\s*)?(?:자료|소스|파일|문서|형식)(?:든|를|가|도|이든)?",
    ),
    "guaranteed_two_clicks": (
        r"(?:단\s*)?(?:2|두)\s*(?:번|회)?(?:의\s*(?:마우스\s*)?)?\s*(?:클릭|누르\w*)\s*(?:만으로|만에|이면)",
        r"(?:클릭|누르\w*)\s*(?:2|두)\s*(?:번|회)\s*(?:만으로|만에|이면)",
        r"(?:2|두)\s*(?:번|회)[^,;.!?\n]{0,12}(?:클릭|누르\w*)[^,;.!?\n]{0,24}(?:완성|완료|제작|생성|만들|끝)",
    ),
    "free_or_unlimited": (
        r"(?:완전\s*)?무료(?:로|한|다|인|이며|이고)?",
        r"공짜",
        r"무제한",
    ),
    "fixed_generation_time": (
        r"(?:5|오)\s*분\s*(?:에서|~|-|부터)\s*(?:10|십)\s*분[^.!?\n]{0,40}(?:완성|완료|제작|생성|만들|기다리|끝)",
        r"(?:\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|십)\s*분\s*(?:안에|이내|만에|이면)[^.!?\n]{0,40}(?:완성|완료|제작|생성|만들|기다리|끝)",
    ),
}

SHORTS_ATTEMPT_BLOCKING_STATUSES = {
    "started",
    "provider_response_received",
    "substantive_failed",
    "unknown_after_provider_start",
    "passed",
}

MINSOO_VOICE_ID = "34bevfaPHev7LXnjGAlA"
MINSOO_MODEL_ID = "eleven_multilingual_v2"
MINSOO_VOICE_SETTINGS = {
    "stability": 0.65,
    "similarity_boost": 0.9,
    "style": 0.0,
    "use_speaker_boost": True,
}

TAILBITE = {"threshold_db": -35.0, "minimum": 0.08, "retained_gap": 0.06}
NARRATION = {
    "generation_mode": "seven_sections_same_voice_settings_for_speed_uniformity",
    "section_count": 7,
    "section_gap_seconds": 0.24,
    "last_to_first_pace_ratio_max": 1.10,
}
VIDEO = {"width": 1080, "height": 1920, "fps": 30}
HEADLINE = {"font_size": 90, "x": 540, "y": 440, "line_spacing": 20}
HEADLINE_SAFE_WIDTH_PX = 920
HEADLINE_SAFE_PROXY_CHAR_LIMIT = 13
SHORTS_TITLE_FONT_PATH = (
    Path(__file__).resolve().parent
    / "outputs/uX6zwf4b8sM-20260829/shorts/renderer/assets/fonts/BMHANNA_11yrs_ttf.ttf"
)
SUBTITLE = {
    "font_size": 59,
    "x": 540,
    "y": 940,
    "max_lines": 1,
    "merge_enabled": False,
    "rule": "one full token; no Latin/mixed-token slicing",
}
SUBTITLE_EDGE_PUNCTUATION = r".,，、:;!?！？。…~ㆍ·•\"'“”‘’()[]{}"
PRESENTER = {"x": 325, "y": 1298, "width": 430, "height": 430, "shape": "circle"}
SOURCE_SCREEN = {"x": 0, "y": 664, "width": 1080, "height": 608, "speed": 2.0}
WATERMARK = {"text": "@aimax", "x": 540, "y": 1768, "font_size": 44}

MINSOO_PRESENTER_ASSETS = {
    "2026-07-02 15-39-18.mp4": "a84c1f78ca5ac4fc2f45d67d7cb0d2e02f7a8dc25da5c71adae911a47f7b5056",
    "2026-06-29 16-32-49.mp4": "6f1382d51b0f9fc1c58634a3be3116d96dd6fa799da2958b7d05f87a6954b5c4",
    "2026-07-16 18-23-45.mp4": "ecb4d3638f9af4e63532943ecb777b8f8d144c30b44785effb032e6ccda90e08",
    "2026-07-16 18-21-06.mp4": "0cc94fb9cda999603980a0e817b0b6b95553a9c44d8f0e467308b1029b7de73f",
}

AUDIO_GATES = {
    "voice_integrated_lufs": -16.0,
    "voice_integrated_tolerance": 0.5,
    "voice_true_peak_max_dbtp": -2.0,
    "voice_minus_bgm_min_lu": 14.0,
    "voice_peak_minus_sfx_peak_min_db": 8.0,
    "final_integrated_lufs": -14.0,
    "final_integrated_tolerance": 0.5,
    "final_true_peak_max_dbtp": -1.8,
}

CARDNEWS = {"width": 1080, "height": 1080, "aspect_ratio": "1:1", "count": 10}
CARDNEWS_EDITORIAL = {
    "content_count": 8,
    "max_warning_first_cards": 2,
    "closing_cta1": "댓글 AIMAX",
    "closing_cta2": "관련 정보 받기",
    "warning_terms": (
        "제작자 주장",
        "독립 검증",
        "단정하면",
        "보장하지",
        "확인해야",
        "주의하세요",
        "믿지 마세요",
        "과장 금지",
    ),
}
SCHEDULE = {
    "max_per_day": 2,
    "minimum_gap_hours": 5,
    "preferred_hours": (11, 20),
    "include_weekends": True,
}
KST = ZoneInfo("Asia/Seoul")


class ProductionPolicyError(RuntimeError):
    """Raised before a nonconforming artifact can reach a provider."""


def normalize_notebook_instruction(value: str) -> str:
    """Normalize provider text exactly as the pinned instruction audit does."""
    return unicodedata.normalize(
        "NFKC", str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    ).strip()


def notebook_instruction_sha256(value: str) -> str:
    return hashlib.sha256(normalize_notebook_instruction(value).encode("utf-8")).hexdigest()


def _shorts_claim_is_qualified(sentence: str, match: re.Match[str], claim: str) -> bool:
    """Allow only qualification that is grammatically tied to this claim match.

    A source actor elsewhere in the sentence or a caveat about a different
    property must not waive the matched claim.  The deliberately narrow forms
    below keep the machine gate predictable instead of attempting open-ended
    Korean semantic interpretation.
    """

    before = sentence[max(0, match.start() - 48):match.start()]
    after = sentence[match.end():match.end() + 72]
    actor = r"(?:영상\s*)?(?:제작자|화자|원본|출처|영상)"
    reporting = (
        r"(?:라고|다고|는다고|한다고|했다고)[^,;.!?\n]{0,24}"
        r"(?:말했|언급했|주장했|소개했|시연했|표현했|"
        r"말합|언급합|주장합|소개합|시연합|표현합|전했|전합)"
    )
    if re.search(actor + r"[^,;.!?\n]{0,32}$", before) and re.search(reporting, after):
        return True
    if re.search(
        r"(?:영상|원본|출처)(?:에서는?|에\s*따르면|를\s*보면)\s*$",
        before,
    ):
        return True

    tail = sentence[match.start():match.end() + 96]
    direct_negations = {
        "all_or_any_source": (
            r"(?:모든|어떤)[^,;.!?\n]{0,48}(?:지원|처리|받)(?:하)?는\s*(?:것|건)?은?\s*(?:아니|아닙)",
            r"지원\s*범위[^,;.!?\n]{0,24}(?:확인이\s*필요|확인해야)",
            r"(?:지원|처리)(?:하)?는지[^,;.!?\n]{0,20}(?:확인이\s*필요|확인해야)",
        ),
        "guaranteed_two_clicks": (
            r"(?:2|두)[^,;.!?\n]{0,48}(?:완성|결과)[^,;.!?\n]{0,24}(?:보장하지\s*않|보장되지\s*않)",
            r"(?:2|두)[^,;.!?\n]{0,48}(?:결과|완성)\s*여부[^,;.!?\n]{0,20}(?:확인이\s*필요|확인해야)",
        ),
        "free_or_unlimited": (
            r"(?:무료|공짜|무제한)(?:가|이|은|는|\s)*(?:아니|아닙)",
            r"(?:무료|공짜|무제한)[^,;.!?\n]{0,36}(?:요금|비용|가격)[^,;.!?\n]{0,20}(?:확인이\s*필요|확인해야)",
            r"(?:무료|공짜|무제한)(?:인지|\s*여부)[^,;.!?\n]{0,20}(?:확인이\s*필요|확인해야)",
        ),
        "fixed_generation_time": (
            r"\d+[^,;.!?\n]{0,56}(?:고정\s*시간|고정된\s*시간|뜻)(?:은|이)?\s*(?:아니|아닙)",
            r"(?:생성|소요)\s*시간[^,;.!?\n]{0,20}(?:조건에\s*따라|확인이\s*필요|확인해야)",
            r"\d+[^,;.!?\n]{0,48}(?:완성|생성|제작)(?:되|하)?는지[^,;.!?\n]{0,20}(?:확인이\s*필요|확인해야)",
        ),
        "automatic_cross_platform_distribution": (
            r"(?:자동|동시|무인)[^,;.!?\n]{0,64}자체\s*기능(?:은|이)?\s*(?:아니|아닙)",
            r"(?:자동|동시|무인)[^,;.!?\n]{0,48}(?:배포|게시|업로드|유포)[^,;.!?\n]{0,20}보장(?:하지|되지)\s*않",
            r"(?:자동|동시|무인)[^,;.!?\n]{0,48}(?:배포|게시|업로드|유포)\s*여부[^,;.!?\n]{0,20}(?:확인이\s*필요|확인해야)",
        ),
    }
    return any(re.search(pattern, tail, re.IGNORECASE) for pattern in direct_negations[claim])


SHORTS_REPURPOSE_BOUNDARY_SENTENCES = (
    "Repurpose는 NotebookLM과 별개의 외부 워크플로우입니다.",
    "별도 연결 설정과 각 플랫폼 공급자 지원이 확인된 채널에만 배포할 수 있습니다.",
)
SHORTS_DISTRIBUTION_PREDICATE = (
    r"(?:게시(?!물)|배포|(?<!다운)업로드|유포|발행(?!물)|"
    r"올리|올립|내보내|내보낼|전송|공유|송출)"
)


def _is_platform_distribution_sentence(sentence: str) -> bool:
    """Identify platform-targeted distribution without treating source ingestion as distribution."""

    distribution_actions = list(
        re.finditer(SHORTS_DISTRIBUTION_PREDICATE, sentence, re.IGNORECASE)
    )
    if not distribution_actions:
        return False
    upload_action = distribution_actions[0] if len(distribution_actions) == 1 else None
    notebooklm_destination = None
    if upload_action and upload_action.group(0) == "업로드":
        before_upload = sentence[:upload_action.start()]
        notebooklm_destination = re.search(
            r"(?:NotebookLM|노트북엘엠)\s*에(?P<tail>[^,;.!?\n]{0,64})$",
            before_upload,
            re.IGNORECASE,
        )
        if notebooklm_destination:
            tail = notebooklm_destination.group("tail")
            external_destination = re.search(
                r"(?:인스타(?:그램)?|틱톡|유튜브\s*쇼츠|링크드인|SNS|"
                r"소셜\s*미디어|플랫폼|채널)\s*(?:에|(?:으)?로)",
                tail,
                re.IGNORECASE,
            )
            if external_destination:
                notebooklm_destination = None
    if (
        notebooklm_destination
        and len(distribution_actions) == 1
        and distribution_actions[0].group(0) == "업로드"
    ):
        return False
    platform_marker = re.search(
        r"(?:인스타(?:그램)?|틱톡|유튜브\s*쇼츠|링크드인|SNS|"
        r"소셜\s*미디어|플랫폼|채널)",
        sentence,
        re.IGNORECASE,
    )
    return bool(platform_marker)


def find_forbidden_shorts_claims(value: str) -> dict[str, list[str]]:
    """Find source-independent claim shapes; source fact gates remain additive."""
    text = normalize_notebook_instruction(value)
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text)
        if sentence.strip()
    ]
    hits: dict[str, list[str]] = {}
    for name, patterns in SHORTS_FORBIDDEN_CLAIM_PATTERNS.items():
        values = {
            match.group(0).strip()
            for sentence in sentences
            for pattern in patterns
            for match in re.finditer(pattern, sentence, re.IGNORECASE)
            if not _shorts_claim_is_qualified(sentence, match, name)
        }
        if values:
            hits[name] = sorted(values)

    exact_boundaries = set(SHORTS_REPURPOSE_BOUNDARY_SENTENCES)
    repurpose_mentioned = any(
        re.search(r"(?:Repurpose|리퍼퍼스)", sentence, re.IGNORECASE)
        for sentence in sentences
    )
    platform_distribution_mentioned = any(
        _is_platform_distribution_sentence(sentence) for sentence in sentences
    )
    distribution_context = repurpose_mentioned or platform_distribution_mentioned
    if distribution_context:
        missing = [boundary for boundary in exact_boundaries if boundary not in sentences]
        if missing:
            hits["repurpose_boundary_missing"] = missing
        duplicate_boundaries = {
            boundary: sentences.count(boundary)
            for boundary in exact_boundaries
            if sentences.count(boundary) > 1
        }
        if duplicate_boundaries:
            hits["repurpose_boundary_cardinality"] = [
                f"{boundary} count={count}"
                for boundary, count in sorted(duplicate_boundaries.items())
            ]

        residual_distribution = []
        platform_distribution = []
        contextual_actor = re.compile(
            r"(?:(?:이|그|저|해당|전용|이런|그런)\s*"
            r"(?:도구|앱|서비스|프로그램|워크플로우|솔루션|시스템)|"
            r"이것|그것|이를|그걸|이걸)",
            re.IGNORECASE,
        )
        for sentence in sentences:
            if sentence in exact_boundaries:
                continue
            has_repurpose = re.search(r"(?:Repurpose|리퍼퍼스)", sentence, re.IGNORECASE)
            has_distribution_predicate = re.search(
                SHORTS_DISTRIBUTION_PREDICATE, sentence, re.IGNORECASE
            )
            has_platform_distribution = _is_platform_distribution_sentence(sentence)
            has_contextual_distribution = bool(
                has_distribution_predicate and contextual_actor.search(sentence)
            )
            if has_repurpose or has_platform_distribution or has_contextual_distribution:
                residual_distribution.append(sentence.strip())
            if has_platform_distribution:
                platform_distribution.append(sentence.strip())
        if residual_distribution:
            hits["repurpose_or_platform_distribution_extra"] = sorted(
                set(residual_distribution)
            )
        if platform_distribution:
            hits["automatic_cross_platform_distribution"] = sorted(
                set(platform_distribution)
            )
    return hits


def validate_shorts_verbatim_claims(value: str) -> dict[str, Any]:
    """Reject known generalized claims before verbatim narration can proceed."""
    hits = find_forbidden_shorts_claims(value)
    if hits:
        raise ProductionPolicyError(
            "쇼츠 NotebookLM 그대로 보존 본문에 금지 주장이 있습니다: "
            + ", ".join(sorted(hits))
        )
    return {"status": "pass", "forbidden_claim_hits": {}}


def validate_shorts_notebook_retry(
    source_key: str,
    attempts: Iterable[dict[str, Any]],
    *,
    instruction_version: str = SHORTS_NOTEBOOK_INSTRUCTION_VERSION,
    instruction_sha256: str = SHORTS_NOTEBOOK_INSTRUCTION_SHA256,
) -> dict[str, Any]:
    """Reject a second provider attempt for one source and instruction pin."""

    source = str(source_key or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", source):
        raise ProductionPolicyError("쇼츠 재시도 원본 source_key가 정확하지 않습니다.")
    records = list(attempts)
    blocking = []
    for raw in records:
        attempt = raw if isinstance(raw, dict) else {}
        if str(attempt.get("source_key") or attempt.get("sourceKey") or "") != source:
            continue
        version = str(
            attempt.get("instruction_version")
            or attempt.get("instructionVersion")
            or (attempt.get("instruction") or {}).get("version")
            or ""
        )
        digest = str(
            attempt.get("instruction_sha256")
            or attempt.get("instructionSha256")
            or (attempt.get("instruction") or {}).get("sha256")
            or ""
        )
        status = str(attempt.get("attempt_status") or attempt.get("status") or "")
        substantive = bool(attempt.get("substantive_failure")) or status in SHORTS_ATTEMPT_BLOCKING_STATUSES
        if version == instruction_version and digest == instruction_sha256 and substantive:
            blocking.append(status or "substantive_failed")
    if blocking:
        raise ProductionPolicyError(
            "동일 Shorts NotebookLM 지침 버전/해시에서 이미 공급자 시도를 시작했거나 "
            "실질적 실패가 확인되어 재추출을 중단합니다."
        )
    return {
        "status": "pass",
        "source_key": source,
        "instruction_version": instruction_version,
        "instruction_sha256": instruction_sha256,
        "prior_attempt_count": len(records),
    }


def validate_shorts_notebook_instruction(
    value: str,
    *,
    goal: str = "맞춤",
    response_length: str = "길게",
) -> dict[str, str]:
    """Fail before adding a source when the shared Shorts notebook drifts."""
    normalized = normalize_notebook_instruction(value)
    missing = [marker for marker in SHORTS_NOTEBOOK_REQUIRED_MARKERS if marker not in normalized]
    if missing:
        raise ProductionPolicyError(
            "Shorts NotebookLM 맞춤 지침에 v14 사전 금지 계약이 없습니다. "
            "소스 추가 전 중단합니다."
        )
    actual = notebook_instruction_sha256(value)
    if actual != SHORTS_NOTEBOOK_INSTRUCTION_SHA256:
        raise ProductionPolicyError(
            "Shorts NotebookLM 맞춤 지침이 정본 v14.0과 다릅니다. 소스 추가 전 중단합니다."
        )
    if goal != "맞춤":
        raise ProductionPolicyError("Shorts NotebookLM 응답 목표가 '맞춤'이 아닙니다.")
    if response_length != "길게":
        raise ProductionPolicyError("Shorts NotebookLM 응답 길이가 '길게'가 아닙니다.")
    return {
        "version": SHORTS_NOTEBOOK_INSTRUCTION_VERSION,
        "sha256": actual,
        "goal": goal,
        "response_length": response_length,
    }


def validate_headline_pixel_width(lines: Iterable[str]) -> dict[str, Any]:
    """Use the exact 90px title font to prevent libass soft-wrapping to 3 lines."""
    values = [str(line or "").strip() for line in lines]
    if len(values) != 2 or not all(values):
        raise ProductionPolicyError("쇼츠 헤드카피는 실측 전에도 정확히 2줄이어야 합니다.")
    if not SHORTS_TITLE_FONT_PATH.is_file():
        raise ProductionPolicyError("쇼츠 90px 헤드카피 정본 폰트가 없습니다.")
    try:
        from PIL import ImageFont

        font = ImageFont.truetype(str(SHORTS_TITLE_FONT_PATH), HEADLINE["font_size"])
        widths = [float(font.getlength(line)) for line in values]
    except Exception as exc:
        raise ProductionPolicyError("쇼츠 헤드카피 90px 픽셀 폭을 측정하지 못했습니다.") from exc
    if any(width > HEADLINE_SAFE_WIDTH_PX for width in widths):
        raise ProductionPolicyError(
            f"쇼츠 헤드카피가 90px 안전폭 {HEADLINE_SAFE_WIDTH_PX}px를 넘어 3줄로 접힐 수 있습니다."
        )
    return {
        "font_path": str(SHORTS_TITLE_FONT_PATH),
        "font_size": HEADLINE["font_size"],
        "safe_width_px": HEADLINE_SAFE_WIDTH_PX,
        "line_widths_px": widths,
        "visible_line_count": 2,
    }


def validate_community_provider_text(
    expected_text: str,
    source_url: str,
    runs: Iterable[dict[str, Any]],
    *,
    provider_origin: str = "https://www.youtube.com",
) -> dict[str, Any]:
    """Verify canonical copy when YouTube truncates a visible link run."""

    rebuilt: list[str] = []
    endpoints: list[str] = []
    truncated_runs = 0
    for raw_run in runs:
        run = raw_run if isinstance(raw_run, dict) else {}
        shown = str(run.get("text") or "")
        navigation = run.get("navigationEndpoint") or {}
        endpoint = str(
            ((navigation.get("urlEndpoint") or {}).get("url"))
            or ((navigation.get("commandMetadata") or {}).get("webCommandMetadata") or {}).get("url")
            or ""
        )
        resolved = urljoin(provider_origin, endpoint) if endpoint else ""
        if resolved:
            endpoints.append(resolved)
        if endpoint and "..." in shown:
            rebuilt.append(resolved)
            truncated_runs += 1
        else:
            rebuilt.append(shown)

    def normalize(value: str) -> str:
        return re.sub(r"\n{2,}", "\n\n", str(value or "").replace("\r", "")).strip()

    reconstructed = "".join(rebuilt)
    checks = {
        "body_exact": normalize(reconstructed) == normalize(expected_text),
        "source_url_endpoint_exact": source_url in endpoints,
        "source_url_in_reconstructed_body": source_url in reconstructed,
    }
    if not all(checks.values()):
        raise ProductionPolicyError("Community 공급자 본문 또는 원본 링크 endpoint가 정본과 다릅니다.")
    return {
        "checks": checks,
        "endpoint_urls": endpoints,
        "rendered_text_was_truncated": truncated_runs > 0,
        "truncated_run_count": truncated_runs,
    }


def _cardnews_field(slide: dict[str, Any], *names: str) -> str:
    fields = slide.get("f") or {}
    return " ".join(str(fields.get(name) or "").strip() for name in names).strip()


def validate_cardnews_editorial_deck(deck: dict[str, Any]) -> dict[str, int]:
    """Reject compliance-report decks before they reach rendering or a provider."""
    slides = list((deck or {}).get("slides") or [])
    if len(slides) != CARDNEWS["count"]:
        raise ProductionPolicyError("카드뉴스는 정확히 10장이어야 합니다.")
    if slides[0].get("type") != "cover" or slides[-1].get("type") != "closing":
        raise ProductionPolicyError("카드뉴스는 표지 1장, 본문 8장, 마감 1장 구조여야 합니다.")
    middle = slides[1:-1]
    if len(middle) != CARDNEWS_EDITORIAL["content_count"]:
        raise ProductionPolicyError("카드뉴스 본문은 정확히 8장이어야 합니다.")

    terms = CARDNEWS_EDITORIAL["warning_terms"]
    cover = _cardnews_field(slides[0], "title", "sub")
    if any(term in cover for term in terms):
        raise ProductionPolicyError("표지는 면책·검증 문구가 아니라 독자가 얻을 내용으로 시작해야 합니다.")

    warning_first = 0
    warning_mentions = 0
    useful_cards = 0
    for slide in middle:
        headline = _cardnews_field(slide, "head", "title", "quote")
        body = _cardnews_field(slide, "head", "title", "quote", "desc", "rows")
        headline_is_warning = any(term in headline for term in terms)
        if headline_is_warning:
            warning_first += 1
        if any(term in body for term in terms):
            warning_mentions += 1
        if body and not headline_is_warning:
            useful_cards += 1
    if warning_first > CARDNEWS_EDITORIAL["max_warning_first_cards"]:
        raise ProductionPolicyError("면책·검증 문구가 본문 카드의 주제를 대신하고 있습니다.")
    if useful_cards < 6:
        raise ProductionPolicyError("원본의 방법·과정·사례를 설명하는 본문 카드가 6장 이상 필요합니다.")

    closing = slides[-1].get("f") or {}
    if closing.get("cta1") != CARDNEWS_EDITORIAL["closing_cta1"]:
        raise ProductionPolicyError("마감 카드의 첫 CTA가 고정값과 다릅니다.")
    if closing.get("cta2") != CARDNEWS_EDITORIAL["closing_cta2"]:
        raise ProductionPolicyError("마감 카드의 둘째 CTA가 고정값과 다릅니다.")
    return {
        "slide_count": len(slides),
        "useful_content_cards": useful_cards,
        "warning_first_cards": warning_first,
        "warning_mentions": warning_mentions,
    }


def strip_subtitle_edge_punctuation(token: str) -> str:
    """Remove sentence punctuation at token edges, preserving internal product marks."""
    return token.strip().strip(SUBTITLE_EDGE_PUNCTUATION).strip()


def validate_subtitle_file(path: str | Path) -> dict[str, int]:
    """Fail closed when an SRT caption retains sentence punctuation at either edge."""
    subtitle = Path(path)
    if not subtitle.is_file():
        raise ProductionPolicyError(f"쇼츠 자막 파일이 없습니다: {subtitle}")
    texts: list[str] = []
    for line in subtitle.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if not value or value.isdigit() or "-->" in value:
            continue
        texts.append(value)
    if not texts:
        raise ProductionPolicyError("쇼츠 자막 이벤트가 비어 있습니다.")
    violations = [text for text in texts if text != strip_subtitle_edge_punctuation(text)]
    if violations:
        raise ProductionPolicyError(
            f"쇼츠 자막 양끝 문장부호가 남아 있습니다: {len(violations)}개"
        )
    return {"caption_count": len(texts), "edge_punctuation_violations": 0}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductionPolicyError(f"검증 증거 JSON을 읽을 수 없습니다: {path}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_notebook_binding(kind: str, *, account: str, title: str, notebook_id: str) -> dict[str, str]:
    expected = CAFE_NOTEBOOK if kind == "cafe" else SHORTS_NOTEBOOK if kind == "shorts" else None
    if expected is None:
        raise ProductionPolicyError(f"알 수 없는 NotebookLM 용도입니다: {kind}")
    if account != ASIDE_ACCOUNT:
        raise ProductionPolicyError("NotebookLM은 Aside u0 로그인 세션만 사용합니다.")
    if title.startswith(FORBIDDEN_NOTEBOOK_PREFIXES):
        raise ProductionPolicyError("그지마케팅 NotebookLM 노트북은 이 제작 흐름에서 금지됩니다.")
    if title != expected["title"] or notebook_id != expected["id"]:
        raise ProductionPolicyError(f"{kind} NotebookLM 정본 제목/ID가 일치하지 않습니다.")
    return {"account": account, "title": title, "id": notebook_id}


def validate_presenter_asset(path: str | Path) -> dict[str, str]:
    asset = Path(path).expanduser().resolve()
    expected = MINSOO_PRESENTER_ASSETS.get(asset.name)
    if not asset.is_file() or not expected:
        raise ProductionPolicyError("승인된 민수 촬영본 4개 중 하나가 아닙니다.")
    actual = _sha256(asset)
    if actual != expected:
        raise ProductionPolicyError("민수 촬영본 파일 해시가 승인값과 다릅니다.")
    return {"path": str(asset), "sha256": actual}


def validate_voice_evidence(payload: dict[str, Any]) -> None:
    if payload.get("voice_id") != MINSOO_VOICE_ID or payload.get("model_id") != MINSOO_MODEL_ID:
        raise ProductionPolicyError("민수 Voice ID/모델이 정본과 다릅니다.")
    settings = payload.get("settings") or payload.get("voice_settings")
    if settings != MINSOO_VOICE_SETTINGS:
        raise ProductionPolicyError("민수 음성 설정값이 정본과 다릅니다.")
    alignment = payload.get("alignment") or {}
    if not alignment.get("characters") or not alignment.get("character_start_times_seconds"):
        raise ProductionPolicyError("민수 발음 정렬값이 비어 있습니다.")
    if payload.get("generation_mode") != NARRATION["generation_mode"]:
        raise ProductionPolicyError("민수 음성은 도입·첫째~다섯째·CTA 7구간 합성본이어야 합니다.")
    if payload.get("section_count") != NARRATION["section_count"]:
        raise ProductionPolicyError("민수 음성 구간 수가 정본 7개와 다릅니다.")


def validate_exact_runtime_evidence(payload: dict[str, Any]) -> None:
    if payload.get("status") != "pass":
        raise ProductionPolicyError("정확 대본 런타임 게이트 상태가 pass가 아닙니다.")
    for key in ("script_alignment_hash_match", "caption_count_matches_script_tokens", "caption_tokens_are_whole"):
        if payload.get(key) is not True:
            raise ProductionPolicyError(f"정확 대본 런타임 게이트 실패: {key}")
    pace = payload.get("pace_uniformity") or {}
    ratio = pace.get("last_to_first_ratio")
    if pace.get("status") != "pass" or not isinstance(ratio, (int, float)):
        raise ProductionPolicyError("초반·후반 말하기 속도 균일성 증거가 없습니다.")
    if ratio > NARRATION["last_to_first_pace_ratio_max"]:
        raise ProductionPolicyError("후반 대사 속도가 초반보다 10% 넘게 빨라졌습니다.")


def validate_render_evidence(payload: dict[str, Any]) -> None:
    required_scalars = {
        "output_width": VIDEO["width"],
        "output_height": VIDEO["height"],
        "fps": VIDEO["fps"],
    }
    for key, expected in required_scalars.items():
        if payload.get(key) != expected:
            raise ProductionPolicyError(f"렌더 설정 {key}가 정본과 다릅니다.")
    for section, expected in (
        ("title", HEADLINE),
        ("subtitle", SUBTITLE),
        ("presenter", PRESENTER),
        ("screen", SOURCE_SCREEN),
        ("watermark", WATERMARK),
    ):
        actual = payload.get(section) or {}
        for key, value in expected.items():
            if key == "rule":
                continue
            if actual.get(key) != value:
                raise ProductionPolicyError(f"렌더 설정 {section}.{key}가 정본과 다릅니다.")
    provenance = payload.get("render_provenance") or {}
    if provenance.get("source_and_presenter_audio_mapped") is not False:
        raise ProductionPolicyError("원본/민수 촬영본 오디오는 최종 믹스에 매핑하면 안 됩니다.")
    if provenance.get("source_audio_mapped") is not False or provenance.get("presenter_audio_mapped") is not False:
        raise ProductionPolicyError("원본 영상 또는 민수 촬영본 오디오가 매핑됐습니다.")
    presenter_name = Path(str(provenance.get("presenter_input") or "")).name
    presenter_hash = str(provenance.get("presenter_sha256") or "")
    if MINSOO_PRESENTER_ASSETS.get(presenter_name) != presenter_hash:
        raise ProductionPolicyError("민수 원형 PIP 출처가 승인된 촬영본이 아닙니다.")
    if not provenance.get("source_footage") or not provenance.get("source_footage_sha256"):
        raise ProductionPolicyError("원본 YouTube 영상 출처/해시 증거가 없습니다.")
    restored = provenance.get("v7_reference_restoration") or {}
    if restored.get("voice_settings") != MINSOO_VOICE_SETTINGS:
        raise ProductionPolicyError("V7 음성 정본 복원 증거가 다릅니다.")
    if restored.get("headline_font_size_1080") != HEADLINE["font_size"]:
        raise ProductionPolicyError("V7 헤드카피 90px 증거가 없습니다.")
    if restored.get("subtitle_rule") != SUBTITLE["rule"]:
        raise ProductionPolicyError("V7 자막 전체 토큰 규칙 증거가 없습니다.")


REQUIRED_MACHINE_GATES = (
    "voice_integrated_minus16_tolerance_0_5",
    "voice_true_peak_at_most_minus2",
    "voice_minus_bgm_at_least14_lu",
    "voice_peak_minus_sfx_peak_at_least8_db",
    "final_integrated_minus14_tolerance_0_5",
    "final_true_peak_at_most_minus1_8",
    "full_decode",
    "exact_script_narration_caption_and_config_hashes",
)


def validate_machine_evidence(payload: dict[str, Any]) -> None:
    if payload.get("status") != "pass" or payload.get("failures"):
        raise ProductionPolicyError("쇼츠 머신 검증 상태가 pass가 아닙니다.")
    gates = payload.get("gates") or {}
    missing = [name for name in REQUIRED_MACHINE_GATES if gates.get(name) is not True]
    if missing:
        raise ProductionPolicyError("필수 쇼츠 머신 게이트 실패: " + ", ".join(missing))
    if gates.get("source_audio_mapped") is not False or gates.get("presenter_audio_mapped") is not False:
        raise ProductionPolicyError("음소거되어야 할 원본/PIP 오디오가 매핑됐습니다.")


def validate_shorts_bundle_for_upload(video_path: str | Path) -> dict[str, str]:
    """Require V7 render, voice, and machine evidence before any Studio upload."""
    video = Path(video_path).expanduser().resolve()
    if not video.is_file() or video.stat().st_size <= 0:
        raise ProductionPolicyError(f"쇼츠 MP4가 없거나 비어 있습니다: {video}")
    root = video.parent
    render_path = root / "render_config.json"
    machine_path = root / "machine_validation.json"
    runtime_path = root / "02_exact_runtime_gate.json"
    subtitle_path = root / "captions.srt"
    alignment_candidates = (
        root / "narration_alignment.json",
        root / "09_shorts_minsoo_alignment.json",
        root / "05_minsoo_alignment.json",
    )
    alignment_path = next((path for path in alignment_candidates if path.is_file()), None)
    missing = [str(path.name) for path in (render_path, machine_path, runtime_path, subtitle_path) if not path.is_file()]
    if alignment_path is None:
        missing.append("narration_alignment.json")
    if missing:
        raise ProductionPolicyError("쇼츠 업로드 필수 검증 증거가 없습니다: " + ", ".join(missing))
    validate_render_evidence(_load_json(render_path))
    validate_machine_evidence(_load_json(machine_path))
    validate_voice_evidence(_load_json(alignment_path))
    validate_exact_runtime_evidence(_load_json(runtime_path))
    validate_subtitle_file(subtitle_path)
    return {
        "video": str(video),
        "video_sha256": _sha256(video),
        "render_config": str(render_path),
        "machine_validation": str(machine_path),
        "exact_runtime_gate": str(runtime_path),
        "voice_alignment": str(alignment_path),
        "subtitles": str(subtitle_path),
    }


def validate_schedule(slots: Iterable[datetime]) -> list[datetime]:
    ordered = sorted(slots)
    by_date: dict[Any, list[datetime]] = {}
    for slot in ordered:
        if slot.tzinfo is None or getattr(slot.tzinfo, "key", None) != "Asia/Seoul":
            raise ProductionPolicyError("예약 시각에는 Asia/Seoul 시간대가 필요합니다.")
        by_date.setdefault(slot.date(), []).append(slot)
    if any(len(items) > SCHEDULE["max_per_day"] for items in by_date.values()):
        raise ProductionPolicyError("쇼츠는 하루 최대 2개만 예약합니다.")
    for previous, current in zip(ordered, ordered[1:]):
        if (current - previous).total_seconds() < SCHEDULE["minimum_gap_hours"] * 3600:
            raise ProductionPolicyError("쇼츠 예약 간격은 최소 5시간이어야 합니다.")
    return ordered


def plan_shorts_schedule(
    existing_slots: Iterable[datetime],
    now_kst: datetime,
    *,
    horizon_days: int = 366,
) -> datetime:
    """Choose the next append-only 11:00/20:00 KST slot.

    Existing provider reservations must already be parsed into exact KST
    datetimes.  Callers must fail closed instead of omitting an unparseable
    provider row.  The planner never backfills before the latest reservation;
    every candidate is validated against the complete schedule.
    """

    if now_kst.tzinfo is None or getattr(now_kst.tzinfo, "key", None) != "Asia/Seoul":
        raise ProductionPolicyError("현재 시각에는 Asia/Seoul 시간대가 필요합니다.")
    existing = validate_schedule(existing_slots)
    cursor = max([now_kst, *existing])
    start_date = cursor.date()
    for offset in range(horizon_days + 1):
        candidate_date = start_date + timedelta(days=offset)
        for hour in SCHEDULE["preferred_hours"]:
            candidate = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour,
                tzinfo=KST,
            )
            if candidate <= cursor:
                continue
            try:
                validate_schedule([*existing, candidate])
            except ProductionPolicyError:
                continue
            return candidate
    raise ProductionPolicyError("예약 가능한 쇼츠 슬롯을 찾지 못했습니다.")


def validate_replacement_sequence(events: Iterable[str]) -> None:
    """Protect provider replacements: verify new first, delete old, then CRM."""
    values = list(events)
    required = ["new_provider_verified", "old_cancelled_or_hidden", "old_deleted", "crm_sent"]
    positions = []
    for event in required:
        if event not in values:
            raise ProductionPolicyError(f"교체 절차 증거 누락: {event}")
        positions.append(values.index(event))
    if positions != sorted(positions):
        raise ProductionPolicyError("교체 순서는 새 게시 확인 → 기존 취소/숨김 → 삭제 → CRM입니다.")
