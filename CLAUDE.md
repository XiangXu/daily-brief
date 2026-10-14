# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Daily Brief generates a one-page morning brief: weather, clothing advice and IT/AI news headlines with links. It is delivered by email and/or WeCom every morning.

## Commands

- Set up: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, then copy `.env.example` to `.env`.
- Test: `.venv/bin/pytest` (one module: `.venv/bin/pytest tests/test_clothing.py`).
- Preview the brief without sending: `.venv/bin/python main.py --force --dry-run`.
- Send now, ignoring the send window: `.venv/bin/python main.py --force`.

## Working rules

- Always propose a plan first and wait for approval before writing code.
- Write tests first, then implement.
- If one data source fails, still produce the brief and mark that section as "unavailable". Never crash the whole run.
- Keep thresholds and offsets in `config.yaml`, not hardcoded.
- Secrets live in `.env` locally and in GitHub Secrets in CI. Never commit secrets.
- Do not commit generated briefs back to the repo, because they contain family info. Deliver them only via email or WeCom.
- The GitHub repository must be Private.

## Stack

- Python 3.11+, with dependencies in `requirements.txt`.
- Tests use pytest, with one test file per module in `tests/`.
- The brief is written in Chinese. All wording lives in `config.yaml` (`brief`, `people`, `clothing`), not in code, and dates use the config's weekday and month names so output never depends on the system locale. Headlines stay in the language of their feeds. Tests read the English copy `tests/fixtures/config.yaml` (which sets `news.max_workers: 1` so mocked replies arrive in order; tests of parallel fetching answer by URL instead), and `tests/test_config.py` fails if its keys drift from `config.yaml`, so add new config keys to both.
- Weather comes from the Open-Meteo API (free, no key).
- News is fetched with `feedparser` from RSS feeds in English and Chinese. There is no LLM and no API key: stories are ranked by keyword and shown as title, source and link for the reader to review.

## Architecture

`main.py` orchestrates the run: check the send window, fetch, compose, send. It does no formatting. Each data source is its own module, and all configurable values live in `config.yaml`. Modules take the loaded config as an argument rather than reading the file themselves (`main.load_config` is the one loader, also used by the tests).

- `render.py` turns the data into the brief. `compose(now, config, forecast, headlines)` derives everything once into a `Brief` (title, weather strings, alert keys, clothing advice, news groups; None marks an unavailable part, and a failing part is logged once). `text(brief, config)` and `html(brief, config)` only format it, so the two versions cannot disagree: put new facts in `compose`, not in a formatter. The HTML uses tables and inline styles with a dark-mode media query; every piece of feed or config text is HTML-escaped and only http(s) links are clickable. Layout, top to bottom: a header with the day's reminder (the `brief.alerts` lines, or `brief.all_clear` on a quiet day) and an icon from `brief.icons`, the weather boxes, clothing pills per person with the hours they are dressed for, then the news sections under tinted heading bars. A section whose headlines all come from one site names that site once in its bar. The look is deliberately friendly, with emoji: the user rejected a serious newspaper-style redesign.
- `weather.py` fetches the forecast and keeps every hour (`Weather.hours`): `temperature_2m`, `apparent_temperature`, `precipitation`, `precipitation_probability`, `wind_speed_10m`, `wind_gusts_10m`, `uv_index`, plus sunrise and sunset. The other `Weather` fields summarise the `clothing.windows.day` hours for display only; that window is the single definition of "daytime", shared with the alerts.
- `clothing.py` holds pure functions, `advise(person, weather, rules) -> list[str]`, with no API calls. `rules` is the `clothing` section of the config. See "Clothing rules" below.
- `news.py` builds the news in sections (`news.groups`): Ireland, IT and AI, the tech stack, cars (English), Chinese tech, cars (Chinese), Chinese news and 内娱. The order is deliberate: the wife's preferred sections (Chinese news, then 内娱) sit at the bottom. Each section has its own feeds and slot count, so no topic crowds out another. Per section it drops items older than `news.max_age_hours`, ranks by keyword (title match counts double a description match; English words match whole words, Chinese matches anywhere), removes duplicates and caps each source. Sections can add `keywords`, switch off the global ones (`use_global_keywords: false`), require a match (`min_score: 1`) or set their own `max_per_source`. All feeds of all sections are downloaded in one parallel batch (`news.max_workers`; results keep feed order), then parsed in order. A feed that fails is skipped and a section with nothing is left out. Headlines keep the language of their feed. Feeds are fetched with a browser-style `user_agent` because several Chinese sites reject unknown clients.
  - A feed is `{url, name}` (RSS/Atom) or `{url, name, parser}` for a page with no feed. Always give `name`: it is the source shown in the email, and feeds' own titles are often ugly ("www.theregister.com - Articles"). Parsers: `completecar`, `carwow`, `dongchedi`, `autohome` (car sections), `sina_ent` (内娱) and `hupu` (a bbs.hupu.com board, used by the 虎扑 section for the NBA and daily-hot boards; the forum home page has different markup and is not supported). They read the site's HTML, so a site redesign breaks that one source: it logs a warning and the section carries on without it.
  - 内娱 reads Sina's mobile page `ent.sina.cn`. Sina's desktop entertainment site and its roll API stopped updating around 2026-09-30, so do not switch back to them. A self-hosted RSSHub could add Weibo, Douban or Bilibili.
