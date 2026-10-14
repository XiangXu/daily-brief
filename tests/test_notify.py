import smtplib
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

import notify

WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret-key"


@pytest.fixture(autouse=True)
def no_sleep():
    with patch.object(notify.time, "sleep") as sleep:
        yield sleep


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setenv("EMAIL_RECIPIENTS", "me@example.com, wife@example.com")


@pytest.fixture
def cfg(config):
    config["notify"]["channels"] = ["email"]
    return config


@pytest.fixture
def smtp():
    with patch.object(notify.smtplib, "SMTP_SSL") as cls:
        server = MagicMock()
        cls.return_value.__enter__.return_value = server
        yield cls, server


def wecom_response(errcode=0, status=200):
    resp = Mock(status_code=status)
    resp.json.return_value = {"errcode": errcode, "errmsg": "ok" if not errcode else "bad"}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} for {WEBHOOK}")
    return resp


def test_email_is_sent_to_all_recipients(cfg, smtp):
    cls, server = smtp
    assert notify.send("Title", "Body", cfg) == {"email": True}

    cls.assert_called_once_with(
        "smtp.gmail.com", 465, timeout=cfg["notify"]["timeout_seconds"]
    )
    server.login.assert_called_once_with("me@gmail.com", "app-password")
    message = server.send_message.call_args.args[0]
    assert message["Subject"] == "Title"
    assert message["From"] == "me@gmail.com"
    assert message["To"] == "me@example.com, wife@example.com"
    assert message.get_content().strip() == "Body"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("me@example.com", "me@example.com"),
        (" me@example.com ,wife@example.com, ", "me@example.com, wife@example.com"),
    ],
)
def test_email_recipients_come_from_the_environment(cfg, smtp, monkeypatch, value, expected):
    _, server = smtp
    monkeypatch.setenv("EMAIL_RECIPIENTS", value)
    notify.send("Title", "Body", cfg)
    assert server.send_message.call_args.args[0]["To"] == expected


@pytest.mark.parametrize("value", ["", " , "])
def test_blank_email_recipients_fail_without_retrying(cfg, smtp, monkeypatch, no_sleep, caplog, value):
    _, server = smtp
    monkeypatch.setenv("EMAIL_RECIPIENTS", value)

    assert notify.send("Title", "Body", cfg) == {"email": False}
    server.send_message.assert_not_called()
    no_sleep.assert_not_called()
    assert "EMAIL_RECIPIENTS" in caplog.text


def test_email_with_html_is_multipart_alternative(cfg, smtp):
    _, server = smtp
    assert notify.send("Title", "Plain body", cfg, html="<p>Rich body</p>") == {"email": True}

    message = server.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/alternative"
    plain, rich = message.get_payload()
    assert plain.get_content_type() == "text/plain"
    assert plain.get_content().strip() == "Plain body"
    assert rich.get_content_type() == "text/html"
    assert rich.get_content().strip() == "<p>Rich body</p>"


def test_email_without_html_stays_plain_text(cfg, smtp):
    _, server = smtp
    notify.send("Title", "Body", cfg)
    assert server.send_message.call_args.args[0].get_content_type() == "text/plain"


def test_wecom_ignores_html_and_posts_markdown(cfg):
    cfg["notify"]["channels"] = ["wecom"]
    with patch.object(notify.requests, "post", return_value=wecom_response()) as post:
        notify.send("Title", "Body", cfg, html="<p>Rich</p>")
    assert post.call_args.kwargs["json"]["markdown"]["content"] == "# Title\n\nBody"


def test_wecom_posts_markdown(cfg):
    cfg["notify"]["channels"] = ["wecom"]
    with patch.object(notify.requests, "post", return_value=wecom_response()) as post:
        assert notify.send("Title", "Body", cfg) == {"wecom": True}

    post.assert_called_once()
    assert post.call_args.args[0] == WEBHOOK
    assert post.call_args.kwargs["json"] == {
        "msgtype": "markdown",
        "markdown": {"content": "# Title\n\nBody"},
    }


def test_wecom_truncates_to_the_api_byte_limit(cfg):
    cfg["notify"]["channels"] = ["wecom"]
    with patch.object(notify.requests, "post", return_value=wecom_response()) as post:
        notify.send("Title", "新闻" * 3000, cfg)

    content = post.call_args.kwargs["json"]["markdown"]["content"]
    assert len(content.encode("utf-8")) <= notify.WECOM_MAX_BYTES
    assert content.endswith("…")


def test_all_listed_channels_are_sent_independently(cfg, smtp):
    cfg["notify"]["channels"] = ["email", "wecom"]
    _, server = smtp
    server.send_message.side_effect = smtplib.SMTPException("down")
    with patch.object(notify.requests, "post", return_value=wecom_response()):
        assert notify.send("Title", "Body", cfg) == {"email": False, "wecom": True}


def test_retries_twice_then_succeeds(cfg, smtp, no_sleep):
    _, server = smtp
    server.send_message.side_effect = [smtplib.SMTPException("1"), OSError("2"), None]

    assert notify.send("Title", "Body", cfg) == {"email": True}
    assert server.send_message.call_count == 3
    assert no_sleep.call_count == 2
    no_sleep.assert_called_with(cfg["notify"]["retry_delay_seconds"])


def test_gives_up_after_two_retries_and_logs(cfg, smtp, caplog):
    _, server = smtp
    server.send_message.side_effect = smtplib.SMTPException("down")

    assert notify.send("Title", "Body", cfg) == {"email": False}
    assert server.send_message.call_count == 3
    assert "email" in caplog.text and "failed" in caplog.text


@pytest.mark.parametrize(
    "outcome",
    [
        wecom_response(errcode=93000),
        wecom_response(status=500),
        requests.ConnectionError(f"cannot reach {WEBHOOK}"),
    ],
)
def test_wecom_failures_are_retried_and_never_log_the_webhook(cfg, outcome, caplog):
    cfg["notify"]["channels"] = ["wecom"]
    kwargs = (
        {"side_effect": outcome}
        if isinstance(outcome, Exception)
        else {"return_value": outcome}
    )
    with patch.object(notify.requests, "post", **kwargs) as post:
        assert notify.send("Title", "Body", cfg) == {"wecom": False}

    assert post.call_count == 3
    assert "secret-key" not in caplog.text


@pytest.mark.parametrize(
    "channel, variable",
    [
        ("email", "GMAIL_USER"),
        ("email", "GMAIL_APP_PASSWORD"),
        ("email", "EMAIL_RECIPIENTS"),
        ("wecom", "WECOM_WEBHOOK_URL"),
    ],
)
def test_missing_credentials_fail_without_retrying(
    cfg, monkeypatch, no_sleep, caplog, channel, variable
):
    cfg["notify"]["channels"] = [channel]
    monkeypatch.delenv(variable)

    assert notify.send("Title", "Body", cfg) == {channel: False}
    no_sleep.assert_not_called()
    assert variable in caplog.text


def test_unknown_channel_is_reported_and_others_still_send(cfg, smtp, caplog):
    cfg["notify"]["channels"] = ["pigeon", "email"]

    assert notify.send("Title", "Body", cfg) == {"pigeon": False, "email": True}
    assert "pigeon" in caplog.text


def test_new_channels_can_be_registered(cfg):
    sent = []
    with patch.dict(notify.CHANNELS):

        @notify.channel("test")
        def _send_test(title, body, settings, html):
            sent.append((title, body, html))

        cfg["notify"]["channels"] = ["test"]
        assert notify.send("Title", "Body", cfg, html="<p>x</p>") == {"test": True}
    assert sent == [("Title", "Body", "<p>x</p>")]
