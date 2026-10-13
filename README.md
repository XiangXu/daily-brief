# ☀️ Daily Brief

> Good morning! Here's the weather, what to wear, and what happened while you slept. ☕

Every morning at **07:30 Dublin time**, a one-page brief lands in your inbox (and/or WeCom) with:

- 🌦️ **Weather** for the day, from [Open-Meteo](https://open-meteo.com/). Free, no API key.
- 🧥 **Clothing advice** for each family member, based on the hours they are actually outside. No more "why didn't you tell me it would rain at pickup?"
- 📰 **News headlines** with links, in tidy sections: 🍀 Ireland, 💻 IT and AI, 🛠️ tech stack, 🚗 cars, 📱 Chinese tech, 🌏 Chinese news and 🎬 entertainment.

The brief is written in Chinese 🇨🇳 and headlines stay in the language of their source. There is no LLM 🤖🚫 and no paid API: news is ranked by keyword from RSS feeds and a few site parsers.

## 🚀 Setup

You need Python 3.11+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Then fill in `.env` 🔑:

| Variable | Used for |
|---|---|
| `GMAIL_USER` | 📧 Gmail address the brief is sent from |
| `GMAIL_APP_PASSWORD` | 🔐 A Gmail [app password](https://myaccount.google.com/apppasswords), not the account password |
| `WECOM_WEBHOOK_URL` | 💬 WeCom group bot webhook (only if the `wecom` channel is enabled) |

## 🎮 Usage

```sh
# 👀 Preview the brief in the terminal without sending
.venv/bin/python main.py --force --dry-run

# 🎨 Also write the HTML email to a file to preview it in a browser
.venv/bin/python main.py --force --dry-run --html-out brief.html

# 📬 Send now, ignoring the send window
.venv/bin/python main.py --force

# ✅ Run the tests
.venv/bin/pytest
```

Without `--force`, the script only sends if it starts within `schedule.window_minutes` after `schedule.send_time`. Otherwise it exits quietly and goes back to sleep. 😴

## 🎛️ Configuration

Everything tunable lives in `config.yaml`. Nothing is hardcoded, so tweak away!

| Section | What it controls |
|---|---|
| ⏰ `schedule` | Timezone, send time and send window |
| 📍 `location` | City name and coordinates for the forecast |
| ✍️ `brief` | All wording of the brief: title, headings, alerts, icons |
| 👨‍👩‍👧 `people` | Each person's name, temperature offset and the hours they are outside |
| 🧣 `clothing` | Time windows, temperature tiers, rain, wind and UV thresholds, and the advice text |
| 🗞️ `news` | Ranking keywords, sections, feeds, slot counts and age limits |
| 📮 `notify` | Delivery channels (`email`, `wecom`), retries and email recipients |

Feels the cold? 🥶 Give that person a negative `offset` in `people` and they get dressed as if it were a few degrees colder.

Tests read an English copy, `tests/fixtures/config.yaml`. A test fails if its keys drift from `config.yaml`, so add new keys to both.

## 🧩 How it works

`main.py` is the conductor 🎼: it checks the send window, fetches the data, composes the brief and sends it. Each part is its own module:

| Module | Role |
|---|---|
| 🌡️ `weather.py` | Fetches the hourly forecast, sunrise and sunset |
| 👕 `clothing.py` | Pure functions that turn a forecast into clothing advice and day-level alerts |
| 📡 `news.py` | Downloads all feeds in parallel, then filters, ranks and de-duplicates per section |
| 🖼️ `render.py` | Builds the brief once (`compose`), then formats it as plain text and HTML |
| 📤 `notify.py` | Sends the brief to every configured channel, with retries |

The brief is hard to break 💪. If a data source fails, the brief still goes out with that section marked as unavailable. A feed that fails is skipped, and a delivery failure on one channel does not block the others.

## ☁️ Deployment

The brief runs on GitHub Actions (`.github/workflows/daily-brief.yml`), so no computer needs to stay on.

1. 🔒 Keep the repository **private**: the config and the briefs contain family information.
2. 🤫 Add the three variables above as repository secrets.
3. 🕢 The workflow runs at 06:30 and 07:30 UTC. The script checks the time in `Europe/Dublin`, so exactly one of the two runs sends the brief, in summer and in winter, with no manual changes for daylight saving.

Can't wait until tomorrow? Trigger the workflow by hand: **Actions → Daily Brief → Run workflow** ▶️

Generated briefs are never committed to the repository 🙅. They are only delivered by email or WeCom.

---

Made with ❤️ (and a lot of ☔) in Dublin.
