from unittest.mock import Mock, patch

import pytest
import requests

import weather
from weather import Hour, fetch

DATE = "2026-10-13"


def payload():
    hours = [f"{DATE}T{h:02d}:00" for h in range(24)]
    apparent = [float(h) for h in range(24)]  # 07:00 -> 7.0 is the daytime minimum
    air = [h - 2.5 for h in range(24)]  # actual temperature: 4.5 to 15.5 by day
    prob = [0] * 24
    mm = [0.0] * 24
    wind = [5.0] * 24
    gusts = [h + 20.0 for h in range(24)]
    uv = [0.0] * 24
    prob[3], mm[3] = 95, 4.0  # night: ignored by the daytime summary
    prob[12], mm[12] = 60, 0.25
    mm[13] = 0.5
    wind[18] = 33.5
    wind[19] = 80.0  # the day window's end hour is excluded: ignored
    uv[13] = 4.2
    gusts[5] = None
    return {
        "hourly": {
            "time": hours,
            "temperature_2m": air,
            "apparent_temperature": apparent,
            "precipitation_probability": prob,
            "precipitation": mm,
            "wind_speed_10m": wind,
            "wind_gusts_10m": gusts,
            "uv_index": uv,
        },
        "daily": {"sunrise": [f"{DATE}T07:53"], "sunset": [f"{DATE}T18:30"]},
    }


def response(data, status=200):
    resp = Mock(status_code=status)
    resp.json.return_value = data
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status}")
    return resp


def fetch_with(config, data):
    with patch.object(weather.requests, "get", return_value=response(data)) as get:
        return fetch(config), get


def test_fetch_keeps_every_hour_with_all_fields(config):
    result, _ = fetch_with(config, payload())

    assert len(result.hours) == 24
    assert result.hours[13] == Hour(
        hour=13, temperature=10.5, apparent_temperature=13.0, precipitation=0.5,
        precipitation_probability=0, wind_speed=5.0, wind_gusts=33.0, uv_index=4.2,
    )
    assert result.sunrise == f"{DATE}T07:53" and result.sunset == f"{DATE}T18:30"


def test_fetch_summarises_daytime_hours_for_display(config):
    result, _ = fetch_with(config, payload())

    assert result.apparent_temperature == 7.0 and result.apparent_temperature_max == 18.0
    assert result.temperature == 4.5 and result.temperature_max == 15.5
    assert result.precipitation_probability == 60
    assert result.wind_speed_10m == 33.5
    assert result.wind_gusts_max == 38.0  # 18:00, the last daytime hour
    assert result.uv_index == 4.2
    assert result.precipitation_total == pytest.approx(0.75)  # night rain excluded


def test_fetch_requests_the_hourly_and_daily_fields(config):
    _, get = fetch_with(config, payload())

    params = get.call_args.kwargs["params"]
    assert params["latitude"] == config["location"]["latitude"]
    assert params["longitude"] == config["location"]["longitude"]
    assert params["timezone"] == config["schedule"]["timezone"]
    assert set(params["hourly"].split(",")) == {
        "temperature_2m", "apparent_temperature", "precipitation_probability",
        "precipitation", "wind_speed_10m", "wind_gusts_10m", "uv_index",
    }
    assert params["daily"] == "sunrise,sunset"
    assert get.call_args.kwargs["timeout"] == config["weather"]["timeout_seconds"]


def test_missing_gusts_count_as_calm_not_as_an_error(config):
    result, _ = fetch_with(config, payload())
    assert result.hours[5].wind_gusts == 0.0


def test_missing_values_get_neutral_defaults(config):
    data = payload()
    data["hourly"]["precipitation"] = [None] * 24
    data["hourly"]["precipitation_probability"] = [None] * 24
    data["hourly"]["uv_index"] = [None] * 24
    result, _ = fetch_with(config, data)

    assert result.precipitation_total == 0 and result.uv_index == 0
    assert result.hours[13].precipitation == 0 and result.hours[13].precipitation_probability == 0


def test_an_hour_without_actual_temperature_falls_back_to_feels_like(config):
    data = payload()
    data["hourly"]["temperature_2m"][9] = None
    result, _ = fetch_with(config, data)
    assert result.hours[9].temperature == 9.0


def test_an_hour_with_no_temperature_at_all_is_dropped(config):
    data = payload()
    data["hourly"]["temperature_2m"][9] = None
    data["hourly"]["apparent_temperature"][9] = None
    result, _ = fetch_with(config, data)
    assert [h.hour for h in result.hours if h.hour == 9] == []
    assert len(result.hours) == 23


def test_missing_daily_block_leaves_sunrise_and_sunset_unknown(config):
    data = payload()
    del data["daily"]
    result, _ = fetch_with(config, data)
    assert result.sunrise is None and result.sunset is None and result.hours


@pytest.mark.parametrize("failure", [requests.ConnectionError("down"), requests.Timeout("slow")])
def test_fetch_returns_none_on_network_error(config, failure):
    with patch.object(weather.requests, "get", side_effect=failure):
        assert fetch(config) is None


def test_fetch_returns_none_on_http_error(config):
    with patch.object(weather.requests, "get", return_value=response({}, status=500)):
        assert fetch(config) is None


@pytest.mark.parametrize("data", [{}, {"hourly": {}}, {"hourly": {"time": []}}])
def test_fetch_returns_none_on_malformed_payload(config, data):
    with patch.object(weather.requests, "get", return_value=response(data)):
        assert fetch(config) is None


def test_fetch_returns_none_when_no_temperature_in_daytime(config):
    data = payload()
    for field in ("temperature_2m", "apparent_temperature"):
        data["hourly"][field] = [None] * 24
    with patch.object(weather.requests, "get", return_value=response(data)):
        assert fetch(config) is None


def test_the_display_summary_and_the_alerts_share_one_day_window(config):
    config["clothing"]["windows"]["day"] = [12, 14]
    result, _ = fetch_with(config, payload())

    # Only 12:00 and 13:00 are summarised now.
    assert result.apparent_temperature == 12.0 and result.apparent_temperature_max == 13.0
    assert result.precipitation_total == pytest.approx(0.75)
    assert "day_start_hour" not in config["weather"]
