"""쇼츠 업로드 제목을 대본에서 새로 쓴다.

제작 단계가 매니페스트에 넣는 `upload_title`은 화면 헤드카피를 그대로 옮긴 값이다.
헤드카피는 영상 안에서 한 번 더 보이고 표현이 몇 개로 굳어 있어서, 그대로 올리면
채널 목록에 "이 남자 미쳤습니다!" 같은 제목이 줄줄이 쌓인다. 실제로 정기 자동화가
그렇게 올려서 2026-09-21에 여덟 건을 손으로 고쳤다.

그래서 발행 직전에 대본으로 제목을 따로 쓴다. 규칙은 세 가지다.

- 헤드카피를 다시 쓰지 않는다. 화면에 이미 있는 문장이므로 제목이 겹치면 낭비다.
- 이미 쓴 제목과 겹치지 않는다. 채널에 올라간 제목은 `outputs/*/shorts/upload_title.json`과
  각 후보의 공급자 매니페스트에 남아 있으니 그걸 장부로 본다.
- 실패하면 막지 않는다. 제목을 못 쓰면 기존 헤드카피 제목으로 올린다. 발행이 멈추는
  것보다 제목이 덜 좋은 쪽이 낫다.

결과는 후보 폴더의 `upload_title.json`에 남겨서, 같은 후보를 다시 발행해도 제목이
흔들리지 않게 한다.
"""
from __future__ import annotations

import argparse
import configparser
import json
import re
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
MIGRATED_CONFIG = Path("~/orca/projects/ccidacafe/config.ini").expanduser()
TITLE_FILENAME = "upload_title.json"
MAX_TITLE_CHARS = 40

# 반복돼서 채널 목록이 똑같아 보이게 만든 표현들. 새 제목이 이걸로 시작하면 버린다.
WORN_OUT_OPENERS = (
    "이 남자",
    "아직도 직접",
    "충격",
    "이것만",
    "이거 모르면",
)
# 쇼츠 제목에 쓰지 않는 단정. 채널 정책이 금지한 수익 보장·의학 단정과 같은 계열이다.
FORBIDDEN_TITLE_PATTERNS = (
    re.compile(r"월\s*\d+\s*(만원|억)"),
    re.compile(r"(보장|확정|무조건)"),
    re.compile(r"(완치|치료|부작용 없)"),
    re.compile(r"(최초|1위|세계 최고)"),
)


class TitleError(RuntimeError):
    pass


def _api_key() -> str:
    cfg = configparser.RawConfigParser()
    local = PROJECT / "config.ini"
    path = local if local.exists() else MIGRATED_CONFIG
    if path.exists():
        cfg.read(path, encoding="utf-8")
    return cfg.get("GEMINI", "api_key", fallback="").strip()


def used_titles(outputs: Path | None = None) -> set[str]:
    """이미 올렸거나 올리기로 정한 제목을 모은다."""
    outputs = outputs or (PROJECT / "outputs")
    titles: set[str] = set()
    for path in outputs.glob(f"*/shorts/{TITLE_FILENAME}"):
        try:
            titles.add(json.loads(path.read_text(encoding="utf-8"))["title"])
        except (OSError, ValueError, KeyError):
            continue
    for path in outputs.glob("*/shorts/07_provider_manifest.json"):
        try:
            titles.add(json.loads(path.read_text(encoding="utf-8"))["title"])
        except (OSError, ValueError, KeyError):
            continue
    return {t.strip() for t in titles if str(t).strip()}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _tail_word(title: str) -> str:
    words = re.findall(r"[가-힣A-Za-z0-9]+", title)
    return words[-1] if words else ""


def check_title(title: str, *, headcopy: str, taken: set[str]) -> str | None:
    """쓸 수 없는 제목이면 이유를 돌려준다."""
    if not title or "\n" in title:
        return "빈 제목이거나 여러 줄입니다"
    if len(title) > MAX_TITLE_CHARS:
        return f"{MAX_TITLE_CHARS}자를 넘습니다({len(title)}자)"
    if title.startswith(("#", "http")) or "#" in title:
        return "해시태그나 링크가 들어 있습니다"
    if _normalize(title) == _normalize(headcopy):
        return "화면 헤드카피와 같습니다"
    if any(title.startswith(opener) for opener in WORN_OUT_OPENERS):
        return "이미 반복해서 쓴 도입 표현입니다"
    if any(pattern.search(title) for pattern in FORBIDDEN_TITLE_PATTERNS):
        return "채널 정책이 금지한 단정이 들어 있습니다"
    if _normalize(title) in {_normalize(t) for t in taken}:
        return "이미 쓴 제목입니다"
    # 대본이 대부분 "5가지 방법" 꼴이라 제목도 같은 말로 끝나기 쉽다. 목록이 단조로워진다.
    tail = _tail_word(title)
    if tail and sum(_tail_word(t) == tail for t in taken) >= 2:
        return f"최근 제목이 이미 '{tail}'로 끝납니다"
    return None


