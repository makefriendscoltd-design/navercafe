"""Run the YouTube Studio provider programs in Ego Lite instead of Aside.

The provider programs in ``youtube_shorts_aside_adapter`` and
``youtube_shorts_inventory`` are unchanged; ``youtube_ego_runtime.mjs``
supplies the Aside-style page API they use on top of one persistent
ego-browser TaskSpace.  Results use the same ``__ASIDE_RESULT__`` marker, so
the existing result parsing and evidence checks apply as-is.

Backend selection: ``YOUTUBE_SHORTS_BROWSER=ego`` (default) or ``aside``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping

PROJECT = Path(__file__).resolve().parent
RUNTIME_JS = PROJECT / "youtube_ego_runtime.mjs"
SPACE_FILE = Path(
    os.environ.get(
        "YOUTUBE_SHORTS_EGO_SPACE",
        str(PROJECT / "outputs" / "shorts-ego-runtime" / "space.json"),
    )
)
BACKEND_ENV = "YOUTUBE_SHORTS_BROWSER"


class EgoRuntimeError(RuntimeError):
    """ego-browser is unavailable or its TaskSpace is not initialized."""


def backend() -> str:
    value = os.environ.get(BACKEND_ENV, "ego").strip().lower()
    if value not in {"ego", "aside"}:
        raise EgoRuntimeError(f"{BACKEND_ENV} must be ego or aside, got {value!r}")
    return value


def resolve_ego_cli() -> str:
    command = shutil.which("ego-browser") or str(Path.home() / ".local/bin/ego-browser")
    if not Path(command).is_file():
        raise EgoRuntimeError("ego-browser is not installed/onboarded")
    return command


def space_id() -> int:
    try:
        data = json.loads(SPACE_FILE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise EgoRuntimeError(
            f"Ego Studio TaskSpace must be initialized explicitly in {SPACE_FILE}"
        ) from exc
    value = data.get("spaceId")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EgoRuntimeError(f"invalid spaceId in {SPACE_FILE}")
    return value


def compose(body: str, payload: Mapping[str, Any], *, cwd: Path) -> str:
    config = {"spaceId": space_id(), "cwd": str(Path(cwd).resolve())}
    script = RUNTIME_JS.read_text()
    for marker, value in (
        ("__YT_EGO_CONFIG__", json.dumps(config, ensure_ascii=False)),
        ("__YT_EGO_PAYLOAD__", json.dumps(dict(payload), ensure_ascii=False)),
    ):
        if script.count(marker) != 1:
            raise EgoRuntimeError(f"runtime template marker {marker} is not unique")
        script = script.replace(marker, value)
    if script.count("__YT_EGO_BODY__") != 1:
        raise EgoRuntimeError("runtime template body marker is not unique")
    return script.replace("__YT_EGO_BODY__", body)


def run_js(body: str, payload: Mapping[str, Any], *, cwd: Path, timeout: int) -> dict[str, Any]:
    from aside_browser import _parse_result

    command = resolve_ego_cli()
    script = compose(body, payload, cwd=cwd)
    try:
        proc = subprocess.run(
            [command, "nodejs"],
            input=script,
            text=True,
            capture_output=True,
            cwd=str(cwd),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise EgoRuntimeError(f"ego-browser call exceeded {timeout}s") from exc
    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    return _parse_result(output)


def _stage_file(cwd: Path, source_path: str, size: int, digest: str) -> str:
    """Copy the candidate into ``provider-stage-<hex>/final.mp4`` under cwd."""

    source = Path(source_path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size != size:
        raise EgoRuntimeError("staged upload size differs from the bound candidate")
    stage = Path(cwd) / f"provider-stage-{uuid.uuid4().hex}"
    stage.mkdir(parents=True)
    target = stage / "final.mp4"
    shutil.copyfile(source, target)
    sha = hashlib.sha256()
    with target.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    if sha.hexdigest() != digest:
        shutil.rmtree(stage, ignore_errors=True)
        raise EgoRuntimeError("staged upload sha256 differs from the bound candidate")
    return f"{stage.name}/final.mp4"


def provider_runner():
    """Return a runner with the ``AsideRunner`` call contract."""

    resolve_ego_cli()
    space_id()

    def run(body: str, payload: Mapping[str, Any], *, cwd: Path, timeout: int) -> Mapping[str, Any]:
        transfer = payload.get("file_transfer")
        if not isinstance(transfer, Mapping):
            return run_js(body, payload, cwd=cwd, timeout=timeout)
        if transfer.get("transport") != "aside-session-path/v1":
            raise EgoRuntimeError("unknown file transfer contract")
        size, digest = transfer.get("size"), transfer.get("sha256")
        if (
            not isinstance(transfer.get("source_path"), str)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 1
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise EgoRuntimeError("file transfer binding is invalid")
        relative = _stage_file(Path(cwd), transfer["source_path"], size, digest)
        final_payload = dict(payload)
        final_payload.pop("file_transfer", None)
        final_payload["file"] = {"name": "final.mp4", "path": relative, "size": size, "sha256": digest}
        return run_js(body, final_payload, cwd=cwd, timeout=timeout)

    return run
