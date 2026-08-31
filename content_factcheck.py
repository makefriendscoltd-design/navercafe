"""Mandatory correction-only fact check for NotebookLM-derived content."""

from __future__ import annotations

import json
import os
from datetime import date


class FactCheckError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start < 0 or end <= start:
        raise FactCheckError("사실확인 응답에서 JSON을 찾지 못했습니다.")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise FactCheckError("사실확인 JSON을 해석하지 못했습니다.") from exc
    if not isinstance(data, dict):
        raise FactCheckError("사실확인 응답이 객체가 아닙니다.")
    return data


def apply_corrections(text: str, corrections: list[dict]) -> str:
    result = text
    for item in corrections:
        old = str(item.get("old") or "")
        new = str(item.get("new") or "")
        if not old:
            raise FactCheckError("사실 수정 항목의 old 문자열이 비어 있습니다.")
        if old not in result:
            raise FactCheckError(f"사실 수정 대상을 원고에서 찾지 못했습니다: {old[:80]}")
        result = result.replace(old, new)
    return result


def factcheck_manuscript(text: str, api_key: str, *, model: str | None = None) -> tuple[str, dict]:
    if not api_key:
        raise FactCheckError("사실확인에 필요한 Gemini API 키가 없습니다.")
    from google import genai
    from google.genai import types

    prompt = f"""오늘 날짜는 {date.today().isoformat()}이다.
아래 원고는 외부 YouTube 영상을 NotebookLM이 옮긴 초안이다. 발행 전 사실확인만 수행하라.

반드시 Google 검색을 사용하고, 다음 항목은 원문에서 넘어왔더라도 모두 미검증으로 취급한다.
- AI 모델명·버전·출시일·가격·공식 벤치마크 수치·회사/벤더 주장
- '최신', '세계 최초/1위/최고' 같은 현재성·순위 단정
- 수익 보장, 허위 성과 수치, 의학적 치료·완치·효능 단정

규칙:
- 모델 버전과 벤더 사실은 오늘의 공식 벤더 페이지를 우선한다.
- 최신 기능을 부정하거나 삭제하기 전에는 제품 홈페이지가 아니라 해당 기능명으로 공식 도움말·공식 문서를 다시 검색한다.
- 화자가 화면에서 직접 시연한 기능이나 워크플로는 일반 소개 페이지에 없다는 이유만으로 삭제하지 않는다. 기능의 공식 문서와 시연 맥락을 함께 확인한다.
- 확인되지 않거나 낡은 주장은 고치거나 제거한다.
- 화자가 자기 워크플로를 설명한 수치나 의견은 인용된 주장으로 두고 함부로 지우지 않는다.
- 관련 없는 문장은 절대 고치지 않는다. 이것은 재작성 작업이 아니라 교정 패스다.
- 수정할 때 old에는 원고에 실제로 존재하는 정확한 연속 문자열을 넣는다.
- source에는 확인한 공식 URL을 넣는다.

JSON 하나만 출력한다:
{{"status":"ok|fixed|blocked","summary":"짧은 결과", "corrections":[{{"old":"원문 문자열","new":"교정 문자열 또는 삭제면 빈 문자열","reason":"이유","source":"공식 URL"}}], "checked_claims":[{{"claim":"검증한 주장","source":"공식 URL"}}]}}

원고:
{text}
"""
    try:
        with genai.Client(api_key=api_key) as client:
            response = client.models.generate_content(
                model=model or os.environ.get("FACTCHECK_MODEL", "gemini-2.5-flash"),
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0,
                ),
            )
    except Exception as exc:
        raise FactCheckError("웹검색 기반 사실확인 요청이 실패했습니다.") from exc

    report = _extract_json(response.text or "")
    status = str(report.get("status") or "").lower()
    corrections = report.get("corrections") or []
    if status == "blocked":
        raise FactCheckError(str(report.get("summary") or "사실확인을 완료하지 못했습니다."))
    if status not in {"ok", "fixed"}:
        raise FactCheckError("사실확인 status가 ok/fixed가 아닙니다.")
    if not isinstance(corrections, list):
        raise FactCheckError("사실확인 corrections가 목록이 아닙니다.")
    if status == "fixed" and not corrections:
        raise FactCheckError("사실확인 결과는 fixed인데 수정 항목이 없습니다.")
    for item in corrections:
        source = str(item.get("source") or "").strip()
        if not source.startswith(("https://", "http://")):
            raise FactCheckError("사실 수정 항목에 확인 가능한 출처 URL이 없습니다.")
    corrected = apply_corrections(text, corrections)
    report["status"] = "fixed" if corrections else "ok"
    return corrected, report
