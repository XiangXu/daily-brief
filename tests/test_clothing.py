import pytest

import dataclasses

from clothing import advise, alerts, format_line, outfit
from helpers import make_day, span, todays_weather
from weather import Weather

ADULT = ["morning", "lunch", "evening"]
ME = {"name": "Me", "offset": 0, "windows": ADULT}
WIFE = {"name": "Wife", "offset": -3, "windows": ADULT}
CHILD = {"name": "Child", "offset": 0, "child": True, "windows": ["school_run", "pickup"]}

# Tiers (top, outer, extras), warmest first.
SHORT = "short sleeves"
LIGHT_TOP, LIGHT_OUT = "light long sleeves", "light jacket"
MID_TOP, MID_OUT = "long sleeves + light sweater", "jacket"
WARM_TOP, WARM_OUT = "sweater", "heavy jacket"
COLD_TOP, COLD_OUT, COLD_EXTRA = "sweater + thermal layer", "warm coat", "hat + gloves"
FREEZE_OUT, FREEZE_EXTRA = "down coat", "hat + gloves + scarf"

UMBRELLA = "folding umbrella"
BOOTS = "rain boots"
SPARE_LIGHT = "spare socks in the bag"
SPARE_STEADY = "spare socks and top in the bag"
DARK = "bright or reflective jacket"
WARMER = "Warms up later: you can take a layer off"
COOLER = "Cooler later: bring an extra layer"
WINDY = "Very windy: skip the umbrella"
UV_MID, UV_HIGH, UV_CHILD = "sunscreen", "sunscreen + hat + sunglasses", "sunscreen + sun hat"


@pytest.fixture
def rules(config):
    return config["clothing"]


# ---- the day that prompted the redesign --------------------------------


def test_todays_drizzle_no_longer_means_hat_gloves_and_boots(rules):
    weather = todays_weather()

    # Morning air 13.8 minus 1 for the breeze = 12.8; 0.5 mm of rain with 44 km/h
    # gusts is a hood, not an umbrella and not waterproofs.
    assert advise(ME, weather, rules) == [MID_TOP, "hooded jacket"]
    assert advise(WIFE, weather, rules) == [WARM_TOP, "hooded heavy jacket"]
    assert advise(CHILD, weather, rules) == [MID_TOP, "hooded jacket", SPARE_LIGHT]


# ---- temperature: air temperature, wind counted once, person offset ------


@pytest.mark.parametrize(
    "temperature, expected",
    [
        (25, [SHORT]),
        (19, [SHORT]),
        (18.9, [LIGHT_TOP, LIGHT_OUT]),
        (15, [LIGHT_TOP, LIGHT_OUT]),
        (14.9, [MID_TOP, MID_OUT]),
        (11, [MID_TOP, MID_OUT]),
        (10.9, [WARM_TOP, WARM_OUT]),
        (7, [WARM_TOP, WARM_OUT]),
        (6.9, [COLD_TOP, COLD_OUT, COLD_EXTRA]),
        (3, [COLD_TOP, COLD_OUT, COLD_EXTRA]),
        (2.9, [COLD_TOP, FREEZE_OUT, FREEZE_EXTRA]),
        (-5, [COLD_TOP, FREEZE_OUT, FREEZE_EXTRA]),
    ],
)
def test_every_tier_for_me_in_calm_dry_weather(rules, temperature, expected):
    assert advise(ME, make_day(temperature=temperature, wind=0), rules) == expected


@pytest.mark.parametrize(
    "temperature, expected",
    [
        (22, [SHORT]),
        (21.9, [LIGHT_TOP, LIGHT_OUT]),
        (17.9, [MID_TOP, MID_OUT]),
        (13.9, [WARM_TOP, WARM_OUT]),
        (9.9, [COLD_TOP, COLD_OUT, COLD_EXTRA]),
        (5.9, [COLD_TOP, FREEZE_OUT, FREEZE_EXTRA]),
    ],
)
def test_wife_is_three_degrees_colder_in_every_tier(rules, temperature, expected):
    assert advise(WIFE, make_day(temperature=temperature, wind=0), rules) == expected


@pytest.mark.parametrize(
    "temperature, expected",
    [(22, [SHORT]), (16, [LIGHT_TOP, LIGHT_OUT]), (12, [MID_TOP, MID_OUT]), (8, [WARM_TOP, WARM_OUT])],
)
def test_child_uses_the_same_tiers_with_no_fixed_extras(rules, temperature, expected):
    assert advise(CHILD, make_day(temperature=temperature, wind=0), rules) == expected