def _prompt(script: str, headcopy: str, taken: list[str]) -> str:
    recent = "\n".join(f"- {t}" for t in taken[:30]) or "- (없음)"
    return f"""너는 한국어 유튜브 쇼츠 제목을 쓴다. 아래 대본으로 제목 후보 5개를 써라.

규칙:
- 한 줄, 공백 포함 {MAX_TITLE_CHARS}자 이내.
- 대본에 실제로 나오는 내용만 쓴다. 대본에 없는 숫자나 도구 이름을 지어내지 않는다.
- 아래 헤드카피는 영상 화면에 이미 나오므로 제목에 그대로 쓰지 않는다.
- 아래 '이미 쓴 제목'과 겹치거나 비슷한 표현으로 시작하지 않는다.
- 낚시 단정을 쓰지 않는다: 수익 보장, 월 얼마 확정, 최초·1위·세계 최고, 치료·완치.
- '이미 쓴 제목'과 같은 말로 끝내지 않는다. '비결', '방법', 'N가지'로 끝나는 제목이 이미 많다.
- 해시태그, 링크, 따옴표, 이모지를 넣지 않는다.
- 클릭할 이유가 제목 안에 보이게 쓴다. 무엇을 어떻게 하는지가 드러나야 한다.

헤드카피(제목에 쓰지 말 것): {headcopy}

이미 쓴 제목:
{recent}

JSON 하나만 출력한다: {{"titles":["후보1","후보2","후보3","후보4","후보5"]}}

대본:
{script}
"""


def _ask_gemini(prompt: str, api_key: str, model: str) -> list[str]:
    from google import genai
    from google.genai import types

    with genai.Client(api_key=api_key) as client:
        response = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(temperature=0.9),
        )
    text = (response.text or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise TitleError("제목 생성 응답에서 JSON을 찾지 못했습니다.")
    titles = json.loads(match.group(0)).get("titles") or []
    return [str(t).strip().strip('"') for t in titles if str(t).strip()]


def write_title(root: Path, *, model: str = "gemini-2.5-flash", force: bool = False) -> dict:
    """후보 폴더의 대본으로 제목을 새로 쓰고 `upload_title.json`에 남긴다."""
    root = Path(root).expanduser().resolve()
    target = root / TITLE_FILENAME
    production = json.loads((root / "production_manifest.json").read_text(encoding="utf-8"))
    headcopy = str(production["render_inputs"]["upload_title"]).strip()
    if target.exists() and not force:
        return {**json.loads(target.read_text(encoding="utf-8")), "status": "existing"}

    script = (root / "07_script_final.txt").read_text(encoding="utf-8").strip()
    api_key = _api_key()
    if not api_key:
        raise TitleError("제목 생성에 필요한 Gemini API 키가 없습니다.")
    taken = sorted(used_titles())
    rejected: list[dict] = []
    for _ in range(2):
        for candidate in _ask_gemini(_prompt(script, headcopy, taken), api_key, model):
            reason = check_title(candidate, headcopy=headcopy, taken=set(taken))
            if reason is None:
                record = {"title": candidate, "headcopy": headcopy, "model": model,
                          "rejected": rejected, "status": "written",
                          "written_at": datetime.now().astimezone().isoformat()}
                target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                return record
            rejected.append({"title": candidate, "reason": reason})
    raise TitleError(f"쓸 수 있는 제목을 못 얻었습니다: {rejected[:5]}")


def upload_title(root: Path, fallback: str) -> str:
    """발행 경로가 부르는 진입점. 제목 생성이 실패해도 발행을 막지 않는다."""
    try:
        return write_title(root)["title"]
    except Exception as exc:  # noqa: BLE001 - 제목 품질 때문에 발행을 멈추지는 않는다
        note = root / "upload_title_error.txt"
        note.write_text(f"{datetime.now().astimezone().isoformat()} {exc}\n", encoding="utf-8")
        return fallback


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", help="후보의 shorts 폴더 경로")
    parser.add_argument("--force", action="store_true", help="이미 쓴 제목도 다시 쓴다")
    args = parser.parse_args(argv)
    bad = 0
    for raw in args.roots:
        try:
            record = write_title(Path(raw), force=args.force)
            print(json.dumps({"root": raw, "title": record["title"],
                              "status": record["status"]}, ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001 - 한 건 실패가 나머지를 막지 않게 한다
            bad += 1
            print(json.dumps({"root": raw, "status": "error", "error": str(exc)[:300]},
                             ensure_ascii=False), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
