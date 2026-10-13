"""Deliver the brief to every channel listed in config.yaml.

To add a channel, write a function `(title, body, settings, html)` that raises on
failure, decorate it with `@channel("name")`, and list the name under
`notify.channels` in config.yaml.
"""

import logging
import os
import smtplib
import time
from email.message import EmailMessage

import requests

log = logging.getLogger(__name__)

# WeCom rejects markdown messages longer than this many UTF-8 bytes.
WECOM_MAX_BYTES = 4096

CHANNELS = {}


class DeliveryError(Exception):
    """A delivery attempt failed. The message must be safe to log."""


class ChannelConfigError(Exception):
    """The channel is misconfigured, so retrying cannot help."""


def channel(name):
    def register(func):
        CHANNELS[name] = func
        return func

    return register


def send(title, body, config, html=None) -> dict[str, bool]:
    """Send the brief to each configured channel. Never raises.

    `body` is the text version. `html` is an optional rich version, used by
    channels that can show it. Returns whether delivery succeeded, per channel.
    """
    settings = config["notify"]
    return {
        name: _deliver(name, title, body, html, settings)
        for name in settings["channels"]
    }


def _deliver(name, title, body, html, settings) -> bool:
    handler = CHANNELS.get(name)
    if handler is None:
        log.error("Unknown notify channel %r (available: %s)", name, ", ".join(CHANNELS))
        return False

    attempts = 1 + settings["retries"]
    for attempt in range(1, attempts + 1):
        try:
            handler(title, body, settings, html)
            log.info("Brief delivered via %s", name)
            return True
        except ChannelConfigError as exc:
            log.error("Delivery via %s failed: %s", name, exc)
            return False
        except DeliveryError as exc:
            reason = str(exc)
        except Exception as exc:  # noqa: BLE001 - delivery must never crash the run
            reason = type(exc).__name__
        if attempt < attempts:
            log.warning("Delivery via %s failed (%s), retrying", name, reason)
            time.sleep(settings["retry_delay_seconds"])
    log.error("Delivery via %s failed after %d attempts: %s", name, attempts, reason)
    return False


def _env(variable) -> str:
    value = os.environ.get(variable)
    if not value:
        raise ChannelConfigError(f"{variable} is not set")
    return value


@channel("email")
def _send_email(title, body, settings, html):
    user = _env("GMAIL_USER")
    password = _env("GMAIL_APP_PASSWORD")
    email = settings["email"]

    message = EmailMessage()
    message["Subject"] = title
    message["From"] = user
    message["To"] = ", ".join(email["recipients"])
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL(
        email["smtp_host"], email["smtp_port"], timeout=settings["timeout_seconds"]
    ) as server:
        server.login(user, password)
        server.send_message(message)


@channel("wecom")
def _send_wecom(title, body, settings, html):
    url = _env("WECOM_WEBHOOK_URL")
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": _truncate(f"# {title}\n\n{body}", WECOM_MAX_BYTES)},
    }
    # requests errors include the webhook URL, which is a secret: never log them.
    try:
        resp = requests.post(url, json=payload, timeout=settings["timeout_seconds"])
        resp.raise_for_status()
        result = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise DeliveryError(type(exc).__name__) from None
    if result.get("errcode") != 0:
        raise DeliveryError(f"WeCom errcode {result.get('errcode')}")


def _truncate(text, max_bytes) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    log.warning("Message truncated to %d bytes", max_bytes)
    ellipsis = "…"
    cut = encoded[: max_bytes - len(ellipsis.encode("utf-8"))]
    return cut.decode("utf-8", errors="ignore") + ellipsis