@pytest.mark.parametrize(
    "temperature, wind, expected_top",
    [
        (15.0, 14.9, LIGHT_TOP),  # below the first wind step
        (16.0, 15.0, LIGHT_TOP),  # exactly 15 km/h takes 1 degree off -> 15.0
        (15.9, 15.0, MID_TOP),
        (17.0, 30.0, LIGHT_TOP),  # 30 km/h takes 2 off -> 15.0
        (16.9, 30.0, MID_TOP),
        (18.0, 45.0, LIGHT_TOP),  # 45 km/h takes 3 off -> 15.0
        (17.9, 60.0, MID_TOP),
    ],
)
def test_sustained_wind_lowers_the_temperature_in_steps(rules, temperature, wind, expected_top):
    weather = make_day(temperature=temperature, wind=wind, gusts=0)
    assert advise(ME, weather, rules)[0] == expected_top


def test_feels_like_is_not_used_for_decisions(rules):
    weather = make_day(temperature=16, apparent=5, wind=0)
    assert advise(ME, weather, rules) == [LIGHT_TOP, LIGHT_OUT]


def test_dresses_for_the_first_window_not_the_coldest_hour(rules):
    weather = make_day(span(0, 7, temperature=2), temperature=16, wind=0)
    assert advise(ME, weather, rules) == [LIGHT_TOP, LIGHT_OUT]


def test_person_without_windows_uses_the_whole_day(rules):
    assert advise({"name": "Guest"}, make_day(temperature=12, wind=0), rules) == [MID_TOP, MID_OUT]


def test_thresholds_come_from_rules(rules):
    custom = {**rules, "tiers": [{"min": 30, "top": "hot"}, {"top": "not hot", "outer": "shell"}]}
    assert advise(ME, make_day(temperature=30, wind=0), custom) == ["hot"]
    assert advise(ME, make_day(temperature=29, wind=0), custom) == ["not hot", "shell"]


# ---- rain: amount matters, not just probability -------------------------


def test_high_probability_with_no_forecast_amount_is_not_rain(rules):
    weather = make_day(temperature=14, probability=70)
    assert advise(ME, weather, rules) == [MID_TOP, MID_OUT]


def test_calm_drizzle_means_an_umbrella_only_for_those_out_in_it(rules):
    weather = make_day(
        {12: dict(precipitation=0.2), 13: dict(precipitation=0.2)},
        temperature=14, wind=5, gusts=20, probability=50,
    )
    assert advise(ME, weather, rules) == [MID_TOP, MID_OUT, UMBRELLA]
    # The child is only out at 13:00 of those hours: 0.2 mm is below the threshold.
    assert advise(CHILD, weather, rules) == [MID_TOP, MID_OUT]


def test_one_likely_wet_hour_counts_as_light_rain(rules):
    weather = make_day({8: dict(precipitation=0.1, probability=60)}, temperature=14, wind=5)
    assert advise(ME, weather, rules) == [MID_TOP, MID_OUT, UMBRELLA]
    unlikely = make_day({8: dict(precipitation=0.1, probability=59)}, temperature=14, wind=5)
    assert advise(ME, unlikely, rules) == [MID_TOP, MID_OUT]


def test_gusty_light_rain_means_a_hood_instead_of_an_umbrella(rules):
    weather = make_day({12: dict(precipitation=0.4)}, temperature=14, wind=5, gusts=40)
    assert advise(ME, weather, rules) == [MID_TOP, "hooded jacket"]
    calmer = make_day({12: dict(precipitation=0.4)}, temperature=14, wind=5, gusts=39.9)
    assert advise(ME, calmer, rules) == [MID_TOP, MID_OUT, UMBRELLA]


@pytest.mark.parametrize(
    "overrides",
    [
        {12: dict(precipitation=1.0)},  # one properly wet hour
        span(7, 10, precipitation=0.6) | span(12, 14, precipitation=0.6),  # 3 mm in total
    ],
)
def test_steady_rain_makes_the_outer_layer_waterproof(rules, overrides):
    weather = make_day(overrides, temperature=14, wind=5)
    assert advise(ME, weather, rules) == [MID_TOP, "waterproof jacket"]


def test_rain_in_short_sleeve_weather(rules):
    light = make_day({12: dict(precipitation=0.4)}, temperature=21, wind=5)
    steady = make_day({12: dict(precipitation=2.0)}, temperature=21, wind=5)
    gusty = make_day({12: dict(precipitation=0.4)}, temperature=21, wind=5, gusts=45)
    assert advise(ME, light, rules) == [SHORT, UMBRELLA]
    assert advise(ME, steady, rules) == [SHORT, "waterproof jacket"]
    assert advise(ME, gusty, rules) == [SHORT, "light rain jacket"]


