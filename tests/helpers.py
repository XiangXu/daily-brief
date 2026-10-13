"""Builders for forecasts used across the tests."""

from weather import Hour, summarise

SUNRISE = "2026-10-13T07:53"
SUNSET = "2026-10-13T18:30"


def make_day(overrides=None, sunrise=SUNRISE, sunset=SUNSET, **base):
    """A 24-hour forecast. `base` applies to every hour; `overrides` is {hour: {...}}.

    Fields: temperature, apparent (default temperature - 2), wind, gusts (default
    wind), precipitation (mm), probability (%), uv.
    """
    defaults = dict(
        temperature=12.0, apparent=None, wind=10.0, gusts=None,
        precipitation=0.0, probability=0, uv=0.0,
    )
    defaults.update(base)
    overrides = overrides or {}
    hours = []
    for hour in range(24):
        v = {**defaults, **overrides.get(hour, {})}
        hours.append(
            Hour(
                hour=hour,
                temperature=v["temperature"],
                apparent_temperature=v["temperature"] - 2 if v["apparent"] is None else v["apparent"],
                precipitation=v["precipitation"],
                precipitation_probability=v["probability"],
                wind_speed=v["wind"],
                wind_gusts=v["wind"] if v["gusts"] is None else v["gusts"],
                uv_index=v["uv"],
            )
        )
    return summarise(tuple(hours), sunrise, sunset, 7, 19)


def span(first, last_exclusive, **values):
    """The same values for hours first..last_exclusive-1, for `overrides`."""
    return {hour: values for hour in range(first, last_exclusive)}


# Dublin, 13 Oct 2026: (temp, feels, rain %, mm, wind, gust, uv) for hours 6..21.
TODAY = [
    (14.1, 11.5, 41, 0.1, 20.2, 42.5, 0.0), (13.9, 11.3, 12, 0.0, 19.1, 40.7, 0.0),
    (13.7, 10.7, 3, 0.0, 20.9, 44.3, 0.0), (13.8, 10.6, 4, 0.0, 22.0, 44.3, 0.1),
    (14.5, 10.9, 12, 0.0, 24.1, 47.5, 0.4), (14.8, 11.0, 35, 0.1, 24.8, 50.0, 1.0),
    (15.1, 11.7, 67, 0.0, 20.2, 41.8, 0.8), (14.1, 10.9, 82, 0.5, 20.9, 42.8, 1.7),
    (15.3, 11.1, 66, 0.0, 23.4, 49.7, 1.5), (15.5, 11.6, 33, 0.0, 21.2, 46.1, 0.9),
    (15.4, 11.9, 8, 0.0, 18.0, 38.9, 0.5), (15.2, 11.6, 0, 0.0, 16.6, 35.6, 0.1),
    (14.5, 11.2, 0, 0.0, 15.5, 33.5, 0.0), (14.1, 11.1, 0, 0.0, 14.4, 30.2, 0.0),
    (13.5, 11.2, 0, 0.0, 9.4, 20.2, 0.0), (13.1, 11.1, 0, 0.0, 9.4, 19.4, 0.0),
]


def todays_weather():
    overrides = {
        hour: dict(temperature=t, apparent=f, probability=p, precipitation=mm, wind=w, gusts=g, uv=u)
        for hour, (t, f, p, mm, w, g, u) in zip(range(6, 22), TODAY)
    }
    return make_day(overrides)


def stormy_day():
    """Cold morning, a wet hour at noon, a breezy hour, some UV, warmer by 14:00.

    Displays as: 4 to 10°C, feels like 7 to 14°C, rain 2.0 mm (60%), wind 34 km/h, UV 4.
    """
    return make_day(
        {
            11: dict(wind=33.5),
            12: dict(precipitation=2.0, probability=60),
            13: dict(uv=4.2),
            14: dict(temperature=10.4, apparent=13.6),
        },
        temperature=4.1, apparent=7.2,
    )


def calm_day():
    return make_day(temperature=16.0, apparent=18.0, wind=5, uv=1)
