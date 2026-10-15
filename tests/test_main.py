from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import holidays
import pytest
import yaml

import main
from conftest import ROOT
from helpers import stormy_day

UTC = ZoneInfo("UTC")
DUBLIN = ZoneInfo("Europe/Dublin")

WEATHER = stormy_day()
HEADLINES = [
    {
        "name": "IT",
        "items": [
            {"source": "InfoQ", "title": "Story one", "link": "https://x.example/1", "summary": ""},
            {"source": "AWS", "title": "Story two", "link": "", "summary": ""},
        ],
    },
    {
        "name": "中文",
        "items": [{"source": "少数派", "title": "故事三", "link": "https://x.example/3", "summary": ""}],
    },
]
ON_TIME = datetime(2026, 10, 13, 7, 30, tzinfo=DUBLIN)


@pytest.fixture
def sources():
    with (
        patch.object(main.weather, "fetch", return_value=WEATHER) as fetch,
        patch.object(main.news, "get_headlines", return_value=HEADLINES) as digest,
        patch.object(main.notify, "send", return_value={"email": True}) as send,
    ):
        yield fetch, digest, send


def test_run_sends_an_html_version_too(config, sources):
    _, _, send = sources
    assert main.run(config, ON_TIME) == 0
    html = send.call_args.kwargs["html"]
    assert html.startswith("<!DOCTYPE html>") and "Story one" in html


def test_run_falls_back_to_plain_text_if_html_fails(config, sources):
    _, _, send = sources
    with patch.object(main.render, "html", side_effect=RuntimeError("bug")):
        assert main.run(config, ON_TIME) == 0
    assert send.call_args.kwargs["html"] is None
    assert "Story one" in send.call_args.args[1]


def test_run_still_sends_if_the_brief_cannot_be_composed(config, sources, caplog):
    _, _, send = sources
    with patch.object(main.render, "compose", side_effect=RuntimeError("bug")):
        assert main.run(config, ON_TIME) == 0
    title, body = send.call_args.args[:2]
    assert title == "Daily Brief: Tue 13 Oct" and body.count("unavailable") == 3
    assert "brief failed" in caplog.text


def test_run_html_out_writes_a_preview_file(config, sources, tmp_path):
    _, _, send = sources
    out = tmp_path / "brief.html"
    assert main.run(config, ON_TIME, dry_run=True, html_out=out) == 0
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    send.assert_not_called()


@pytest.mark.parametrize(
    "utc_time, expected",
    [
        # Weekday, summer (IST, UTC+1): 07:00 in Dublin is 06:00 UTC.
        (datetime(2026, 7, 1, 6, 0, tzinfo=UTC), True),
        (datetime(2026, 7, 1, 7, 0, tzinfo=UTC), False),
        (datetime(2026, 7, 1, 8, 0, tzinfo=UTC), False),
        # Weekday, winter (GMT, UTC+0): 07:00 in Dublin is 07:00 UTC.
        (datetime(2026, 12, 1, 6, 0, tzinfo=UTC), False),
        (datetime(2026, 12, 1, 7, 0, tzinfo=UTC), True),
        (datetime(2026, 12, 1, 8, 0, tzinfo=UTC), False),
        # GitHub Actions often starts late: still inside the window.
        (datetime(2026, 12, 1, 7, 54, tzinfo=UTC), True),
        (datetime(2026, 12, 1, 7, 55, tzinfo=UTC), False),
        (datetime(2026, 12, 1, 6, 59, tzinfo=UTC), False),
        # Weekend, summer: 08:00 in Dublin is 07:00 UTC.
        (datetime(2026, 7, 4, 6, 0, tzinfo=UTC), False),
        (datetime(2026, 7, 4, 7, 0, tzinfo=UTC), True),
        (datetime(2026, 7, 5, 8, 0, tzinfo=UTC), False),
        # Weekend, winter: 08:00 in Dublin is 08:00 UTC.
        (datetime(2026, 12, 5, 7, 0, tzinfo=UTC), False),
        (datetime(2026, 12, 6, 8, 0, tzinfo=UTC), True),
        (datetime(2026, 12, 6, 8, 54, tzinfo=UTC), True),
        # Bank holidays are days off: St Patrick's Day (Tue) and the October bank holiday (Mon).
        (datetime(2026, 3, 17, 7, 0, tzinfo=UTC), False),
        (datetime(2026, 3, 17, 8, 0, tzinfo=UTC), True),
        (datetime(2026, 10, 26, 7, 0, tzinfo=UTC), False),
        (datetime(2026, 10, 26, 8, 0, tzinfo=UTC), True),
        # The clocks change on Sundays at 01:00 UTC, before any send.
        (datetime(2026, 3, 29, 7, 0, tzinfo=UTC), True),
        (datetime(2026, 3, 29, 8, 0, tzinfo=UTC), False),
        (datetime(2026, 10, 25, 7, 0, tzinfo=UTC), False),
        (datetime(2026, 10, 25, 8, 0, tzinfo=UTC), True),
    ],
)
def test_should_run_only_around_send_time_in_dublin(config, utc_time, expected):
    assert main.should_run(utc_time, config["schedule"]) is expected