def test_rain_outside_your_windows_is_ignored_for_clothes_but_still_an_alert(rules):
    weather = make_day(span(10, 12, precipitation=5.0, probability=90), temperature=14, wind=5)
    for person in (ME, WIFE, CHILD):
        items = advise(person, weather, rules)
        assert not {UMBRELLA, BOOTS} & set(items)
        assert "waterproof" not in " ".join(items) and "hooded" not in " ".join(items)
    assert alerts(weather, rules) == ["rain_steady"]


def test_child_wears_a_hood_in_light_rain_and_never_carries_an_umbrella(rules):
    light = make_day({13: dict(precipitation=0.4)}, temperature=14, wind=5)
    assert advise(CHILD, light, rules) == [MID_TOP, "hooded jacket", SPARE_LIGHT]


def test_child_gets_boots_and_a_spare_top_only_in_steady_rain(rules):
    steady = make_day({13: dict(precipitation=2.0)}, temperature=14, wind=5)
    assert advise(CHILD, steady, rules) == [MID_TOP, "waterproof jacket", BOOTS, SPARE_STEADY]


# ---- wind, UV, daylight, change through the day ------------------------


def test_storm(rules):
    weather = make_day(temperature=10, wind=45, gusts=85, precipitation=3.0, probability=95)

    assert advise(ME, weather, rules) == [WARM_TOP, "waterproof heavy jacket", WINDY]
    assert advise(WIFE, weather, rules) == [COLD_TOP, "waterproof warm coat", COLD_EXTRA, WINDY]
    assert advise(CHILD, weather, rules) == [
        WARM_TOP, "waterproof heavy jacket", BOOTS, SPARE_STEADY, WINDY,
    ]


def test_wind_warning_needs_strong_gusts(rules):
    assert WINDY in advise(ME, make_day(gusts=65), rules)
    assert WINDY not in advise(ME, make_day(gusts=64.9), rules)


def test_warm_sunny_day_that_heats_up(rules):
    weather = make_day(
        span(7, 10, temperature=16) | span(12, 14, uv=6.0),
        temperature=22, wind=10, sunrise="2026-06-15T05:00", sunset="2026-06-15T21:50",
    )
    assert advise(ME, weather, rules) == [LIGHT_TOP, LIGHT_OUT, UV_HIGH, WARMER]
    assert advise(WIFE, weather, rules) == [MID_TOP, MID_OUT, UV_HIGH, WARMER]
    assert advise(CHILD, weather, rules) == [LIGHT_TOP, LIGHT_OUT, UV_CHILD, WARMER]


@pytest.mark.parametrize(
    "uv, adult, child",
    [(2.9, None, None), (3, UV_MID, UV_CHILD), (5.9, UV_MID, UV_CHILD), (6, UV_HIGH, UV_CHILD)],
)
def test_uv_levels(rules, uv, adult, child):
    weather = make_day(temperature=22, wind=0, uv=uv)
    assert advise(ME, weather, rules) == [SHORT] + ([adult] if adult else [])
    assert advise(CHILD, weather, rules) == [SHORT] + ([child] if child else [])


def test_cooler_evening_means_bring_a_layer(rules):
    weather = make_day(span(7, 10, temperature=12), temperature=8, wind=0)
    assert advise(ME, weather, rules) == [MID_TOP, MID_OUT, COOLER]


def test_no_layer_note_for_small_changes_or_within_the_same_tier(rules):
    small = make_day(span(7, 10, temperature=14.5), temperature=16, wind=0)  # new tier, only 1.5 warmer
    same_tier = make_day(span(7, 10, temperature=11), temperature=14.5, wind=0)  # 3.5 warmer, same tier
    assert advise(ME, small, rules) == [MID_TOP, MID_OUT]
    assert advise(ME, same_tier, rules) == [MID_TOP, MID_OUT]


def test_cold_dark_january_morning(rules):
    weather = make_day(temperature=2, wind=10, sunrise="2026-01-10T08:38", sunset="2026-01-10T16:25")

    freezing = [COLD_TOP, FREEZE_OUT, FREEZE_EXTRA]
    assert advise(ME, weather, rules) == freezing
    assert advise(WIFE, weather, rules) == freezing
    assert advise(CHILD, weather, rules) == [*freezing, DARK]


def test_child_reflective_jacket_when_pickup_is_after_sunset(rules):
    weather = make_day(temperature=12, wind=0, sunrise="2026-12-20T07:00", sunset="2026-12-20T14:30")
    assert advise(CHILD, weather, rules) == [MID_TOP, MID_OUT, DARK]
    assert DARK not in advise(ME, weather, rules)


