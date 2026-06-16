#!/usr/bin/env python3
"""
Monitor the Central Bank of Armenia (CBA) press-releases page for new
policy / refinancing rate announcements and notify via Telegram.

The CBA site sits behind bot protection that returns HTTP 403 to plain
HTTP clients (requests / curl / WebFetch all get blocked), so we drive a
real headless Chromium via Playwright to render the page.

State (the set of press-release URLs we've already seen) is persisted to
monitor/state.json and committed back to the repo by the workflow, since
GitHub Actions runs are otherwise stateless between invocations.

Environment variables:
  TELEGRAM_BOT_TOKEN  (required to send) Telegram bot token
  TELEGRAM_CHAT_ID    (required to send) chat/channel id to message
  CBA_URL             override the page to scrape
  TITLE_REGEX         case-insensitive regex; only titles matching it are
                      treated as relevant rate announcements
  STATE_FILE          path to the JSON state file
  DRY_RUN             "1" to skip sending Telegram messages (still updates state)
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

CBA_URL = os.environ.get(
    "CBA_URL",
    "https://www.cba.am/en/press-releases/?keyword=rate",
)

# Default: monetary-policy rate decisions, not e.g. exchange-rate notices.
TITLE_REGEX = os.environ.get(
    "TITLE_REGEX",
    r"(policy rate|refinancing rate|basis point|interest rate|"
    r"deposit facility|lombard|repo)",
)

STATE_FILE = Path(os.environ.get("STATE_FILE", "monitor/state.json"))
MAX_SEEN = 300  # cap the persisted history so state.json stays small

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def scrape() -> list[dict]:
    """Return a list of {url, title, date} for press releases on the page."""
    items: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = context.new_page()
        page.goto(CBA_URL, wait_until="networkidle", timeout=60_000)
        # Give any anti-bot JS challenge / client rendering a moment to settle.
        page.wait_for_timeout(4_000)

        # Each press release links to a detail page under /press-releases/.
        anchors = page.eval_on_selector_all(
            "a[href*='/press-releases/']",
            """els => els.map(a => {
                // climb up a few levels to find a container that holds the date
                let node = a;
                let block = '';
                for (let i = 0; i < 4 && node; i++) {
                    node = node.parentElement;
                    if (node) block = node.innerText || '';
                    if (/\\d{4}-\\d{2}-\\d{2}/.test(block)) break;
                }
                return {
                    href: a.href,
                    title: (a.innerText || a.textContent || '').trim(),
                    block: block,
                };
            })""",
        )

        if not anchors:
            # Persist rendered HTML for debugging when the structure changes.
            Path("monitor/last_page.html").write_text(
                page.content(), encoding="utf-8"
            )

        seen_hrefs = set()
        for a in anchors:
            href = (a.get("href") or "").split("#")[0].split("?")[0]
            title = a.get("title", "").strip()
            # Skip the listing page itself, pagination, and empty titles.
            if not title or len(title) < 8:
                continue
            if href.rstrip("/").endswith("/press-releases"):
                continue
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            m = DATE_RE.search(a.get("block", ""))
            items.append(
                {"url": href, "title": title, "date": m.group(0) if m else ""}
            )

        browser.close()
    return items


def send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if os.environ.get("DRY_RUN") == "1":
        print(f"[DRY_RUN] would send Telegram message:\n{text}")
        return
    if not token or not chat_id:
        print("WARNING: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; "
              "skipping notification.", file=sys.stderr)
        return
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        }
    ).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            print(f"Telegram API returned {resp.status}", file=sys.stderr)


def main() -> int:
    title_re = re.compile(TITLE_REGEX, re.IGNORECASE)
    items = scrape()
    print(f"Scraped {len(items)} press-release item(s) from {CBA_URL}")

    relevant = [it for it in items if title_re.search(it["title"])]
    print(f"{len(relevant)} match the rate-announcement filter.")

    state = load_state()
    seen = set(state.get("seen", []))
    first_run = "seen" not in state

    new_items = [it for it in relevant if it["url"] not in seen]

    if first_run:
        # Baseline only: record what's there now, don't blast every old release.
        print("First run: recording baseline, no notifications sent.")
    else:
        for it in new_items:
            text = (
                "🇦🇲 <b>New CBA rate press release</b>\n\n"
                f"<b>{it['title']}</b>\n"
                f"{it['date']}\n"
                f"{it['url']}"
            )
            print(f"NOTIFY: {it['title']} ({it['date']})")
            send_telegram(text)

    # Update + persist state (newest first, capped).
    all_urls = [it["url"] for it in relevant] + list(state.get("seen", []))
    deduped: list[str] = []
    for u in all_urls:
        if u not in deduped:
            deduped.append(u)
    state["seen"] = deduped[:MAX_SEEN]
    save_state(state)

    if not first_run and not new_items:
        print("No new rate announcements.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
