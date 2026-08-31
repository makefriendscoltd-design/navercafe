import configparser
from pathlib import Path
from unittest import mock

import aside_browser
import external_publish_tracking as tracking
import telegram_publish_approval as approval


def test_embedded_upload_contains_real_bytes(tmp_path):
    image = tmp_path / "sample.png"
    image.write_bytes(b"real-image-bytes")
    payload = aside_browser._embedded_uploads(tmp_path, [image.name])
    assert payload[0]["name"] == "sample.png"
    assert payload[0]["mimeType"] == "image/png"
    assert payload[0]["base64"]


def test_telegram_review_includes_shorts_and_waits_for_matching_decision():
    cfg = configparser.RawConfigParser()
    cfg["PUBLISH_APPROVAL"] = {
        "telegram_enabled": "true",
        "telegram_token": "hidden",
        "telegram_chat_id": "123",
        "expected_bot_username": "ccida_bot",
        "expected_chat_type": "private",
    }
    client = mock.Mock()
    client.latest_update_id.return_value = 41
    client.send_text.side_effect = [1, 1, 1]
    client.send_images.return_value = 0
    client.send_approval_buttons.return_value = 1
    client.wait_for_decision.return_value = True
    with (
        mock.patch.object(approval, "TelegramApproval", return_value=client),
        mock.patch.object(approval, "track_external_event") as track,
    ):
        result = approval.request_publish_approval(
            cfg,
            source_url="https://youtu.be/example",
            cafe_title="제목",
            cafe_body="카페 본문",
            youtube_body="유튜브 본문",
            card_images=[],
            factcheck_status="ok",
            shorts_body="쇼츠 본문",
        )
    assert result is True
    client.verify_destination.assert_called_once_with(
        expected_bot="ccida_bot", expected_chat_type="private"
    )
    sent_text = "\n".join(call.args[0] for call in client.send_text.call_args_list)
    assert "사실확인: ok" in sent_text
    assert "쇼츠 본문" in sent_text
    client.send_approval_buttons.assert_called_once()
    assert client.wait_for_decision.call_args.kwargs["after_update_id"] == 41
    track.assert_called_once_with(
        "telegram",
        "https://youtu.be/example",
        campaign="youtube-content-repurpose-first-review",
        stage="sent",
        count=4,
    )


def test_disabled_telegram_blocks_before_any_send():
    cfg = configparser.RawConfigParser()
    cfg["PUBLISH_APPROVAL"] = {"telegram_enabled": "false"}
    with mock.patch.object(approval, "TelegramApproval") as client:
        try:
            approval.request_publish_approval(
                cfg,
                source_url="x",
                cafe_title="t",
                cafe_body="c",
                youtube_body="y",
                card_images=[],
            )
        except approval.TelegramApprovalError:
            pass
        else:
            raise AssertionError("disabled Telegram must block")
    client.assert_not_called()


def test_approval_credentials_can_use_separate_ccida_yaml(tmp_path):
    ccida = tmp_path / "config.yaml"
    ccida.write_text(
        "telegram:\n  bot_token: hidden-token\n  chat_id: 456\nother:\n  value: x\n",
        encoding="utf-8",
    )
    cfg = configparser.RawConfigParser()
    cfg["PUBLISH_APPROVAL"] = {"telegram_config_file": str(ccida)}
    credentials = approval.load_approval_credentials(cfg)
    assert credentials["token"] == "hidden-token"
    assert credentials["chat_id"] == "456"
    assert credentials["expected_bot"] == "ccida_bot"
    assert credentials["expected_chat_type"] == "private"


def test_external_tracking_hashes_source_and_records_only_safe_dimensions(tmp_path):
    tracker = tmp_path / "aimax-crm-track"
    tracker.write_text("stub", encoding="utf-8")
    run_result = mock.Mock(returncode=0)
    with (
        mock.patch.object(tracking, "TRACKER", tracker),
        mock.patch.object(tracking.subprocess, "run", return_value=run_result) as run,
    ):
        assert tracking.track_external_event(
            "telegram",
            "https://example.invalid/private?token=secret",
            campaign="first-review",
            stage="sent",
            count=4,
        )
    command = run.call_args.args[0]
    assert not any("example.invalid" in part or "secret" in part for part in command)
    assert command[command.index("--channel") + 1] == "telegram"
    assert command[command.index("--campaign") + 1] == "first-review"
    assert command[command.index("--stage") + 1] == "sent"
    assert command[command.index("--count") + 1] == "4"
