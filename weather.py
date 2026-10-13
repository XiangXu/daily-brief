"""Today's forecast from the Open-Meteo API: every hour, plus a daytime summary."""

import logging
import math
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "precipitation_probability",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "uv_index",
)
DAILY_FIELDS = ("sunrise", "sunset")


@dataclass(frozen=True)
class Hour:
    """One forecast hour, in local time. Missing values arrive as neutral defaults."""

    hour: int
    temperature: float  # °C, actual air temperature
    apparent_temperature: float  # °C, feels-like (display only)
    precipitation: float  # mm in the preceding hour
    precipitation_probability: float  # %
    wind_speed: float  # km/h, sustained
    wind_gusts: float  # km/h
    uv_index: float


@dataclass(frozen=True)
class Weather:
    """The day's forecast.

    Clothing advice works from `hours`. The other fields summarise the daytime
    hours for display: the range of temperatures and the peaks of rain, wind and UV.
    """

    apparent_temperature: float  # °C, feels-like, daytime minimum
    precipitation_probability: float  # %, daytime maximum
    wind_speed_10m: float  # km/h, daytime maximum
    uv_index: float  # daytime maximum
    apparent_temperature_max: float | None = None  # °C, feels-like, daytime maximum
    temperature: float | None = None  # °C, actual air temperature, daytime minimum
    temperature_max: float | None = None  # °C, actual air temperature, daytime maximum
    precipitation_total: float = 0.0  # mm over the daytime hours
    wind_gusts_max: float = 0.0  # km/h, daytime maximum
    hours: tuple[Hour, ...] = ()
    sunrise: str | None = None  # local ISO time, e.g. "2026-10-13T07:53"
    sunset: str | None = None


def fetch(config) -> Weather | None:
    """Return today's weather, or None if the forecast is unavailable."""
    location = config["location"]
    settings = config["weather"]
    params = {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "hourly": ",".join(HOURLY_FIELDS),
        "daily": ",".join(DAILY_FIELDS),
        "timezone": config["schedule"]["timezone"],
        "forecast_days": 1,
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=settings["timeout_seconds"])
        resp.raise_for_status()
        data = resp.json()
        daily = data.get("daily") or {}
        return summarise(
            _hours(data["hourly"]),
            _first(daily.get("sunrise")),
            _first(daily.get("sunset")),
            *config["clothing"]["windows"]["day"],
        )
    except (requests.RequestException, KeyError, TypeError, ValueError, IndexError) as exc:
        log.error("Weather unavailable: %s: %s", type(exc).__name__, exc)
        return None


def summarise(hours, sunrise, sunset, day_start, day_end) -> Weather:
    """Build a `Weather` from hourly rows. The summary covers day_start..day_end (exclusive)."""
    day = [h for h in hours if day_start <= h.hour < day_end]
    if not day:
        raise ValueError("no forecast for the daytime hours")
    return Weather(
        apparent_temperature=min(h.apparent_temperature for h in day),
        precipitation_probability=max(h.precipitation_probability for h in day),
        wind_speed_10m=max(h.wind_speed for h in day),
        uv_index=max(h.uv_index for h in day),
        apparent_temperature_max=max(h.apparent_temperature for h in day),
        temperature=min(h.temperature for h in day),
        temperature_max=max(h.temperature for h in day),
        precipitation_total=round(math.fsum(h.precipitation for h in day), 2),
        wind_gusts_max=max(h.wind_gusts for h in day),
        hours=tuple(hours),
        sunrise=sunrise,
        sunset=sunset,
    )


def _hours(hourly) -> tuple[Hour, ...]:
    # Times look like "2026-10-13T07:00", in the requested timezone.
    def value(field, index, default=None):
        series = hourly.get(field) or []
        found = series[index] if index < len(series) else None
        return default if found is None else found

    hours = []
    for index, stamp in enumerate(hourly["time"]):
        air = value("temperature_2m", index)
        feels = value("apparent_temperature", index)
        if air is None and feels is None:
            continue  # nothing to dress for in this hour
        hours.append(
            Hour(
                hour=int(stamp[11:13]),
                temperature=feels if air is None else air,
                apparent_temperature=air if feels is None else feels,
                precipitation=value("precipitation", index, 0.0),
                precipitation_probability=value("precipitation_probability", index, 0),
                wind_speed=value("wind_speed_10m", index, 0.0),
                wind_gusts=value("wind_gusts_10m", index, 0.0),
                uv_index=value("uv_index", index, 0.0),
            )
        )
    return tuple(hours)


def _first(values):
    return values[0] if values else None
