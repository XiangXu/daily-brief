from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import main
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


def test_run_html_out_writes_a_preview_file(config, sources, tmp_path):
    _, _, send = sources
    out = tmp_path / "brief.html"
    assert main.run(config, ON_TIME, dry_run=True, html_out=out) == 0
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    send.assert_not_called()


@pytest.mark.parametrize(
    "utc_time, expected",
    [
        # Summer (IST, UTC+1): the 06:30 UTC entry is 07:30 in Dublin.
        (datetime(2026, 7, 1, 6, 30, tzinfo=UTC), True),
        (datetime(2026, 7, 1, 7, 30, tzinfo=UTC), False),
        # Winter (GMT, UTC+0): the 07:30 UTC entry is 07:30 in Dublin.
        (datetime(2026, 12, 1, 6, 30, tzinfo=UTC), False),
        (datetime(2026, 12, 1, 7, 30, tzinfo=UTC), True),
        # GitHub Actions often starts late: still inside the window.
        (datetime(2026, 12, 1, 7, 50, tzinfo=UTC), True),
        (datetime(2026, 12, 1, 8, 24, tzinfo=UTC), True),
        (datetime(2026, 12, 1, 8, 25, tzinfo=UTC), False),
        (datetime(2026, 12, 1, 7, 29, tzinfo=UTC), False),
    ],
)
def test_should_run_only_around_send_time_in_dublin(config, utc_time, expected):
    assert main.should_run(utc_time, config["schedule"]) is expected


def test_build_brief_has_every_section(config):
    title, body = main.build_brief(ON_TIME, config, WEATHER, HEADLINES)

    assert title == "Daily Brief: Tue 13 Oct"
    assert "Dublin: 4 to 10°C, feels like 7 to 14°C, rain 2.0 mm (60%), wind 34 km/h, UV 4" in body
    assert "Steady rain: wear waterproofs\nUV 3 or more: use sunscreen" in body
    assert "Me: sweater + thermal layer + waterproof warm coat + hat + gloves + sunscreen" in body
    assert "Wife: sweater + thermal layer + waterproof down coat + hat + gloves + scarf + sunscreen" in body
    # The child is not out at noon, so no rain gear, but is out when it warms up.
    assert (
        "Child: sweater + thermal layer + warm coat + hat + gloves"
        " + Warms up later: you can take a layer off + sunscreen + sun hat"
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
    assert "Me: sweater + thermal layer" in body


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
    with patch.object(main.render.clothing, "advise", side_effect=RuntimeError("bug")):
        assert main.run(config, ON_TIME) == 0

    body = send.call_args.args[1]
    assert "## What to wear\nunavailable" in body
    assert "Dublin: 4 to 10°C, feels like" in body


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


def test_main_parses_flags_and_loads_config(sources):
    with patch.object(main, "run", return_value=0) as run:
        assert main.main(["--force", "--dry-run"]) == 0

    config, now = run.call_args.args
    assert config["schedule"]["timezone"] == "Europe/Dublin"
    assert now.tzinfo == ZoneInfo("Europe/Dublin")
    assert run.call_args.kwargs == {"force": True, "dry_run": True, "html_out": None}


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
    assert "老公：" in body and "老婆：" in body and "孩子：" in body
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
