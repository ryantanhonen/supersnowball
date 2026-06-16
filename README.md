# supersnowball — CBA policy-rate monitor

Watches the [Central Bank of Armenia press-releases page](https://www.cba.am/en/press-releases/?keyword=rate)
for new **policy / refinancing rate** announcements and sends a **Telegram**
message when one appears.

## How it works

A scheduled GitHub Action (`.github/workflows/monitor-cba-rate.yml`) runs
**every 30 minutes**:

1. Launches headless Chromium via Playwright (the CBA site returns HTTP 403
   to plain HTTP clients, so a real browser is required).
2. Scrapes the press-release listing filtered by `keyword=rate`.
3. Keeps only titles matching a monetary-policy regex (policy/refinancing
   rate, basis points, deposit facility, lombard, repo…).
4. Diffs against `monitor/state.json` (the URLs already seen).
5. Sends a Telegram message for anything new, then commits the updated
   state back to the repo.

The **first run is a baseline** — it records the current releases without
notifying, so you don't get a flood of old announcements.

## Setup

### 1. Create a Telegram bot and get your chat id

1. In Telegram, message [@BotFather](https://t.me/BotFather) → `/newbot` →
   copy the **bot token**.
2. Send any message to your new bot (so it can message you back).
3. Get your chat id: open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and
   read `result[].message.chat.id`. (For a channel, add the bot as admin
   and use the channel id / `@channelusername`.)

### 2. Add repository secrets

In **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the BotFather token |
| `TELEGRAM_CHAT_ID` | your chat / channel id |

### 3. Put this on the default branch

> ⚠️ GitHub runs `schedule:` workflows **only on the repository's default
> branch**. Merge this branch into the default branch (or set it as
> default) or the 30-minute timer will not fire.

Also enable, under **Settings → Actions → General → Workflow permissions**,
*"Read and write permissions"* so the workflow can commit `state.json`.

## Manual run / testing

Trigger it by hand from the **Actions** tab (`Run workflow`), or locally:

```bash
pip install -r monitor/requirements.txt
python -m playwright install --with-deps chromium

# dry run: scrape + show what would be sent, no Telegram, no notifications
DRY_RUN=1 python monitor/scrape.py
```

## Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `CBA_URL` | press-releases?keyword=rate | page to scrape |
| `TITLE_REGEX` | policy/refinancing rate… | which titles count as relevant |
| `STATE_FILE` | `monitor/state.json` | where seen-history is stored |
| `DRY_RUN` | unset | `1` to skip sending Telegram messages |

## Caveats

- Scraping depends on the site's HTML structure; if CBA changes it, the
  scraper may find nothing. The workflow uploads `last_page.html` as an
  artifact in that case so the selectors can be updated.
- Anti-bot challenges can occasionally block even headless Chromium; if you
  see empty scrapes, the `last_page.html` artifact will show what loaded.
- For an official, scraping-free alternative, consider the CBA site's own
  **Subscription** feature.
