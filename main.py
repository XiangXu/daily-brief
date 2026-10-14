"""Daily Brief: gather each section, build the brief and deliver it."""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import holidays
import yaml
from dotenv import load_dotenv

import news
import notify
import render
import weather

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config(path=CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_day_off(day, schedule) -> bool:
    """True on weekends and public holidays in the configured country."""
    return day.weekday() >= 5 or day in holidays.country_holidays(schedule["holidays"], years=day.year)


def should_run(now, schedule) -> bool:
    """True if `now` falls in the day's send window, in the configured timezone.

    Workdays and days off have their own send time. The workflow fires every
    hour that a send time can map to in UTC, and only one lands in the window.
    """
    local = now.astimezone(ZoneInfo(schedule["timezone"]))
    kind = "day_off" if is_day_off(local.date(), schedule) else "weekday"
    hour, minute = map(int, schedule["send_times"][kind].split(":"))
    start = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return start <= local < start + timedelta(minutes=schedule["window_minutes"])


def build_brief(now, config, forecast, headlines) -> tuple[str, str]:
    """Return (title, text body). A section with no data is marked unavailable."""
    brief = render.compose(now, config, forecast, headlines)
    return brief.title, render.text(brief, config)


def run(config, now, force=False, dry_run=False, html_out=None) -> int:
    """Run once and return the process exit code."""
    if not force and not should_run(now, config["schedule"]):
        log.info("Outside the send window, nothing to do")
        return 0

    forecast = _safe("weather", weather.fetch, config)
    headlines = _safe("news", news.get_headlines, config)
    brief = render.compose(now, config, forecast, headlines)
    title, body = brief.title, render.text(brief, config)
    # The rich version is a nicety: if it fails, the plain text still goes out.
    html = _safe("html", render.html, brief, config)

    if html_out and html:
        Path(html_out).write_text(html, encoding="utf-8")
    if dry_run:
        print(f"{title}\n\n{body}")
        return 0

    results = notify.send(title, body, config, html=html)
    return 0 if results and all(results.values()) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="run now, ignoring the send window"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the brief instead of sending it"
    )
    parser.add_argument(
        "--html-out", metavar="FILE", help="also write the HTML email to FILE, to preview it"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()
    config = load_config()

    now = datetime.now(ZoneInfo(config["schedule"]["timezone"]))
    return run(
        config, now, force=args.force, dry_run=args.dry_run, html_out=args.html_out
    )


def _safe(name, func, *args):
    """Call a data source. Any failure becomes None so the run carries on."""
    try:
        return func(*args)
    except Exception:  # noqa: BLE001 - one source must never crash the run
        log.exception("%s failed", name)
        return None


if __name__ == "__main__":
    sys.exit(main())
