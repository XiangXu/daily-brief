"""The brief, as plain text and as an HTML email.

`compose` works out what the brief says, once. `text` and `html` only format
it, so the two versions cannot disagree.

The HTML uses tables and inline styles because email clients ignore most
modern CSS. Every piece of feed or config text goes through `escape`.
"""

import logging
from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

import clothing

log = logging.getLogger(__name__)

# Palette. The dark-mode overrides in STYLE reuse the same roles.
BLUE = "#2563eb"
TEXT = "#111827"
MUTED = "#6b7280"
CARD = "#ffffff"
PAGE = "#f3f4f6"
RULE = "#e5e7eb"
SOFT = "#eff6ff"
PILL = "#e0e7ff"
PILL_TEXT = "#3730a3"

TABLE = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB',"
    "'Microsoft YaHei',Roboto,Helvetica,Arial,sans-serif"
)

STYLE = """
@media (prefers-color-scheme: dark) {
  .bg { background: #0b1220 !important; }
  .card { background: #172033 !important; }
  .txt { color: #e5e7eb !important; }
  .muted { color: #9ca3af !important; }
  .stat { background: #1f2c45 !important; }
  .pill { background: #2b3a5c !important; color: #c7d2fe !important; }
  .rule { border-color: #2a3550 !important; }
  .hero { background: #1e3a8a !important; background-image: linear-gradient(135deg,#1e3a8a,#4c1d95) !important; }
  .city { background: #0f172a !important; color: #ffffff !important; }
  a.link { color: #93c5fd !important; }
}
@media only screen and (max-width: 480px) {
  .pad { padding-left: 16px !important; padding-right: 16px !important; }
}
"""


def title(now, config) -> str:
    """The email subject, e.g. `每日简报：10月13日 周二`.

    Names come from config, not strftime, so the output ignores the system locale.
    """
    brief = config["brief"]
    return brief["title"].format(
        weekday=brief["weekdays"][now.weekday()],
        day=now.day,
        month=brief["months"][now.month - 1],
    )


def weather_values(forecast, brief) -> dict[str, str]:
    """The forecast as display strings: temp, feels, rain (%), rain_mm, wind, gust and uv."""
    return {
        "temp": _temperature(forecast.temperature, forecast.temperature_max, brief),
        "feels": _temperature(
            forecast.apparent_temperature, forecast.apparent_temperature_max, brief
        ),
        "rain": f"{forecast.precipitation_probability:.0f}",
        "rain_mm": f"{forecast.precipitation_total:.1f}",
        "wind": f"{forecast.wind_speed_10m:.0f}",
        "gust": f"{forecast.wind_gusts_max:.0f}",
        "uv": f"{forecast.uv_index:.0f}",
    }


def _temperature(low, high, brief) -> str:
    """`4 to 10°C`, or `7°C` when there is no range. A dash if unknown."""
    if low is None:
        return "–"
    low_text = f"{low:.0f}"
    if high is None or f"{high:.0f}" == low_text:
        return brief["temp_single"].format(low=low_text)
    return brief["temp_range"].format(low=low_text, high=f"{high:.0f}")


@dataclass(frozen=True)
class Brief:
    """What the brief says. None (or empty) marks a part that is unavailable."""

    title: str
    city: str
    footer: str
    values: dict | None  # the forecast as display strings
    weather_line: str | None  # the one-line weather summary
    alerts: tuple[str, ...]  # keys of `brief.alerts`, most important first
    advice: list | None  # [(person, items)]
    groups: list | None  # [{"name", "items"}]


def compose(now, config, forecast, headlines) -> Brief:
    """Derive everything the brief shows. A part that fails is left unavailable."""
    brief = config["brief"]
    city = config["location"]["name"]
    values = weather_line = advice = None
    alerts = ()
    if forecast is not None:
        values = weather_values(forecast, brief)
        weather_line = brief["weather_line"].format(city=city, **values)
        alerts = tuple(_guarded("alerts", clothing.alerts, forecast, config["clothing"]) or ())
        advice = _guarded(
            "clothing", clothing.advise_people, config["people"], forecast, config["clothing"]
        )
    return Brief(
        title=title(now, config),
        city=city,
        footer=brief["footer"].format(time=f"{now:%H:%M}", city=city),
        values=values,
        weather_line=weather_line,
        alerts=alerts,
        advice=advice,
        groups=headlines or None,
    )


def _guarded(name, func, *args):
    """Call `func`. A failure is logged once and becomes None: it must not lose the brief."""
    try:
        return func(*args)
    except Exception:  # noqa: BLE001 - one section must never crash the run
        log.exception("%s failed", name)
        return None


