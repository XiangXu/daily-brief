"""Clothing advice. Pure functions: no I/O, all thresholds and wording come from `rules`.

Each person dresses for the hours they are actually outside (their `windows`),
not for the worst hour of the day:

- Temperature is the mean air temperature of their first window, minus a step
  for sustained wind, plus their personal offset. Feels-like is not used: it
  already contains wind, which would then be counted twice.
- Rain is judged by amount as well as probability, over all their windows.
- Gusts decide between an umbrella and a hood.
"""

import math


def advise(person, weather, rules) -> list[str]:
    """Return the clothing items for one person, in the order to show them.

    `person` is an entry from `people` in config.yaml, `weather` is a
    `weather.Weather`, and `rules` is the `clothing` section of config.yaml.
    """
    names = person.get("windows") or ["day"]
    windows = [hours for name in names if (hours := _window(weather, rules, name))]
    if not windows:
        raise ValueError(f"no forecast hours for {person.get('name', 'this person')}")

    is_child = person.get("child", False)
    offset = person.get("offset", 0)
    tiers = rules["tiers"]
    felt = _effective(windows[0], offset, rules)
    index = _tier_index(felt, tiers)
    tier = tiers[index]

    outdoors = [hour for hours in windows for hour in hours]
    rain = _rain_level(outdoors, rules["rain"])
    gust = max(hour.wind_gusts for hour in outdoors)

    outer, rain_items = _rain_gear(tier.get("outer"), rain, gust, is_child, rules)
    items = [tier["top"]]
    if outer:
        items.append(outer)
    if tier.get("extras"):
        items.append(tier["extras"])
    items += rain_items
    if is_child:
        items += _child_items(names, rain, weather, rules)

    change = _change_later(felt, index, windows[1:], offset, rules)
    if change:
        items.append(change)
    if gust >= rules["wind"]["gust_threshold"]:
        items.append(rules["wind"]["advice"])
    uv = _uv_rule(max(hour.uv_index for hour in outdoors), rules["uv"])
    if uv:
        items.append(uv["child_advice"] if is_child else uv["advice"])
    return items


def advise_people(people, weather, rules) -> list[tuple[dict, list[str]]]:
    """Pair each person with their clothing items."""
    return [(person, advise(person, weather, rules)) for person in people]


def alerts(weather, rules) -> list[str]:
    """What stands out about the whole day, as keys in this order:

    "rain_light" or "rain_steady", then "wind" (or the milder "breezy"), then "uv".
    """
    day = _window(weather, rules, "day")
    if not day:
        return []
    triggered = []
    rain = _rain_level(day, rules["rain"])
    if rain:
        triggered.append(f"rain_{rain}")
    gust = max(hour.wind_gusts for hour in day)
    if gust >= rules["wind"]["gust_threshold"]:
        triggered.append("wind")
    elif gust >= rules["wind"]["breezy_gust"]:
        triggered.append("breezy")
    if _uv_rule(max(hour.uv_index for hour in day), rules["uv"]):
        triggered.append("uv")
    return triggered


def format_line(person, items, template="{name}: {items}") -> str:
    """One line per person, e.g. `Child: light jacket + raincoat + boots`."""
    return template.format(
        name=person["name"], emoji=person.get("emoji", ""), items=" + ".join(items)
    ).strip()


def _window(weather, rules, name) -> list:
    """The forecast hours inside a named window (end hour excluded)."""
    start, end = rules["windows"][name]
    return [hour for hour in weather.hours if start <= hour.hour < end]


def _effective(hours, offset, rules) -> float:
    """What the window feels like to this person: air, less wind, plus their offset."""
    temperature = math.fsum(h.temperature for h in hours) / len(hours)
    wind = math.fsum(h.wind_speed for h in hours) / len(hours)
    steps = [step["minus"] for step in rules["wind_chill"] if wind >= step["min"]]
    return temperature - max(steps, default=0) + offset


def _tier_index(felt, tiers) -> int:
    for index, tier in enumerate(tiers):
        if tier.get("min") is None or felt >= tier["min"]:
            return index
    raise ValueError("clothing tiers need a final entry with no `min`")


def _rain_level(hours, rain) -> str | None:
    """"steady", "light" or None. Probability alone, with no amount, is not rain."""
    total = round(math.fsum(h.precipitation for h in hours), 2)
    wettest = max(h.precipitation for h in hours)
    steady, light = rain["steady"], rain["light"]
    if wettest >= steady["hour_mm"] or total >= steady["total_mm"]:
        return "steady"
    likely_wet_hour = any(
        h.precipitation_probability >= light["probability"] and h.precipitation >= light["hour_mm"]
        for h in hours
    )
    if total >= light["total_mm"] or likely_wet_hour:
        return "light"
    return None


def _rain_gear(outer, level, gust, is_child, rules) -> tuple[str | None, list[str]]:
    """The outer layer after rain is taken into account, plus any extra items."""
    rain = rules["rain"]
    if level == "steady":
        if outer:
            return rain["waterproof_outer"].format(outer=outer), []
        return rain["no_outer"]["steady"], []
    if level == "light":
        # An umbrella is no use in gusts, and a small child will not carry one.
        if gust < rain["umbrella_max_gust"] and not is_child:
            return outer, [rain["umbrella"]]
        if outer:
            return rain["hooded_outer"].format(outer=outer), []
        return rain["no_outer"]["light"], []
    return outer, []


def _child_items(window_names, level, weather, rules) -> list[str]:
    child = rules["child"]
    items = []
    if level == "steady":
        items.append(child["boots"])
    if level:
        items.append(child["spare"][level])
    if _is_dark(window_names, weather, rules):
        items.append(child["dark"])
    return items


def _is_dark(window_names, weather, rules) -> bool:
    """True if the first window starts before sunrise or the last ends after sunset."""
    sunrise, sunset = _clock(weather.sunrise), _clock(weather.sunset)
    starts = rules["windows"][window_names[0]][0]
    ends = rules["windows"][window_names[-1]][1]
    return (sunrise is not None and starts < sunrise) or (sunset is not None and ends > sunset)


def _clock(stamp) -> float | None:
    """"2026-10-13T07:53" as hours since midnight (7.88), or None."""
    try:
        hour, minute = stamp[11:16].split(":")
        return int(hour) + int(minute) / 60
    except (TypeError, ValueError):
        return None


def _change_later(felt, index, later_windows, offset, rules) -> str | None:
    """A note when a later window is clearly warmer or colder, and in another tier.

    Needing both a gap in degrees and a different tier stops it flickering.
    """
    change = rules["change_later"]
    biggest = 0.0
    for hours in later_windows:
        later = _effective(hours, offset, rules)
        delta = later - felt
        later_index = _tier_index(later, rules["tiers"])
        # Tiers run warmest first, so a smaller index is a warmer tier.
        in_other_tier = later_index < index if delta > 0 else later_index > index
        if abs(delta) >= change["min_degrees"] and in_other_tier and abs(delta) > abs(biggest):
            biggest = delta
    if biggest > 0:
        return change["warmer"]
    if biggest < 0:
        return change["colder"]
    return None


def _uv_rule(uv, uv_rules) -> dict | None:
    matching = [rule for rule in uv_rules if uv >= rule["min"]]
    return max(matching, key=lambda rule: rule["min"]) if matching else None
