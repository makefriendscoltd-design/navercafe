"""Telegram approval gate for the first Cafe + YouTube publication.

This module only talks to Telegram when ``request_publish_approval`` is called.
Credentials are received from the already-loaded config and are never logged.
"""

from __future__ import annotations

import re
import secrets
import time
from contextlib import ExitStack
from pathlib import Path

import requests

from external_publish_tracking import track_external_event


class TelegramApprovalError(RuntimeError):
    pass


class TelegramPollingConflict(TelegramApprovalError):
    pass


DEFAULT_APPROVAL_CONFIG = Path.home() / "orca" / "projects" / "ccidainsta" / "config.yaml"
DEFAULT_APPROVAL_BOT = "ccida_bot"
DEFAULT_APPROVAL_CHAT_TYPE = "private"


def _read_telegram_yaml(path: Path) -> tuple[str, str]:
    """Read only telegram.bot_token/chat_id from the existing ccida config."""
    if not path.is_file():
        raise TelegramApprovalError(f"ccida 텔레그램 설정 파일이 없습니다: {path}")
    values: dict[str, str] = {}
    in_telegram = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^telegram\s*:\s*$", raw_line):
            in_telegram = True
            continue
        if in_telegram and raw_line and not raw_line[0].isspace():
            break
        if not in_telegram:
            continue
        match = re.match(
            r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$", raw_line
        )
        if match and match.group(1) in {"bot_token", "chat_id"}:
            values[match.group(1)] = match.group(2).strip().strip("\"'")
    return values.get("bot_token", ""), values.get("chat_id", "")


def load_approval_credentials(config) -> dict[str, str | bool]:
    """Load publishing approval separately from comment-bot reports."""
    section = "PUBLISH_APPROVAL"
    enabled = config.getboolean(section, "telegram_enabled", fallback=True)
    config_file = Path(
        config.get(section, "telegram_config_file", fallback=str(DEFAULT_APPROVAL_CONFIG))
    ).expanduser()
    token = config.get(section, "telegram_token", fallback="").strip()
    chat_id = config.get(section, "telegram_chat_id", fallback="").strip()
    if not token or not chat_id:
        file_token, file_chat_id = _read_telegram_yaml(config_file)
        token = token or file_token
        chat_id = chat_id or file_chat_id
    if not enabled:
        raise TelegramApprovalError("[PUBLISH_APPROVAL] 텔레그램 승인이 비활성화되어 있습니다.")
    if not token or not chat_id:
        raise TelegramApprovalError("ccida 승인용 token/chat_id 설정이 없습니다.")
    return {
        "enabled": enabled,
        "token": token,
        "chat_id": chat_id,
        "expected_bot": config.get(
            section, "expected_bot_username", fallback=DEFAULT_APPROVAL_BOT
        ).strip().lstrip("@"),
        "expected_chat_type": config.get(
            section, "expected_chat_type", fallback=DEFAULT_APPROVAL_CHAT_TYPE
        ).strip(),
    }


def _chunks(text: str, limit: int = 3500) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    out = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        out.append(text[:split_at].strip())
        text = text[split_at:].strip()
    if text:
        out.append(text)
    return out


