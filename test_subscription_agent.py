"""구독 에이전트 호출기 시험. 실제 호출은 하지 않는다."""
from __future__ import annotations

import pathlib
import subprocess

import pytest

import subscription_agent as agent


def _completed(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(args=["agent-run"], returncode=code,
                                       stdout=stdout, stderr=stderr)


def test_prompt_goes_through_stdin_not_the_command_line(monkeypatch):
    """긴 원고를 인자로 넘기면 명령줄 길이 상한에 걸린다."""
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["input"] = kwargs.get("input")
        return _completed(stdout="결과 문장")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)
    assert agent.run("원고" * 5000) == "결과 문장"
    assert seen["input"].startswith("원고")
    assert not any("원고" in str(part) for part in seen["command"])


def test_both_engines_failing_raises(monkeypatch):
    """한도가 바닥나면 두 엔진이 모두 실패한다. 조용히 넘어가면 안 된다."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stderr="quota", code=2))
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)
    with pytest.raises(agent.SubscriptionAgentError, match="두 엔진이 모두 실패"):
        agent.run("프롬프트")


def test_empty_answer_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout="  \n"))
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)
    with pytest.raises(agent.SubscriptionAgentError, match="빈 응답"):
        agent.run("프롬프트")


@pytest.mark.parametrize("stdout", [
    '{"titles":["가","나"]}',
    '설명이 앞에 붙습니다.\n{"titles":["가","나"]}\n뒤에도 붙습니다.',
    '```json\n{"titles":["가","나"]}\n```',
])
def test_json_is_recovered_from_a_chatty_answer(monkeypatch, stdout):
    """에이전트는 JSON만 달라고 해도 설명을 덧붙이곤 한다."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout=stdout))
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)
    assert agent.run_json("프롬프트") == {"titles": ["가", "나"]}


def test_answer_without_json_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout="JSON 없이 답했다"))
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)
    with pytest.raises(agent.SubscriptionAgentError, match="JSON을 찾지 못했습니다"):
        agent.run_json("프롬프트")


def test_missing_agent_run_is_reported(monkeypatch):
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: False)
    with pytest.raises(agent.SubscriptionAgentError, match="agent-run"):
        agent.run("프롬프트")
