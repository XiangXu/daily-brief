# ☀️ Daily Brief

> Good morning! Here's the weather, what to wear, and what happened while you slept. ☕

Every morning at **06:30 Dublin time** (08:00 on weekends and Irish bank holidays), a one-page brief lands in your inbox (and/or WeCom) with:

- 🌦️ **Weather** for the day, from [Open-Meteo](https://open-meteo.com/). Free, no API key.
- 🧥 **Clothing advice** for each family member, based on the hours they are actually outside. No more "why didn't you tell me it would rain at pickup?"
- 📰 **News headlines** with links, in tidy sections: 🎬 entertainment, 🌏 Chinese news, 📱 Chinese tech, 🚙 Chinese cars, 🏀 Hupu (NBA and daily-hot forum threads), 🍀 Ireland, 💻 IT and AI, 🛠️ tech stack and 🚗 cars.

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
| `EMAIL_RECIPIENTS` | 📬 Comma-separated addresses that receive the brief |
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

Without `--force`, the script only sends if it starts within `schedule.window_minutes` after the day's time in `schedule.send_times` (`weekday`, or `day_off` for weekends and bank holidays). Otherwise it exits quietly and goes back to sleep. 😴

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
| 📮 `notify` | Delivery channels (`email`, `wecom`), retries and the SMTP server |

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

1. 🔒 Keep personal data out of the repository: email addresses live in secrets, and names in `config.yaml` stay generic.
2. 🤫 Add the variables above as repository secrets.
3. 🕖 The workflow runs every 30 minutes from 05:37 to 08:37 UTC: just after each possible send time and again half an hour later in case GitHub drops a run, and off the hour, when GitHub is less busy. The script checks the date and time in `Europe/Dublin`, so only runs within 90 minutes of that day's send time go ahead, on workdays and days off, in summer and in winter, with no manual changes for daylight saving. The first one to send leaves a `brief-sent-<date>` marker in the Actions cache, and the rest that day see it and skip 🛑, so you get one brief.

4. ⏱️ Add an outside trigger (strongly recommended). GitHub's own schedule is best effort: it often starts runs late, and sometimes not at all. On 2026-10-15 it started none of the morning's runs, and the brief only went out when started by hand. So a free scheduler, [cron-job.org](https://cron-job.org), also asks GitHub to start the workflow. The brief still runs on GitHub Actions; cron-job.org only presses "start".

   **a. A GitHub token** for cron-job.org to call GitHub with: **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token** ([direct link](https://github.com/settings/personal-access-tokens/new)).
   - Repository access: **Only select repositories** → this repository. (The default, "Public repositories", is read-only, so the permission below never offers write access.)
   - Permissions: **+ Add permissions → Actions**, then switch its access to **Read and write**.
   - Note the expiry date: when the token expires the trigger stops, and you need a new token in both jobs.

   **b. Two jobs on cron-job.org**, both in the UTC time zone, each sending the same request:

   ```
   POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/daily-brief.yml/dispatches
   Authorization: Bearer <token>
   Accept: application/vnd.github+json
   Content-Type: application/json

   {"ref": "main", "inputs": {"scheduled": "true"}}
   ```

   | Job | Days | Times (UTC) | Why |
   |---|---|---|---|
   | 💼 Workdays (06:30 Dublin) | Mon–Fri | :07 and :37 past 05, 06 and 07 | 06:30 Dublin is 05:30 UTC in summer and 06:30 in winter |
   | 🛋️ Weekends and bank holidays (08:00 Dublin) | every day | :07 and :37 past 07 and 08 | 08:00 Dublin is 07:00 UTC in summer and 08:00 in winter. Every day, because cron-job.org does not know Irish bank holidays |

   Turn on cron-job.org's failure notification, so an expired or revoked token emails you instead of failing quietly. A successful call gets `204` back from GitHub.

   **Why this never sends two briefs.** A dispatch with `scheduled: true` behaves exactly like a scheduled run, and a manual **Run workflow** leaves it off and sends straight away. Every scheduled-style run:
   - waits for any other run to finish first (the workflow's `concurrency` group), so two runs never check at the same moment;
   - skips if today's `brief-sent-<date>` marker is in the Actions cache;
   - otherwise sends only inside the send window, and saves the marker only if every channel delivered.

   So the first run in the window sends, and every later one that day, from cron-job.org or GitHub, skips. Extra runs outside the window just log "Outside the send window" and stop. GitHub's own schedule stays on as a backup in case cron-job.org is down or the token has expired.

   The token lives only in cron-job.org: it is not a repository secret and the workflow never reads it.

GitHub pauses scheduled workflows in public repos after 60 days without activity, so each run re-enables its own workflow to keep the schedule going 🔁.

Can't wait until tomorrow? Trigger the workflow by hand: **Actions → Daily Brief → Run workflow** ▶️

Generated briefs are never committed to the repository 🙅. They are only delivered by email or WeCom.

---

Made with ❤️ (and a lot of ☔) in Dublin.