def test_day_off_is_judged_by_the_dublin_date(config):
    # 23:30 UTC on Friday is 00:30 on Saturday in Dublin: a day off.
    schedule = dict(config["schedule"], send_times={"weekday": "00:15", "day_off": "00:30"})
    assert main.should_run(datetime(2026, 7, 3, 23, 30, tzinfo=UTC), schedule) is True
    assert main.should_run(datetime(2026, 7, 3, 23, 15, tzinfo=UTC), schedule) is False


@pytest.mark.parametrize(
    "day, expected",
    [
        (date(2026, 10, 14), False),  # Wednesday
        (date(2026, 10, 17), True),  # Saturday
        (date(2026, 10, 18), True),  # Sunday
        (date(2026, 2, 2), True),  # St Brigid's Day
        (date(2026, 3, 17), True),  # St Patrick's Day
        (date(2026, 4, 6), True),  # Easter Monday
        (date(2026, 10, 26), True),  # October bank holiday
        (date(2026, 12, 25), True),  # Christmas Day
        (date(2027, 3, 29), True),  # Easter Monday moves every year
    ],
)
def test_weekends_and_irish_bank_holidays_are_days_off(config, day, expected):
    assert main.is_day_off(day, config["schedule"]) is expected


def _cron_times(day):
    """The UTC times the workflow's cron entries fire on `day`."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "daily-brief.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))  # PyYAML reads the key `on` as True
    times = []
    for entry in triggers["schedule"]:
        minute, hour, *rest = entry["cron"].split()
        # Every entry fires daily: main.py picks the day's send time in Dublin.
        assert rest == ["*", "*", "*"], entry["cron"]
        times.append(datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=UTC))
    return times


CRON_LAG = timedelta(minutes=7)  # the first cron entry after a send time
RETRY_GAP = timedelta(minutes=30)  # the next entry, in case GitHub drops the first


def _utc_send_times(schedule):
    """Every UTC time a send time can fall at, in winter (UTC+0) and summer (UTC+1)."""
    times = set()
    for send in schedule["send_times"].values():
        hour, minute = map(int, send.split(":"))
        for offset in (0, 1):
            times.add(datetime(2026, 1, 1, hour - offset, minute, tzinfo=UTC))
    return times


def test_crons_fire_twice_after_each_send_time_and_off_the_hour(real_config):
    # GitHub delays or drops many runs scheduled at :00, and sometimes drops a
    # run outright, so each send time has a second entry half an hour later.
    schedule = real_config["schedule"]
    crons = set(_cron_times(date(2026, 1, 1)))
    assert all(t.minute != 0 for t in crons)
    for send in _utc_send_times(schedule):
        assert {send + CRON_LAG, send + CRON_LAG + RETRY_GAP} <= crons, send
    assert CRON_LAG + RETRY_GAP < timedelta(minutes=schedule["window_minutes"])


def _sends(day, schedule, delay=timedelta(0), dropped=()):
    """The Dublin times the workflow sends on `day`.

    Runs start `delay` late, `dropped` runs never start, and after a send the
    workflow's "sent today" cache entry makes the later runs skip.
    """
    sent = []
    for cron in sorted(_cron_times(day)):
        start = cron + delay
        if cron not in dropped and not sent and main.should_run(start, schedule):
            sent.append(start.astimezone(DUBLIN))
    return sent


def _on_time(day, schedule):
    days_off = holidays.Ireland(years=day.year)
    kind = "day_off" if day.weekday() >= 5 or day in days_off else "weekday"
    hour, minute = map(int, schedule["send_times"][kind].split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=DUBLIN)


def _every_day():
    day = date(2026, 1, 1)
    while day.year < 2028:
        yield day
        day += timedelta(days=1)


@pytest.mark.parametrize("delay_minutes", [0, 30, 75])
def test_workflow_sends_exactly_once_a_day_at_the_dublin_send_time(real_config, delay_minutes):
    schedule = real_config["schedule"]
    delay = timedelta(minutes=delay_minutes)
    for day in _every_day():
        sent = _sends(day, schedule, delay)
        on_time = _on_time(day, schedule)
        # An earlier entry, started late, may land in the window first: fine.
        assert len(sent) == 1 and on_time <= sent[0] <= on_time + CRON_LAG + delay, (day, sent)


def test_workflow_still_sends_once_if_github_drops_the_first_run(real_config):
    schedule = real_config["schedule"]
    for day in _every_day():
        first = _on_time(day, schedule) + CRON_LAG
        assert _sends(day, schedule, dropped={first}) == [first + RETRY_GAP], day


def test_a_run_hours_late_sends_nothing(real_config):
    # 2026-10-14: GitHub started the morning's run at 13:36 UTC.
    assert main.should_run(datetime(2026, 10, 14, 13, 36, tzinfo=UTC), real_config["schedule"]) is False


def test_real_send_times_are_half_six_on_weekdays_and_eight_on_days_off(real_config):
    schedule = real_config["schedule"]
    assert schedule["send_times"] == {"weekday": "06:30", "day_off": "08:00"}
    assert schedule["holidays"] == "IE"


def test_build_brief_has_every_section(config):
    title, body = main.build_brief(ON_TIME, config, WEATHER, HEADLINES)

    assert title == "Daily Brief: Tue 13 Oct"
    assert "Dublin: daytime (7–19) 4 to 10°C, feels like 7 to 14°C, rain 2.0 mm (60%), wind 34 km/h (gusts 34), UV 4" in body
    assert body.startswith("Steady rain: wear waterproofs\nUV 3 or more: use sunscreen\n\n")
    assert "Me (out 7–10): sweater + thermal layer + waterproof warm coat + hat + gloves + sunscreen" in body
    assert "Wife (out 7–10): sweater + thermal layer + waterproof down coat + hat + gloves + scarf + sunscreen" in body
    # The child is not out at noon, so no rain gear, but is out when it warms up.
    assert (
        "Child (out 8–9): sweater + thermal layer + warm coat + hat + gloves + sunscreen + sun hat"
        " 💡 Warms up later: you can take a layer off"
    ) in body
    assert "### IT\n- Story one (InfoQ)\n  https://x.example/1\n- Story two (AWS)" in body
    assert "### 中文\n- 故事三 (少数派)\n  https://x.example/3" in body
    assert "unavailable" not in body


def test_build_brief_marks_missing_weather_and_clothing_unavailable(config):
    _, body = main.build_brief(ON_TIME, config, None, HEADLINES)

    assert "## Weather\nunavailable" in body
    assert "## What to wear\nunavailable" in body
    assert "- Story one (InfoQ)" in body


def test_build_brief_marks_missing_news_unavailable(config):
    _, body = main.build_brief(ON_TIME, config, WEATHER, None)

    assert "## News\nunavailable" in body
    assert "Me (out 7–10): sweater + thermal layer" in body


def test_run_sends_the_brief(config, sources):
    _, _, send = sources

    assert main.run(config, ON_TIME) == 0
    title, body, cfg = send.call_args.args
    assert title == "Daily Brief: Tue 13 Oct"
    assert "Story one" in body
    assert cfg is config


def test_run_exits_early_outside_the_window(config, sources):
    fetch, digest, send = sources
    late = datetime(2026, 10, 13, 8, 30, tzinfo=DUBLIN)

    assert main.run(config, late) == 0
    fetch.assert_not_called()
    digest.assert_not_called()
    send.assert_not_called()


def test_run_force_ignores_the_window(config, sources):
    _, _, send = sources
    late = datetime(2026, 10, 13, 15, 0, tzinfo=DUBLIN)

    assert main.run(config, late, force=True) == 0
    send.assert_called_once()


def test_run_survives_every_source_failing(config, sources):
    fetch, digest, send = sources
    fetch.side_effect = RuntimeError("weather bug")
    digest.side_effect = RuntimeError("news bug")

    assert main.run(config, ON_TIME) == 0
    body = send.call_args.args[1]
    assert body.count("unavailable") == 3


def test_run_survives_a_clothing_failure(config, sources):
    _, _, send = sources
    with patch.object(main.render.clothing, "outfit", side_effect=RuntimeError("bug")):
        assert main.run(config, ON_TIME) == 0

    body = send.call_args.args[1]
    assert "## What to wear\nunavailable" in body
    assert "Dublin: daytime (7–19) 4 to 10°C, feels like" in body


def test_run_reports_delivery_failure_in_exit_code(config, sources):
    _, _, send = sources
    send.return_value = {"email": True, "wecom": False}

    assert main.run(config, ON_TIME) == 1


def test_run_dry_run_prints_instead_of_sending(config, sources, capsys):
    _, _, send = sources

    assert main.run(config, ON_TIME, dry_run=True) == 0
    send.assert_not_called()
    out = capsys.readouterr().out
    assert "Daily Brief: Tue 13 Oct" in out and "Story one" in out


def test_run_sent_file_records_the_dublin_date_after_a_send(config, sources, tmp_path):
    sent = tmp_path / "sent"
    # 23:30 UTC on the 12th is already the 13th in Dublin (summer time).
    assert main.run(config, datetime(2026, 10, 12, 23, 30, tzinfo=UTC), force=True, sent_file=sent) == 0

    assert sent.read_text(encoding="utf-8") == "2026-10-13"


def test_run_sent_file_is_not_written_outside_the_window(config, sources, tmp_path):
    sent = tmp_path / "sent"
    late = datetime(2026, 10, 13, 8, 30, tzinfo=DUBLIN)

    assert main.run(config, late, sent_file=sent) == 0
    assert not sent.exists()


def test_run_sent_file_is_not_written_when_delivery_fails(config, sources, tmp_path):
    # No marker, so the next run that morning tries again.
    _, _, send = sources
    send.return_value = {"email": False}
    sent = tmp_path / "sent"

    assert main.run(config, ON_TIME, sent_file=sent) == 1
    assert not sent.exists()


def test_run_sent_file_is_not_written_on_a_dry_run(config, sources, tmp_path):
    sent = tmp_path / "sent"

    assert main.run(config, ON_TIME, dry_run=True, sent_file=sent) == 0
    assert not sent.exists()


def test_main_parses_flags_and_loads_config(sources):
    with patch.object(main, "run", return_value=0) as run:
        assert main.main(["--force", "--dry-run", "--sent-file", "out.txt"]) == 0

    config, now = run.call_args.args
    assert config["schedule"]["timezone"] == "Europe/Dublin"
    assert now.tzinfo == ZoneInfo("Europe/Dublin")
    assert run.call_args.kwargs == {
        "force": True,
        "dry_run": True,
        "html_out": None,
        "sent_file": "out.txt",
    }


def test_build_brief_uses_labels_from_config(config):
    config["brief"]["headings"]["news"] = "Headlines"
    config["brief"]["unavailable"] = "n/a"
    config["brief"]["person_line"] = "{name} -> {items}"

    _, body = main.build_brief(ON_TIME, config, None, None)

    assert "## Headlines\nn/a" in body
    _, body = main.build_brief(ON_TIME, config, WEATHER, None)
    assert "Me -> sweater + thermal layer" in body


def test_build_brief_title_does_not_depend_on_the_system_locale(config):
    sunday = datetime(2026, 1, 4, 7, 30, tzinfo=DUBLIN)
    title, _ = main.build_brief(sunday, config, WEATHER, HEADLINES)
    assert title == "Daily Brief: Sun 4 Jan"


def test_real_config_builds_a_chinese_brief(real_config):
    title, body = main.build_brief(ON_TIME, real_config, WEATHER, HEADLINES)

    assert title == "每日简报：10月13日 周二"
    for heading in ("## 🌤️ 天气", "## 👕 穿衣建议", "## 📰 新闻"):
        assert heading in body
    assert "气温 4~10°C，体感 7~14°C，降雨 2.0 毫米（概率 60%）" in body
    assert "🌧️ 有持续降雨，穿防水外套" in body and "防水保暖大衣" in body
    assert "老公（7–10 点出门）：" in body and "老婆（7–10 点出门）：" in body and "孩子（8–9 点出门）：" in body
    assert "阵风" in body
    assert "unavailable" not in body and "{" not in body and "}" not in body
    assert "- Story one (InfoQ)" in body  # headlines stay as published


def test_real_config_marks_missing_sections_in_chinese(real_config):
    _, body = main.build_brief(ON_TIME, real_config, None, None)
    assert body.count("暂无") == 3


def test_clothing_is_unavailable_if_the_forecast_has_no_hours(config):
    from weather import Weather

    _, body = main.build_brief(ON_TIME, config, Weather(7.2, 60, 33.5, 4.2), HEADLINES)
    assert "## What to wear\nunavailable" in body
    assert "Dublin: " in body  # the weather line still shows


def test_load_config_reads_the_given_file(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("schedule: {timezone: 名字}\n", encoding="utf-8")
    assert main.load_config(path) == {"schedule": {"timezone": "名字"}}
    assert main.load_config()["schedule"]["timezone"] == "Europe/Dublin"