def text(brief, config) -> str:
    """The plain-text body (also WeCom's markdown)."""
    wording = config["brief"]
    unavailable = wording["unavailable"]

    weather = unavailable
    if brief.weather_line is not None:
        weather = "\n".join([brief.weather_line, *(wording["alerts"][key] for key in brief.alerts)])

    wear = unavailable
    if brief.advice is not None:
        wear = "\n".join(
            clothing.format_line(person, items, wording["person_line"])
            for person, items in brief.advice
        )

    news = unavailable
    if brief.groups:
        blocks = []
        for group in brief.groups:
            lines = [f"### {group['name']}"]
            for headline in group["items"]:
                lines.append(f"- {headline['title']} ({headline['source']})")
                if headline["link"]:
                    lines.append(f"  {headline['link']}")
            blocks.append("\n".join(lines))
        news = "\n\n".join(blocks)

    sections = [("weather", weather), ("clothing", wear), ("news", news)]
    return "\n\n".join(f"## {wording['headings'][key]}\n{body}" for key, body in sections)


def build_html(now, config, forecast, headlines) -> str:
    """Compose and format in one step."""
    return html(compose(now, config, forecast, headlines), config)


def html(brief, config) -> str:
    """The HTML email."""
    wording = config["brief"]
    page_title, city, footer = brief.title, brief.city, brief.footer
    preheader = brief.weather_line or ""
    sections = [
        _section(wording["headings"]["weather"], _weather(brief, wording)),
        _section(wording["headings"]["clothing"], _clothing(brief, config)),
        _section(wording["headings"]["news"], _news(brief.groups, wording)),
    ]
    reminders = "".join(
        f'<div style="font-size:17px;line-height:1.5;font-weight:700;color:#ffffff;">{escape(line)}</div>'
        for line in _reminders(brief, wording)
    )
    if reminders:
        reminders = f'<div style="margin-top:12px;">{reminders}</div>'

    return f"""<!DOCTYPE html>
<html lang="{escape(wording['lang'])}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{escape(page_title)}</title>
<style>{STYLE}</style>
</head>
<body class="bg" style="margin:0;padding:0;background:{PAGE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="bg" style="background:{PAGE};">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;font-family:{FONT};">
<tr><td bgcolor="{BLUE}" class="pad hero" style="background:{BLUE};background-image:linear-gradient(135deg,#2563eb,#7c3aed);border-radius:16px 16px 0 0;padding:28px 28px 24px;">
<div style="font-size:40px;line-height:1;">{_icon(brief, wording)}</div>
<div style="font-size:24px;line-height:1.3;font-weight:700;color:#ffffff;margin-top:10px;">{escape(page_title)}</div>
{reminders}
<div class="city" style="display:inline-block;background:#1e3a8a;color:#ffffff;font-size:14px;font-weight:600;line-height:1.2;border-radius:999px;padding:6px 14px;margin-top:14px;">📍 {escape(city)}</div>
</td></tr>
<tr><td class="card pad" bgcolor="{CARD}" style="background:{CARD};padding:8px 28px 8px;">
{''.join(sections)}
</td></tr>
<tr><td class="card muted" bgcolor="{CARD}" align="center" style="background:{CARD};border-radius:0 0 16px 16px;padding:12px 28px 24px;font-size:12px;color:{MUTED};">
{escape(footer)}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def _reminders(brief, wording) -> list[str]:
    """What today needs, most important first. An all-clear line on a quiet day."""
    if brief.values is None:
        return []
    return [wording["alerts"][key] for key in brief.alerts] or [wording["all_clear"]]


def _icon(brief, wording) -> str:
    """One emoji for the day: that of the most important alert."""
    icons = wording["icons"]
    return icons[brief.alerts[0]] if brief.alerts else icons["default"]


def _section(heading, content) -> str:
    return f"""{TABLE}