- `notify.py` exposes `send(title, body, config)`. Every channel listed under `notify.channels` gets the brief, each delivered independently.
  - Channels are functions `(title, body, settings, html)` registered with `@channel("name")`. To add one, write the function and list its name in the config. `body` is plain text and `html` is the rich version or None.
  - Email sends both as multipart/alternative. WeCom uses the text as markdown.
  - Preview the email without sending: `.venv/bin/python main.py --force --dry-run --html-out brief.html` (`*.html` previews are git-ignored).
  - Email uses `smtplib` with a Gmail app password and a recipient list from config (me and wife).
  - WeCom sends a webhook POST with a markdown message.
  - Retry twice on failure, then log the error. A delivery failure must not block brief generation.
  - Tests use mocks and never send real messages.

## Clothing rules (`clothing.py`)

Each person dresses for the hours they are actually outside, not for the worst hour of the day. All numbers and wording below live in `config.yaml` (`people`, `clothing`).

People and their `windows` (named hour ranges in `clothing.windows`, end excluded):

- `me`: offset 0, windows morning 7-10, lunch 12-14, evening 16-19.
- `wife`: offset -3 (feels the cold; tunable), same windows.
- `child`: offset 0, `child: true`, windows school_run 8-9 and pickup 13-15.

A person dresses for their first window; the others are checked for rain and for a change later in the day. `day` (7-19) is used only for the weather warnings.

**Temperature** is the mean air temperature of the first window, minus a wind step (sustained wind >= 15 km/h: 1 degree, >= 30: 2, >= 45: 3), plus the person's offset. Do not use `apparent_temperature` for decisions: it already contains wind, so wind would be counted twice. It is shown in the brief only.

| Temp (after wind and offset) | Top | Outer | Extras |
|---|---|---|---|
| 19+ | short sleeves | | |
| 15-19 | light long sleeves | light jacket | |
| 11-15 | long sleeves + light sweater | jacket | |
| 7-11 | sweater | heavy jacket | |
| 3-7 | sweater + thermal layer | warm coat | hat + gloves |
| below 3 | sweater + thermal layer | down coat | hat + gloves + scarf |

**Rain** is judged by amount as well as probability, over all the person's windows. A high probability with no forecast amount is not rain.

- Steady (any hour >= 1.0 mm, or >= 3.0 mm in total): the outer layer becomes waterproof.
- Light (>= 0.3 mm in total, or an hour with >= 60% and >= 0.1 mm): a folding umbrella, or a hooded outer layer if gusts reach 40 km/h.
- Rain changes the outer layer rather than adding a second jacket.

**Other rules**

- Gusts >= 65 km/h: a "skip the umbrella" warning.
- UV >= 3: sunscreen. UV >= 6: sunscreen + hat + sunglasses. The child gets sunscreen + sun hat from 3.
- A later window that is >= 3 degrees warmer or colder and in a different tier adds a "take a layer off" or "bring a layer" note.
- Child only: a hood instead of an umbrella in light rain; rain boots only in steady rain; spare socks (light rain) or spare socks and top (steady rain); a bright or reflective jacket if the school run starts before sunrise or pickup ends after sunset.

`clothing.alerts(weather, rules)` returns `rain_light` or `rain_steady`, `wind`, `uv` for the whole day. It drives the header icon, the warning chips and the text version, so keep `brief.alerts` in step with it.

Output is one line per person, e.g. `Child: sweater + hooded jacket + spare socks in the bag`.

Tests must cover every tier, every person, the rain levels, wind steps, UV levels and the change-later notes. `tests/helpers.py` builds forecasts (`make_day`, `span`) and holds the real Dublin day that prompted this design (`todays_weather`).

The thresholds are judgement calls for Dublin's mild, wet, windy climate. Tune them in `config.yaml` against how days actually felt.

## Deployment

The brief runs on GitHub Actions, on a schedule plus `workflow_dispatch`. The workflow file is `.github/workflows/daily-brief.yml`.

- Secrets: `GMAIL_USER`, `GMAIL_APP_PASSWORD` and `WECOM_WEBHOOK_URL`.
- The brief goes out at 07:00 Dublin time on workdays and 08:00 on days off (weekends and Irish bank holidays, from the `holidays` package): `schedule.send_times` and `schedule.holidays`. Cron runs in UTC, so the workflow fires daily at 06:00, 07:00 and 08:00 UTC, with no day-of-week filters. The script uses `zoneinfo` to find the date and time in `Europe/Dublin`, picks that day's send time, and exits immediately unless it is within `schedule.window_minutes` after it. This handles DST without manual edits, and the window absorbs late starts by GitHub Actions. The window must stay below the 60-minute gap between cron entries, so only one run sends; `tests/test_main.py` simulates every cron entry for two years to check this. A manual `workflow_dispatch` run passes `--force`.