class TelegramApproval:
    def __init__(self, token: str, chat_id: str, *, session=None):
        if not token or not chat_id:
            raise TelegramApprovalError("텔레그램 승인용 token/chat_id 설정이 없습니다.")
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.session = session or requests.Session()

    def _call(self, method: str, **kwargs):
        try:
            response = self.session.post(
                f"{self.base_url}/{method}", timeout=kwargs.pop("timeout", 45), **kwargs
            )
            payload = response.json()
            if response.status_code == 409 and method == "getUpdates":
                raise TelegramPollingConflict("텔레그램 승인 폴링이 다른 요청과 겹쳤습니다.")
            response.raise_for_status()
        except TelegramPollingConflict:
            raise
        except Exception:
            # Never chain requests.HTTPError: its URL contains the bot token.
            raise TelegramApprovalError(f"텔레그램 {method} 요청에 실패했습니다.") from None
        if not payload.get("ok"):
            description = payload.get("description") or "응답 ok=false"
            raise TelegramApprovalError(f"텔레그램 {method} 실패: {description}")
        return payload.get("result")

    def latest_update_id(self) -> int:
        result = self._call("getUpdates", data={"offset": -1, "limit": 1, "timeout": 0}) or []
        return max((int(item.get("update_id", 0)) for item in result), default=0)

    def verify_destination(self, *, expected_bot: str, expected_chat_type: str) -> dict[str, str]:
        """Block the send unless both the bot account and chat type are expected."""
        bot = self._call("getMe") or {}
        chat = self._call("getChat", data={"chat_id": self.chat_id}) or {}
        actual_bot = str(bot.get("username") or "").lstrip("@")
        actual_chat_type = str(chat.get("type") or "")
        if expected_bot and actual_bot.casefold() != expected_bot.lstrip("@").casefold():
            raise TelegramApprovalError("승인용 텔레그램 봇이 ccida 봇과 다릅니다.")
        if expected_chat_type and actual_chat_type != expected_chat_type:
            raise TelegramApprovalError("승인용 텔레그램 대상이 개인 채팅이 아닙니다.")
        return {"bot": actual_bot, "chat_type": actual_chat_type}

    def send_text(self, text: str) -> int:
        chunks = _chunks(text)
        for chunk in chunks:
            self._call("sendMessage", data={"chat_id": self.chat_id, "text": chunk})
        return len(chunks)

    def send_images(self, image_paths: list[str]) -> int:
        paths = [Path(path).expanduser().resolve() for path in image_paths[:10]]
        if not paths:
            return 0
        import json

        media = []
        with ExitStack() as stack:
            files = {}
            for index, path in enumerate(paths):
                if not path.is_file():
                    raise TelegramApprovalError(f"승인용 이미지가 없습니다: {path.name}")
                key = f"media{index}"
                media.append({"type": "photo", "media": f"attach://{key}"})
                files[key] = (path.name, stack.enter_context(path.open("rb")), "image/png")
            self._call(
                "sendMediaGroup",
                data={"chat_id": self.chat_id, "media": json.dumps(media, ensure_ascii=False)},
                files=files,
                timeout=90,
            )
        return len(paths)

    def send_approval_buttons(self, nonce: str) -> int:
        import json

        keyboard = {
            "inline_keyboard": [[
                {"text": "승인 후 발행", "callback_data": f"publish:approve:{nonce}"},
                {"text": "보류", "callback_data": f"publish:reject:{nonce}"},
            ]]
        }
        self._call(
            "sendMessage",
            data={
                "chat_id": self.chat_id,
                "text": "카페와 YouTube 게시물을 확인해 주세요. 승인하면 두 플랫폼에 발행합니다.",
                "reply_markup": json.dumps(keyboard, ensure_ascii=False),
            },
        )
        return 1

    def wait_for_decision(self, nonce: str, *, after_update_id: int, timeout_seconds: int = 86400) -> bool:
        offset = after_update_id + 1
        deadline = time.monotonic() + timeout_seconds
        wanted = {
            f"publish:approve:{nonce}": True,
            f"publish:reject:{nonce}": False,
        }
        while time.monotonic() < deadline:
            poll_seconds = max(1, min(30, int(deadline - time.monotonic())))
            try:
                result = self._call(
                    "getUpdates",
                    data={"offset": offset, "timeout": poll_seconds, "allowed_updates": '["callback_query"]'},
                    timeout=poll_seconds + 10,
                ) or []
            except TelegramPollingConflict:
                time.sleep(2)
                continue
            for update in result:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                callback = update.get("callback_query") or {}
                message = callback.get("message") or {}
                chat = message.get("chat") or {}
                decision = wanted.get(callback.get("data"))
                if str(chat.get("id")) != self.chat_id or decision is None:
                    continue
                self._call(
                    "answerCallbackQuery",
                    data={
                        "callback_query_id": callback.get("id", ""),
                        "text": "발행을 시작합니다." if decision else "발행을 보류했습니다.",
                    },
                )
                return decision
        raise TelegramApprovalError("텔레그램 승인 대기 시간이 만료되었습니다.")


def request_publish_approval(
    config,
    *,
    source_url: str,
    cafe_title: str,
    cafe_body: str,
    youtube_body: str,
    card_images: list[str],
    factcheck_status: str = "",
    shorts_body: str = "",
    timeout_seconds: int = 86400,
) -> bool:
    credentials = load_approval_credentials(config)
    client = TelegramApproval(str(credentials["token"]), str(credentials["chat_id"]))
    client.verify_destination(
        expected_bot=str(credentials["expected_bot"]),
        expected_chat_type=str(credentials["expected_chat_type"]),
    )
    after_update_id = client.latest_update_id()
    sent_count = client.send_text(
        "[첫 발행 승인 요청]\n"
        f"원본 영상: {source_url}\n\n"
        f"사실확인: {factcheck_status or '확인 필요'}\n\n"
        f"[네이버 카페 제목]\n{cafe_title}\n\n"
        f"[네이버 카페 본문]\n{cafe_body}"
    )
    sent_count += client.send_images(card_images)
    sent_count += client.send_text(f"[YouTube 커뮤니티 본문]\n{youtube_body}")
    if shorts_body.strip():
        sent_count += client.send_text(f"[쇼츠 나레이션 — 5번째까지 + 고정 CTA]\n{shorts_body}")
    nonce = secrets.token_urlsafe(8)
    sent_count += client.send_approval_buttons(nonce)
    track_external_event(
        "telegram",
        source_url,
        campaign="youtube-content-repurpose-first-review",
        stage="sent",
        count=sent_count,
    )
    return client.wait_for_decision(
        nonce, after_update_id=after_update_id, timeout_seconds=timeout_seconds
    )
