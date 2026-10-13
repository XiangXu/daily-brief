from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import render
from helpers import calm_day, make_day, stormy_day
from weather import Weather

NOW = datetime(2026, 10, 13, 7, 30, tzinfo=ZoneInfo("Europe/Dublin"))
WEATHER = stormy_day()
CALM = calm_day()
HEADLINES = [
    {
        "name": "IT and AI",
        "items": [
            {"source": "InfoQ", "title": "Java 27 released", "link": "https://x.example/a?b=1&c=2", "summary": ""},
            {"source": "AWS", "title": "No link here", "link": "", "summary": ""},
        ],
    },
    {
        "name": "中文科技",
        "items": [{"source": "少数派", "title": "小米手表发布", "link": "https://sspai.com/p/1", "summary": ""}],
    },
]


def grouped(items):
    return [{"name": "Group", "items": items}]


def html_for(config, forecast=WEATHER, headlines=HEADLINES):
    return render.build_html(NOW, config, forecast, headlines)


def test_title_uses_config_names_not_the_locale(config):
    assert render.title(NOW, config) == "Daily Brief: Tue 13 Oct"


def test_weather_values_are_rounded_strings(config):
    values = render.weather_values(WEATHER, config["brief"])
    assert values == {
        "temp": "4 to 10°C",
        "feels": "7 to 14°C",
        "rain": "60",
        "rain_mm": "2.0",
        "gust": "34",
        "wind": "34",
        "uv": "4",
    }


def test_weather_values_with_no_daily_high(config):
    forecast = Weather(7.2, 0, 0, 0, temperature=5.0)
    values = render.weather_values(forecast, config["brief"])
    assert values["feels"] == "7°C" and values["temp"] == "5°C"


def test_weather_values_collapse_a_range_that_rounds_to_one_number(config):
    forecast = Weather(7.2, 0, 0, 0, apparent_temperature_max=7.4, temperature=5.0, temperature_max=5.4)
    values = render.weather_values(forecast, config["brief"])
    assert values["feels"] == "7°C" and values["temp"] == "5°C"


def test_weather_values_show_a_dash_when_actual_temperature_is_missing(config):
    forecast = Weather(7.2, 0, 0, 0)
    assert render.weather_values(forecast, config["brief"])["temp"] == "–"


def test_html_is_a_complete_document_with_title_and_lang(config):
    page = html_for(config)
    assert page.startswith("<!DOCTYPE html>")
    assert 'lang="en"' in page
    assert "<title>Daily Brief: Tue 13 Oct</title>" in page
    assert "Daily Brief: Tue 13 Oct" in page.split("</head>")[1]


def test_html_shows_every_section(config):
    page = html_for(config)
    for heading in ("Weather", "What to wear", "News"):
        assert heading in page
    for text in ("4 to 10°C", "Temperature", "7 to 14°C", "Feels like", "2.0 mm", "Rain 60%", "34 km/h", "Dublin"):
        assert text in page
    for name in ("Me", "Wife", "Child"):
        assert f">{name}<" in page
    assert ">sweater + thermal layer<" in page
    assert "👨" in page and "🧒" in page


def test_html_shows_each_clothing_item_as_its_own_pill(config):
    page = html_for(config)
    for pill in ("waterproof warm coat", "hat + gloves", "sunscreen + sun hat", "waterproof down coat"):
        assert f">{pill}<" in page
    assert ">Warms up later: you can take a layer off<" in page


def test_html_headlines_are_links_with_escaped_urls(config):
    page = html_for(config)
    assert 'href="https://x.example/a?b=1&amp;c=2"' in page
    assert ">Java 27 released</a>" in page
    assert ">InfoQ<" in page
    assert "No link here" in page and 'href=""' not in page


@pytest.mark.parametrize("link", ["javascript:alert(1)", "data:text/html,x", "ftp://x", "//x.example"])
def test_html_never_links_unsafe_schemes(config, link):
    headlines = grouped([{"source": "S", "title": "Evil", "link": link, "summary": ""}])
    page = html_for(config, headlines=headlines)
    assert "Evil" in page and "href=" not in page.split("News")[-1]