<tr><td class="txt" style="padding:22px 0 10px;font-size:18px;font-weight:700;color:{TEXT};">{escape(heading)}</td></tr>
<tr><td>{content}</td></tr>
</table>"""


def _unavailable(brief) -> str:
    return (
        f'<div class="stat muted" style="background:{PAGE};border-radius:12px;padding:14px 16px;'
        f'font-size:14px;color:{MUTED};">{escape(brief["unavailable"])}</div>'
    )


def _weather(brief, wording) -> str:
    values = brief.values
    if values is None:
        return _unavailable(wording)

    labels = wording["weather_labels"]
    temperatures = [
        ("🌡️", values["temp"], labels["temp"]),
        ("🧥", values["feels"], labels["feels"]),
    ]
    conditions = [
        # The amount is the headline: a high chance of a trace of rain is not a wet day.
        ("☔", f"{values['rain_mm']} mm", f"{labels['rain']} {values['rain']}%"),
        ("💨", f"{values['wind']} km/h", f"{labels['wind']} · {labels['gust']} {values['gust']}"),
        ("☀️", values["uv"], labels["uv"]),
    ]

    return (
        _stat_row(temperatures)
        + '<div style="height:8px;line-height:8px;font-size:0;">&nbsp;</div>'
        + _stat_row(conditions)
    )


def _stat_row(stats) -> str:
    """A row of equal-width stat boxes (icon, value, label) with gaps between."""
    width = 100 // len(stats)
    cells = [
        f'<td class="stat" width="{width}%" align="center" style="background:{SOFT};border-radius:12px;padding:14px 4px;">'
        f'<div style="font-size:22px;line-height:1.2;">{icon}</div>'
        f'<div class="txt" style="font-size:17px;font-weight:700;color:{TEXT};margin-top:4px;">{escape(value)}</div>'
        f'<div class="muted" style="font-size:12px;color:{MUTED};margin-top:2px;">{escape(label)}</div>'
        "</td>"
        for icon, value, label in stats
    ]
    spacer = '<td width="8" style="width:8px;min-width:8px;font-size:0;line-height:0;">&nbsp;</td>'
    return f"{TABLE}<tr>{spacer.join(cells)}</tr></table>"


def _clothing(brief, config) -> str:
    if brief.advice is None:
        return _unavailable(config["brief"])

    rows = []
    for person, items in brief.advice:
        pills = "".join(
            f'<span class="pill" style="display:inline-block;background:{PILL};color:{PILL_TEXT};'
            f'border-radius:999px;padding:5px 12px;margin:3px 6px 3px 0;font-size:14px;line-height:1.4;">'
            f"{escape(item)}</span>"
            for item in items
        )
        rows.append(
            f'<tr><td class="rule" style="padding:12px 0;border-top:1px solid {RULE};">'
            f'<div class="txt" style="font-size:15px;font-weight:700;color:{TEXT};margin-bottom:4px;">'
            f'{escape(person.get("emoji", ""))} <span>{escape(person["name"])}</span>'
            f"{_hours_label(person, config)}</div>"
            f"{pills}</td></tr>"
        )
    return TABLE + "".join(rows) + "</table>"


def _hours_label(person, config) -> str:
    """The hours this person is dressed for, e.g. "7–10 点出门". Empty if unknown."""
    names = person.get("windows")
    if not names:
        return ""
    start, end = config["clothing"]["windows"][names[0]]
    label = config["brief"]["window_label"].format(start=start, end=end)
    return (
        f' <span class="muted" style="font-size:12px;font-weight:400;color:{MUTED};'
        f'margin-left:4px;">{escape(label)}</span>'
    )


def _news(groups, brief) -> str:
    if not groups:
        return _unavailable(brief)

    rows = []
    for group in groups:
        sources = {headline["source"] for headline in group["items"]}
        one_site = len(sources) == 1
        site = (
            f'<td class="stat muted" align="right" style="background:{SOFT};border-radius:0 10px 10px 0;'
            f'padding:9px 12px;font-size:12px;color:{MUTED};"><span>{escape(next(iter(sources)))}</span></td>'
            if one_site
            else ""
        )
        radius = "10px 0 0 10px" if one_site else "10px"
        rows.append(
            '<tr><td style="padding:18px 0 4px;">'
            f"{TABLE}<tr>"
            f'<td class="stat txt" style="background:{SOFT};border-radius:{radius};padding:9px 12px;'
            f'font-size:15px;font-weight:700;color:{TEXT};"><span>{escape(group["name"])}</span></td>'
            f"{site}</tr></table></td></tr>"
        )
        for position, headline in enumerate(group["items"]):
            text = escape(headline["title"])
            url = _safe_url(headline.get("link", ""))
            if url:
                text = (
                    f'<a class="link" href="{escape(url)}" style="color:#1d4ed8;text-decoration:none;">'
                    f"{text}</a>"
                )
            source = (
                ""
                if one_site
                else f'<div class="muted" style="font-size:12px;color:{MUTED};margin-top:3px;">'
                f'<span>{escape(headline["source"])}</span></div>'
            )
            # The heading bar separates the first headline, so only later ones get a rule.
            border = f"border-top:1px solid {RULE};" if position else ""
            rows.append(
                f'<tr><td class="rule" style="padding:10px 2px;{border}">'
                f'<div class="txt" style="font-size:15px;line-height:1.45;font-weight:600;color:{TEXT};">{text}</div>'
                f"{source}</td></tr>"
            )
    return TABLE + "".join(rows) + "</table>"


def _safe_url(url) -> str | None:
    """Only http(s) links are clickable: feed links are untrusted."""
    parts = urlsplit(url)
    return url if parts.scheme in ("http", "https") and parts.netloc else None
