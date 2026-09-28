"""구독 에이전트로 글을 받는다. API 과금 경로를 쓰지 않는다.

제목 생성과 사실확인은 Gemini API를 불렀다. 호출당 요금이 붙고, 키가 새면 그대로
비용이 된다. 이 맥에는 이미 `loopguard/bin/agent-run`이 있고, Claude 구독이 막히면
Codex 구독으로, Codex가 막히면 Claude로 넘긴다. 크론·루프가 에이전트를 부를 때는
이걸 쓰라는 것이 이 맥의 작업 규칙이다.

한도가 바닥나면 두 엔진 모두 실패한다. 그때는 예외를 내고 부르는 쪽이 판단한다.
제목은 기존 헤드카피로 물러서고, 사실확인은 발행을 막는다.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

AGENT_RUN = Path("/Users/apple/orca/projects/loopguard/bin/agent-run")
DEFAULT_TIMEOUT = 420


class SubscriptionAgentError(RuntimeError):
    pass


def run(prompt: str, *, timeout: int = DEFAULT_TIMEOUT, tools: str | None = None,
        prefer: str | None = None) -> str:
    """프롬프트를 구독 에이전트에 넘기고 본문을 돌려준다."""
    if not AGENT_RUN.is_file():
        raise SubscriptionAgentError(f"agent-run을 찾지 못했습니다: {AGENT_RUN}")
    command = [str(AGENT_RUN), "--timeout", str(timeout)]
    if tools:
        command += ["--tools", tools]
    if prefer:
        command += ["--prefer", prefer]
    try:
        # 프롬프트는 stdin 으로 넘긴다. 명령줄 인자는 길이 상한에 걸린다.
        completed = subprocess.run(command, input=prompt, capture_output=True,
                                   text=True, timeout=timeout + 60)
    except subprocess.TimeoutExpired as exc:
        raise SubscriptionAgentError(f"구독 에이전트 응답이 {exc.timeout}초를 넘겼습니다.") from None
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-300:]
        raise SubscriptionAgentError(
            f"구독 에이전트 두 엔진이 모두 실패했습니다(종료코드 {completed.returncode}): {tail}"
        )
    text = (completed.stdout or "").strip()
    if not text:
        raise SubscriptionAgentError("구독 에이전트가 빈 응답을 돌려줬습니다.")
    return text


def run_json(prompt: str, **kwargs) -> dict:
    """JSON 하나를 요구하는 호출. 설명 문장이 섞여 와도 JSON만 건져낸다."""
    text = run(prompt, **kwargs)
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise SubscriptionAgentError(f"응답에서 JSON을 찾지 못했습니다: {text[:200]}")
        raw = match.group(0)
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise SubscriptionAgentError(f"응답 JSON을 읽지 못했습니다: {exc}") from None
    if not isinstance(parsed, dict):
        raise SubscriptionAgentError("응답 JSON이 객체가 아닙니다.")
    return parsed


def status() -> str:
    """지금 어느 엔진이 되는지 한 줄로 확인한다."""
    completed = subprocess.run([str(AGENT_RUN), "--status"], capture_output=True,
                               text=True, timeout=120)
    return (completed.stdout or completed.stderr or "").strip()