def test_html_escapes_untrusted_text(config):
    headlines = grouped([{"source": "<b>S</b>", "title": "<script>alert(1)</script>", "link": "", "summary": ""}])
    page = html_for(config, headlines=headlines)
    assert "<script>" not in page and "<b>S</b>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_html_marks_missing_sections_unavailable(config):
    page = html_for(config, forecast=None, headlines=None)
    assert page.count(">unavailable<") == 3
    assert "Feels like" not in page


def test_html_shows_alerts_only_when_triggered(config):
    stormy = html_for(config)
    assert "Steady rain: wear waterproofs" in stormy
    assert "UV 3 or more: use sunscreen" in stormy
    assert "Very gusty" not in stormy and "Light rain" not in stormy
    gale = html_for(config, forecast=make_day({11: dict(precipitation=0.4)}, gusts=70))
    assert "Light rain: an umbrella or a hood" in gale and "Very gusty: skip the umbrella" in gale
    calm = html_for(config, forecast=CALM)
    for text in ("Steady rain", "Light rain", "Very gusty", "UV 3 or more"):
        assert text not in calm


@pytest.mark.parametrize(
    "forecast, icon",
    [
        (WEATHER, "🌧️"),
        (make_day({11: dict(precipitation=0.4)}), "🌦️"),
        (make_day(gusts=70), "💨"),
        (make_day(uv=5), "☀️"),
        (CALM, "🌤️"),
        (Weather(10, 90, 40, 5), "🌤️"),  # no hourly data: nothing to judge by
    ],
)
def test_header_icon_follows_the_weather(config, forecast, icon):
    assert icon in html_for(config, forecast=forecast).split("</head>")[1][:1500]


def test_html_has_a_preheader_and_dark_mode_styles(config):
    page = html_for(config)
    assert "display:none" in page and "Dublin: 4 to 10°C, feels like 7 to 14°C" in page
    assert "prefers-color-scheme: dark" in page


def test_html_footer_has_time_and_city(config):
    assert "Generated at 07:30 · Dublin" in html_for(config)


def test_real_config_renders_chinese_html(real_config):
    page = html_for(real_config)
    assert 'lang="zh"' in page
    for text in ("每日简报：10月13日 周二", "天气", "穿衣建议", "新闻", "气温", "体感", "防水保暖大衣", "2.0 mm", "降雨 60%", "生成于 07:30 · 都柏林"):
        assert text in page
    assert "{" not in page.split("</style>")[1]


def test_header_city_is_white_on_a_dark_pill(config):
    page = html_for(config)
    assert "📍 Dublin" in page
    pill = page.split("📍 Dublin")[0].rsplit("<div", 1)[1]
    assert "color:#ffffff" in pill
    assert "background:#1e3a8a" in pill


def test_dark_mode_darkens_the_header_and_keeps_text_white(config):
    dark = html_for(config).split("prefers-color-scheme: dark")[1].split("}\n@media")[0]
    assert ".hero" in dark and ".city" in dark


def test_html_headlines_are_grouped_under_subheadings(config):
    page = html_for(config)
    news_part = page.split("News")[-1]
    assert news_part.index(">IT and AI<") < news_part.index("Java 27 released")
    assert news_part.index("Java 27 released") < news_part.index(">中文科技<")
    assert news_part.index(">中文科技<") < news_part.index("小米手表发布")
    assert 'href="https://sspai.com/p/1"' in page


def test_html_escapes_group_names(config):
    page = html_for(config, headlines=[{"name": "<i>x</i>", "items": HEADLINES[0]["items"]}])
    assert "<i>x</i>" not in page and "&lt;i&gt;x&lt;/i&gt;" in page


def test_html_marks_clothing_unavailable_when_the_forecast_has_no_hours(config):
    page = html_for(config, forecast=Weather(7.2, 60, 33.5, 4.2))
    assert page.count(">unavailable<") == 1
    assert "Feels like" in page and "Java 27 released" in page


# ---- reminder in the header, hours, gusts, tidy news headings -----------


