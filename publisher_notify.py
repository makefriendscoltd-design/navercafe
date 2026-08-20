# -*- coding: utf-8 -*-
"""Telegram notification for a verified Naver Cafe publication."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from publisher_contract import notification_message


class PublishNotificationError(RuntimeError):
    pass


def load_notification_config(config):
    section = "PUBLISH_NOTIFY"
    token = os.environ.get("PUBLISH_NOTIFY_TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("PUBLISH_NOTIFY_TELEGRAM_CHAT_ID", "").strip()
    thread_id = os.environ.get("PUBLISH_NOTIFY_TELEGRAM_THREAD_ID", "").strip()
    if not token:
        token = config.get(section, "telegram_token", fallback="").strip()
    if not chat_id:
        chat_id = config.get(section, "telegram_chat_id", fallback="").strip()
    if not thread_id:
        thread_id = config.get(section, "telegram_thread_id", fallback="").strip()
    return {
        "enabled": config.getboolean(section, "enabled", fallback=False),
        "token": token,
        "chat_id": chat_id,
        "thread_id": thread_id,
    }


def validate_notification_config(config):
    settings = load_notification_config(config)
    if not settings["enabled"]:
        raise PublishNotificationError("config.ini의 [PUBLISH_NOTIFY] enabled가 false입니다.")
    if not settings["token"] or not settings["chat_id"]:
        raise PublishNotificationError(
            "[PUBLISH_NOTIFY] telegram_token/telegram_chat_id 설정이 필요합니다."
        )
    return settings


def send_verified_article(config, title, article_url, timeout=20):
    settings = validate_notification_config(config)

    payload = {
        "chat_id": settings["chat_id"],
        "text": notification_message(title, article_url),
        "disable_web_page_preview": False,
    }
    if settings["thread_id"]:
        payload["message_thread_id"] = settings["thread_id"]

    endpoint = f"https://api.telegram.org/bot{settings['token']}/sendMessage"
    request = urllib.request.Request(
        endpoint,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise PublishNotificationError(f"텔레그램 전송 실패: {e}") from e

    if not parsed.get("ok"):
        description = parsed.get("description") or "Telegram API가 실패를 반환했습니다."
        raise PublishNotificationError(f"텔레그램 전송 실패: {description}")
    message_id = (parsed.get("result") or {}).get("message_id")
    return {
        "ok": True,
        "status": "sent",
        "messageId": message_id,
    }
