# -*- coding: utf-8 -*-
"""Pure contracts shared by the Cafe publisher and its scheduler.

This module intentionally imports only the Python standard library so the
layout and result contracts can be tested without Selenium, OpenCV, or a
logged-in Naver session.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from datetime import datetime, timezone


REFERENCE_TEMPLATE = "reference-5854"
SCENE_MARKER = "[[SCENE]]"
IMAGE_MARKER = "[IMAGE_HERE]"
REFERENCE_IMAGE_COUNT = 5
REFERENCE_TEXT_GROUP_COUNT = REFERENCE_IMAGE_COUNT + 1
REFERENCE_PARAGRAPH_COUNTS = (4, 8, 11, 8, 6, 4)
APPROVED_CLUB_ID = "26321967"
APPROVED_MENU_ID = "315"


# 5854의 핵심은 단순한 6구간 분할이 아니라 이 문장 흐름 자체다.
# 각 정규식은 같은 위치의 문단이 유지해야 할 짧은 전개 문구만 고정하고,
# 강의별 사실·도구·숫자·사례는 자유롭게 바뀌도록 둔다.
REFERENCE_PARAGRAPH_PATTERNS = (
    (
        r"^이게 말이 됩니까\?$",
        r"^처음 AI가",
        r"^이건 진짜 경이로운 수준이다\.$",
        r"^오늘 내용은 바쁜 분들을 위해",
    ),
    (
        r"^예전엔",
        r"^이 .+ 하나, .+ 하나",
        r"^그러다",
        r"^하지만",
        r"^지나고 보니",
        r"^그런데 이제는",
        r"^여기에 한번 빠지시면",
        r"패러다임이 완전히 뒤바뀐 겁니다\.$",
    ),
    (
        r"^왜 AI랑 대화만 시작하면.+\?$",
        r"^대부분 AI한테",
        r"^그러니 당연히",
        r"때문입니다\.$",
        r"^구체적인 재료를 던져줘야 합니다\.$",
        r"^단순히 지어내지 말고",
        r"^내 말투",
        r"^이런 식으로 명확한 재료와 지침을 쥐여줘야",
        r"일반적인 챗봇과 차원이 다릅니다\.$",
        r"^단순히",
        r"^내가 전달한",
    ),
    (
        r"^게다가 속도를 보면 진짜 깜짝 놀라실 겁니다\.$",
        r"^주제 선정부터",
        r"^자, 이제",
        r"^AI가",
        r"^이게 말이 됩니까\? 진짜 경이롭다는 말이 절로 나옵니다\.$",
        r"^이렇게 실행된 결과물들을 확인해 보면,?$",
        r"^눈앞에서 유능한",
        r"노가다가 완전히 증발하는 순간입니다\.$",
    ),
    (
        r"^여기서 꼭 나오는 질문이 있습니다\.$",
        r"^[\"“]그래도 결국 내가 직접.+부분이 있지 않나요\?[\"”]$",
        r"^맞습니다\.",
        r"^하지만 여기서 제가 비장의 치트키를 알려 드립니다\.$",
        r"^AI를 [\"'‘]대신.+[\"'’]가 아닌 [\"'‘]지능형 파트너[\"'’]로 활용해 보세요\.$",
        r"^반복적이고",
    ),
    (
        r"^그러면 신기하게도",
        r"^이제 남은 시간에는 더 본질적인",
        r"^더 이상",
        r"^더욱 구체적인 실제 시연 과정과 비하인드 꿀팁들은",
    ),
)


class PublisherContractError(ValueError):
    """Raised when content cannot safely match the selected template."""


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", (text or "").strip()) if part.strip()]


def _balanced_groups(parts: list[str], group_count: int) -> list[str]:
    """Keep every paragraph unchanged while distributing them across groups."""
    if len(parts) < group_count:
        raise PublisherContractError(
            f"기준글 포맷에는 본문 구간이 {group_count}개 필요하지만 "
            f"원고 문단은 {len(parts)}개뿐입니다."
        )

    base, remainder = divmod(len(parts), group_count)
    groups = []
    cursor = 0
    for index in range(group_count):
        size = base + (1 if index < remainder else 0)
        groups.append("\n\n".join(parts[cursor:cursor + size]))
        cursor += size
    return groups


def _reference_groups_from_paragraphs(parts: list[str]) -> list[str]:
    expected = sum(REFERENCE_PARAGRAPH_COUNTS)
    if len(parts) != expected:
        raise PublisherContractError(
            f"기준글 포맷의 문단은 총 {expected}개여야 하지만 {len(parts)}개입니다."
        )

    groups = []
    cursor = 0
    for count in REFERENCE_PARAGRAPH_COUNTS:
        groups.append("\n\n".join(parts[cursor:cursor + count]))
        cursor += count
    return groups


def validate_reference_5854_body(body: str, image_count: int = REFERENCE_IMAGE_COUNT) -> dict:
    """Validate the 5854 interleaving contract and return its measured shape."""
    if image_count != REFERENCE_IMAGE_COUNT:
        raise PublisherContractError(
            f"{REFERENCE_TEMPLATE} 템플릿은 이미지 {REFERENCE_IMAGE_COUNT}장만 허용합니다."
        )

    marker_count = (body or "").count(IMAGE_MARKER)
    if marker_count != image_count:
        raise PublisherContractError(
            f"이미지 위치가 {image_count}개여야 하지만 {marker_count}개입니다."
        )

    groups = [part.strip() for part in body.split(IMAGE_MARKER)]
    if len(groups) != REFERENCE_TEXT_GROUP_COUNT or any(not part for part in groups):
        raise PublisherContractError(
            f"텍스트 구간이 빈칸 없이 {REFERENCE_TEXT_GROUP_COUNT}개여야 합니다."
        )

    if SCENE_MARKER in body:
        raise PublisherContractError("장면 구분 마커가 최종 본문에 남아 있습니다.")
    if re.search(r"\[/?BLOCKQUOTE\]", body, re.IGNORECASE):
        raise PublisherContractError("기준글 포맷에는 인용구 블록을 사용할 수 없습니다.")
    if re.search(r"(?m)^\s{0,3}#{1,6}\s+", body):
        raise PublisherContractError("기준글 포맷에는 마크다운 소제목을 사용할 수 없습니다.")

    paragraph_counts = tuple(len(_paragraphs(group)) for group in groups)
    if paragraph_counts != REFERENCE_PARAGRAPH_COUNTS:
        raise PublisherContractError(
            "기준글 문단 배열은 "
            f"{'·'.join(map(str, REFERENCE_PARAGRAPH_COUNTS))}이어야 하지만 "
            f"{'·'.join(map(str, paragraph_counts))}입니다."
        )

    drifted_paragraphs = []
    for group_index, (group, patterns) in enumerate(
            zip(groups, REFERENCE_PARAGRAPH_PATTERNS), start=1):
        paragraphs = _paragraphs(group)
        for paragraph_index, (paragraph, pattern) in enumerate(
                zip(paragraphs, patterns), start=1):
            if not re.search(pattern, paragraph.strip()):
                drifted_paragraphs.append(f"{group_index}구간 {paragraph_index}문단")
    if drifted_paragraphs:
        raise PublisherContractError(
            "기준글 고정 전개 문구가 달라진 위치: "
            + ", ".join(drifted_paragraphs)
        )

    if "마법" in body:
        raise PublisherContractError(
            "기준글의 비유 표현은 현재 문체 규칙에 맞게 바꿔야 합니다."
        )

    return {
        "template": REFERENCE_TEMPLATE,
        "textGroupCount": len(groups),
        "imageMarkerCount": marker_count,
        "paragraphCounts": list(paragraph_counts),
    }


def build_reference_5854_body(manuscript: str, image_count: int = REFERENCE_IMAGE_COUNT) -> str:
    """Build six text groups interleaved with five image markers.

    Preferred input uses five standalone ``[[SCENE]]`` markers generated by
    NotebookLM. A marker-free manuscript can still be used only when it has the
    exact 41-paragraph reference shape; those paragraphs are grouped without
    rewriting them.
    A partial marker set is rejected instead of guessing around malformed AI
    output.
    """
    text = (manuscript or "").strip()
    if not text:
        raise PublisherContractError("원고가 비어 있습니다.")
    if image_count != REFERENCE_IMAGE_COUNT:
        raise PublisherContractError(
            f"{REFERENCE_TEMPLATE} 템플릿은 이미지 {REFERENCE_IMAGE_COUNT}장만 허용합니다."
        )

    marker_count = text.count(SCENE_MARKER)
    if marker_count:
        if marker_count != image_count:
            raise PublisherContractError(
                f"장면 구분 마커가 {image_count}개여야 하지만 {marker_count}개입니다."
            )
        groups = [part.strip() for part in text.split(SCENE_MARKER)]
        if len(groups) != REFERENCE_TEXT_GROUP_COUNT or any(not part for part in groups):
            raise PublisherContractError(
                f"장면 구분 마커 사이에 텍스트 구간이 "
                f"{REFERENCE_TEXT_GROUP_COUNT}개 있어야 합니다."
            )
    else:
        groups = _reference_groups_from_paragraphs(_paragraphs(text))

    body = f"\n\n{IMAGE_MARKER}\n\n".join(groups)
    validate_reference_5854_body(body, image_count)
    return body


def reference_body_without_markers(body: str) -> str:
    """Return content text for manuscript-preservation assertions."""
    return re.sub(r"\s+", "", (body or "").replace(IMAGE_MARKER, ""))


def notification_message(title: str, article_url: str) -> str:
    title = (title or "").strip()
    article_url = (article_url or "").strip()
    if not title or not article_url:
        raise PublisherContractError("텔레그램 알림에는 제목과 카페 글 URL이 필요합니다.")
    return (
        "새 라이브 정리글이 올라왔습니다.\n\n"
        f"{title}\n\n"
        "한 번 확인 부탁드립니다.\n"
        f"{article_url}"
    )


def assert_cafe_target(cafe_url: str, expected_club_id: str = "",
                       expected_menu_id: str = "") -> dict:
    """Fail closed when the configured Naver board differs from the approved target."""
    if expected_club_id and str(expected_club_id) != APPROVED_CLUB_ID:
        raise PublisherContractError(
            f"예약 발행 카페 ID는 승인 대상 {APPROVED_CLUB_ID}만 허용합니다."
        )
    if expected_menu_id and str(expected_menu_id) != APPROVED_MENU_ID:
        raise PublisherContractError(
            f"예약 발행 게시판 ID는 승인 대상 {APPROVED_MENU_ID}만 허용합니다."
        )
    decoded = urllib.parse.unquote(cafe_url or "")
    club_match = re.search(r"(?:cafes/|clubid[=&])(\d+)", decoded, re.IGNORECASE)
    menu_match = re.search(r"(?:menus/|menuid[=&]|boardId[=&])(\d+)", decoded, re.IGNORECASE)
    actual_club = club_match.group(1) if club_match else ""
    actual_menu = menu_match.group(1) if menu_match else ""
    if expected_club_id and actual_club != str(expected_club_id):
        raise PublisherContractError(
            f"카페 ID가 승인 대상 {expected_club_id}와 다릅니다 (현재 {actual_club or '확인불가'})."
        )
    if expected_menu_id and actual_menu != str(expected_menu_id):
        raise PublisherContractError(
            f"게시판 ID가 승인 대상 {expected_menu_id}와 다릅니다 (현재 {actual_menu or '확인불가'})."
        )
    return {"clubId": actual_club, "menuId": actual_menu}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_result(path: str | None, payload: dict) -> dict:
    """Atomically write the machine-readable publisher outcome when requested."""
    result = dict(payload)
    result.setdefault("updatedAt", utc_now_iso())
    if not path:
        return result

    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, target)
    return result