def test_todays_reminder_is_in_the_header_above_everything_else(config):
    page = html_for(config)
    body = page.split("</head>")[1]
    assert body.index("Steady rain: wear waterproofs") < body.index("📍 Dublin") < body.index(">Weather<")
    assert body.count("Steady rain: wear waterproofs") == 1  # not repeated lower down


def test_a_quiet_day_gets_an_all_clear_line_in_the_header(config):
    body = html_for(config, forecast=CALM).split("</head>")[1]
    assert body.index("Nothing special to watch today") < body.index(">Weather<")
    assert "Nothing special to watch today" not in html_for(config)


def test_no_reminder_line_when_there_is_no_forecast(config):
    page = html_for(config, forecast=None)
    assert "Nothing special to watch today" not in page and "Steady rain" not in page


def test_each_person_shows_the_hours_they_are_dressed_for(config):
    page = html_for(config)
    assert page.count("out 7–10") == 2  # Me and Wife
    assert page.count("out 8–9") == 1  # Child


def test_person_without_windows_shows_no_hours(config):
    config["people"] = [{"name": "Guest"}]
    page = html_for(config, forecast=CALM)
    assert ">Guest<" in page and "out " not in page.split("What to wear")[1].split("News")[0]


def test_wind_box_shows_gusts(config):
    assert "Wind · gusts 34" in html_for(config)


def test_a_one_site_section_names_the_site_once_in_its_heading(config):
    items = [
        {"source": "Sina", "title": f"Story {n}", "link": "", "summary": ""} for n in range(3)
    ]
    page = html_for(config, headlines=[{"name": "Stars", "items": items}])
    assert page.count(">Sina<") == 1
    assert page.index(">Stars<") < page.index(">Sina<") < page.index("Story 0")


def test_a_mixed_section_names_the_site_under_each_headline(config):
    page = html_for(config)
    assert page.count(">InfoQ<") == 1 and page.count(">AWS<") == 1
    assert page.index("Java 27 released") < page.index(">InfoQ<")


# ---- composing once ------------------------------------------------------


def test_compose_holds_everything_both_versions_show(config):
    brief = render.compose(NOW, config, WEATHER, HEADLINES)

    assert brief.title == "Daily Brief: Tue 13 Oct" and brief.city == "Dublin"
    assert brief.alerts == ("rain_steady", "uv")
    assert brief.weather_line.startswith("Dublin: 4 to 10°C")
    assert [person["name"] for person, _ in brief.advice] == ["Me", "Wife", "Child"]
    assert brief.groups == HEADLINES


def test_compose_marks_missing_parts(config):
    brief = render.compose(NOW, config, None, [])
    assert (brief.values, brief.weather_line, brief.alerts, brief.advice, brief.groups) == (
        None, None, (), None, None,
    )


def test_text_and_html_agree_because_they_share_one_brief(config):
    brief = render.compose(NOW, config, WEATHER, HEADLINES)
    plain, rich = render.text(brief, config), render.html(brief, config)
    for wording in ("Steady rain: wear waterproofs", "waterproof warm coat", "Java 27 released"):
        assert wording in plain and wording in rich


def test_a_clothing_failure_is_logged_once_and_blanks_only_that_section(config, caplog):
    from unittest.mock import patch

    with patch.object(render.clothing, "advise", side_effect=RuntimeError("bug")):
        brief = render.compose(NOW, config, WEATHER, HEADLINES)
        plain, rich = render.text(brief, config), render.html(brief, config)

    assert caplog.text.count("clothing failed") == 1
    assert "## What to wear\nunavailable" in plain and rich.count(">unavailable<") == 1
    assert brief.alerts == ("rain_steady", "uv")  # the reminder still shows


def test_an_alerts_failure_does_not_lose_the_clothing_advice(config):
    from unittest.mock import patch

    with patch.object(render.clothing, "alerts", side_effect=RuntimeError("bug")):
        brief = render.compose(NOW, config, WEATHER, HEADLINES)
    assert brief.alerts == () and brief.advice is not None