def test_unknown_sunrise_means_no_daylight_advice(rules):
    weather = make_day(temperature=12, wind=0, sunrise=None, sunset=None)
    assert advise(CHILD, weather, rules) == [MID_TOP, MID_OUT]


# ---- alerts, plumbing --------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, overrides, expected",
    [
        (dict(), None, []),
        (dict(), {11: dict(precipitation=0.4)}, ["rain_light"]),
        (dict(), {11: dict(precipitation=1.0)}, ["rain_steady"]),
        (dict(gusts=44.9), None, []),
        (dict(gusts=45), None, ["breezy"]),
        (dict(gusts=64.9), None, ["breezy"]),
        (dict(gusts=65), None, ["wind"]),  # a gale replaces the milder warning
        (dict(uv=3), None, ["uv"]),
        (dict(gusts=70, uv=7), {11: dict(precipitation=4.0)}, ["rain_steady", "wind", "uv"]),
        (dict(probability=90), None, []),  # likely, but no amount forecast
    ],
)
def test_alerts_describe_the_whole_day(rules, kwargs, overrides, expected):
    assert alerts(make_day(overrides, **kwargs), rules) == expected


def test_a_windy_afternoon_is_flagged_even_if_the_morning_is_calm(rules):
    # Dublin, 2026-10-14: gusts of about 30 in the morning, 57 at 15:00.
    weather = make_day(span(7, 13, gusts=33) | span(13, 19, gusts=57), wind=20)
    assert alerts(weather, rules) == ["breezy"]


def test_alerts_ignore_the_night(rules):
    weather = make_day(span(0, 6, precipitation=9.0, uv=9) | span(20, 24, gusts=120))
    assert alerts(weather, rules) == []


def test_a_forecast_without_hours_cannot_be_advised_on(rules):
    weather = Weather(10.0, 0, 0, 0)
    assert alerts(weather, rules) == []
    with pytest.raises(ValueError):
        advise(ME, weather, rules)


def test_outfit_keeps_notes_apart_from_what_to_wear(rules):
    weather = make_day(span(7, 10, temperature=16) | span(12, 14, uv=6.0), temperature=22, wind=10, gusts=70)
    wear, notes = outfit(ME, weather, rules)
    assert wear == [LIGHT_TOP, LIGHT_OUT, UV_HIGH]
    assert notes == [WARMER, WINDY]
    assert advise(ME, weather, rules) == wear + notes


def test_a_quiet_day_has_no_notes(rules):
    assert outfit(ME, make_day(temperature=16, wind=0), rules) == ([LIGHT_TOP, LIGHT_OUT], [])


def _without_hours(weather, start, end):
    return dataclasses.replace(weather, hours=[h for h in weather.hours if not start <= h.hour < end])


def test_missing_first_window_is_not_silently_replaced_by_a_later_one(rules):
    # Dressing for lunch while showing "out 7-10" would be wrong: give no advice instead.
    weather = _without_hours(make_day(span(12, 14, temperature=25), temperature=5, wind=0), 7, 10)
    with pytest.raises(ValueError):
        advise(ME, weather, rules)


def test_a_missing_later_window_is_skipped(rules):
    weather = _without_hours(make_day(temperature=16, wind=0), 16, 19)
    assert advise(ME, weather, rules) == [LIGHT_TOP, LIGHT_OUT]


def test_format_line():
    assert format_line(CHILD, [LIGHT_TOP, UMBRELLA]) == f"Child: {LIGHT_TOP} + {UMBRELLA}"


def test_format_line_uses_the_template():
    line = format_line({"name": "孩子"}, ["外套", "雨靴"], "{name}：{items}")
    assert line == "孩子：外套 + 雨靴"


def test_format_line_fills_extra_fragments():
    line = format_line({"name": "Me"}, [SHORT], "{name}{hours}: {items}{notes}", hours=" (out 7–10)", notes=" 💡 x")
    assert line == f"Me (out 7–10): {SHORT} 💡 x"
    assert format_line({"name": "Me"}, [SHORT], "{name}{hours}: {items}{notes}") == f"Me: {SHORT}"


def test_format_line_fills_the_emoji_placeholder():
    person = {"name": "Me", "emoji": "👨"}
    assert format_line(person, [SHORT], "{emoji} {name}: {items}") == f"👨 Me: {SHORT}"
    assert format_line({"name": "Me"}, [SHORT], "{emoji}{name}") == "Me"
