"""Owned stdio connection to Aside's persistent u0 MCP REPL; never retries."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path


class AsideMCPError(RuntimeError):
    pass


class AsideMCPTimeout(AsideMCPError):
    pass


class AsideMCP:
    def __init__(self, executable: str, *, account: str = "u0",
                 cwd: str | Path | None = None, timeout: float = 45):
        if account != "u0":
            raise ValueError("Aside MCP account must be u0")
        self.timeout = timeout
        self._next_id = 0
        self._closed = False
        self._messages = queue.Queue()
        self._process = subprocess.Popen(
            [executable, "mcp", "--account", account], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        try:
            result = self._request("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "navercafe-aside", "version": "1"},
            })
            if result.get("protocolVersion") != "2024-11-05":
                raise AsideMCPError("Aside MCP returned an unsupported protocol version")
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            for line in self._process.stdout:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError):
                    self._messages.put(AsideMCPError("Aside MCP returned invalid JSON"))
                    return
                self._messages.put(message)
        except (OSError, UnicodeError) as exc:
            self._messages.put(AsideMCPError(f"Aside MCP output failed: {type(exc).__name__}"))
        finally:
            self._messages.put(AsideMCPError("Aside MCP stdout reached EOF"))

    def _send(self, message):
        if self._closed:
            raise AsideMCPError("Aside MCP connection is closed")
        try:
            self._process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AsideMCPError("Aside MCP stdin is unavailable") from exc

    def _request(self, method, params, *, timeout=None):
        self._next_id += 1
        request_id = self._next_id
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            remaining = deadline - time.monotonic()
            try:
                message = self._messages.get(timeout=max(remaining, 0))
            except queue.Empty as exc:
                self.close()
                raise AsideMCPTimeout(f"Aside MCP {method} timed out; request was not retried") from exc
            if isinstance(message, Exception):
                self.close()
                raise message
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                self.close()
                raise AsideMCPError("Aside MCP returned an invalid JSON-RPC message")
            if "id" not in message and isinstance(message.get("method"), str):
                continue  # Server notifications do not complete this request.
            if message.get("id") != request_id:
                self.close()
                raise AsideMCPError("Aside MCP response ID does not match the request")
            if "error" in message:
                error = message["error"]
                raise AsideMCPError(f"Aside MCP protocol error: {error}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise AsideMCPError("Aside MCP response has no result object")
            return result

    def repl(self, code: str, *, title: str = "Cafe automation", timeout: float | None = None) -> str:
        result = self._request("tools/call", {
            "name": "repl", "arguments": {"title": title, "code": code},
        }, timeout=timeout)
        content = result.get("content", [])
        if not isinstance(content, list):
            raise AsideMCPError("Aside MCP tool response has invalid content")
        text = "\n".join(item["text"] for item in content
                         if isinstance(item, dict) and item.get("type") == "text"
                         and isinstance(item.get("text"), str))
        if result.get("isError"):
            raise AsideMCPError(f"Aside MCP repl tool failed: {text}")
        return text

    def close(self):
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        process.stdin.close()
        self._reader.join(timeout=1)
        process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
