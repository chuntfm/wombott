import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("wombott")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "180"))
CONFIRM_SECONDS = int(os.getenv("CONFIRM_SECONDS", "60"))
API_URL = os.getenv(
    "API_URL", "https://api.chunt.org/fm/channels/1/now-playing"
)
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
NOTIFY_RESTREAMS = os.getenv("NOTIFY_RESTREAMS", "false").lower() == "true"
NOTIFY_NOT_LIVE = os.getenv("NOTIFY_NOT_LIVE", "false").lower() == "true"

DEFAULT_LIVE_TEMPLATE = "{show}"
LIVE_MESSAGE_TEMPLATE = os.getenv("LIVE_MESSAGE_TEMPLATE", DEFAULT_LIVE_TEMPLATE).replace(
    "\\n", "\n"
)

ARCHIVE_URL = os.getenv(
    "ARCHIVE_URL", "https://assets.chunt.org/mixcloud_archive.slim.json"
)
ARCHIVE_CHECK_INTERVAL = int(os.getenv("ARCHIVE_CHECK_INTERVAL", "3600"))
ARCHIVE_STATE_FILE = Path(os.getenv("ARCHIVE_STATE_FILE", ".wombott_last_archive"))
DEFAULT_ARCHIVE_TEMPLATE = "{show}"
ARCHIVE_MESSAGE_TEMPLATE = os.getenv("ARCHIVE_MESSAGE_TEMPLATE", DEFAULT_ARCHIVE_TEMPLATE).replace(
    "\\n", "\n"
)


def fetch_now_playing() -> List[Dict]:
    resp = httpx.get(API_URL, timeout=15)
    resp.raise_for_status()
    return resp.json()


def should_notify(show: dict) -> bool:
    is_restream = show.get("restream", False)
    is_not_live = show.get("not_live", False)

    if not is_restream and not is_not_live:
        return True
    if is_restream and NOTIFY_RESTREAMS:
        return True
    if is_not_live and NOTIFY_NOT_LIVE:
        return True
    return False


def build_show_block(show: dict) -> str:
    """Build show info block dynamically from available fields."""
    lines = []

    title = show.get("title")
    if title:
        lines.append("<b>%s</b>" % title)

    start_raw = show.get("start")
    if start_raw:
        try:
            start_dt = datetime.strptime(start_raw, "%Y-%m-%dT%H:%M:%S%z")
            lines.append("Started: %s" % start_dt.strftime("%H:%M UTC%z"))
        except ValueError:
            lines.append("Started: %s" % start_raw)

    duration_secs = show.get("duration")
    if duration_secs:
        lines.append("Duration: ~%d min" % round(duration_secs / 60))

    description = show.get("description")
    if description:
        lines.append(description)

    show_url = show.get("show_url")
    if show_url:
        lines.append(show_url)

    return "\n".join(lines)


def format_message(show: dict) -> str:
    show_block = build_show_block(show)
    return LIVE_MESSAGE_TEMPLATE.format(show=show_block)


def build_archive_block(entry: dict) -> str:
    """Build archive show info block dynamically from available fields."""
    lines = []

    info = entry.get("info", {})
    title = info.get("title") or entry.get("name")
    if title:
        lines.append("<b>%s</b>" % title)

    date = info.get("date")
    if date:
        lines.append("Date: %s" % date)

    audio_length = entry.get("audio_length")
    if audio_length:
        lines.append("Duration: ~%d min" % round(audio_length / 60))

    tags = info.get("tags")
    if tags:
        lines.append("Tags: %s" % ", ".join(tags))

    url = entry.get("url")
    if url:
        lines.append(url)

    return "\n".join(lines)


def format_archive_message(entry: dict) -> str:
    show_block = build_archive_block(entry)
    return ARCHIVE_MESSAGE_TEMPLATE.format(show=show_block)


def read_posted_archive_urls() -> set:
    if ARCHIVE_STATE_FILE.exists():
        text = ARCHIVE_STATE_FILE.read_text().strip()
        if text:
            return set(text.splitlines())
    return set()


def write_posted_archive_urls(urls: set) -> None:
    ARCHIVE_STATE_FILE.write_text("\n".join(sorted(urls)))


def check_archive() -> None:
    resp = httpx.get(ARCHIVE_URL, timeout=30)
    resp.raise_for_status()
    archive = resp.json()

    posted_urls = read_posted_archive_urls()

    # find entries not yet posted
    all_urls = {e["url"] for e in archive if e.get("url")}
    new_entries = [e for e in archive if e.get("url") and e["url"] not in posted_urls]

    if not new_entries:
        log.info("No new archive entries.")
        return

    # post oldest first so channel reads chronologically
    new_entries.sort(key=lambda e: e.get("created_time", ""))
    for entry in new_entries:
        log.info("New archive entry: %s", entry.get("name"))
        send_telegram_message(format_archive_message(entry))

    # update state: all archive urls (auto-prunes removed entries)
    posted_urls = (posted_urls & all_urls) | {e["url"] for e in new_entries}
    write_posted_archive_urls(posted_urls)


def send_telegram_message(text: str) -> None:
    resp = httpx.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML",
        },
        timeout=15,
    )
    if resp.is_success:
        log.info("Telegram message sent.")
    else:
        log.error("Telegram API error: %s %s", resp.status_code, resp.text)


def main() -> None:
    log.info(
        "Starting wombott -- polling %s every %ds, archive check every %ds",
        API_URL, POLL_INTERVAL, ARCHIVE_CHECK_INTERVAL,
    )

    last_seen_state = None  # type: Optional[tuple]
    pending_show = None  # type: Optional[dict]
    pending_state = None  # type: Optional[tuple]
    pending_since = 0.0
    last_archive_check = 0.0

    while True:
        # live show check
        try:
            shows = fetch_now_playing()
            if shows:
                show = shows[0]
                current_state = (
                    show.get("title"),
                    show.get("restream", False),
                    show.get("not_live", False),
                )
            else:
                show = None
                current_state = None

            if current_state != last_seen_state:
                log.info("State changed: %s -> %s", last_seen_state, current_state)
                last_seen_state = current_state

                if show and should_notify(show):
                    # start confirmation timer
                    log.info("Pending live show: %s (confirming for %ds)", show.get("title"), CONFIRM_SECONDS)
                    pending_show = show
                    pending_state = current_state
                    pending_since = time.time()
                else:
                    # state changed to something non-notifiable, clear pending
                    pending_show = None
                    pending_state = None

            # check if pending show has been live long enough
            if pending_show and current_state == pending_state:
                elapsed = time.time() - pending_since
                if elapsed >= CONFIRM_SECONDS:
                    log.info("Confirmed live show: %s (live for %ds)", pending_show.get("title"), int(elapsed))
                    send_telegram_message(format_message(pending_show))
                    pending_show = None
                    pending_state = None

        except httpx.HTTPError as exc:
            log.error("API request failed: %s", exc)
        except Exception:
            log.exception("Unexpected error in live show poll")

        # archive check (on its own interval)
        if time.time() - last_archive_check >= ARCHIVE_CHECK_INTERVAL:
            try:
                check_archive()
            except httpx.HTTPError as exc:
                log.error("Archive request failed: %s", exc)
            except Exception:
                log.exception("Unexpected error in archive check")
            last_archive_check = time.time()

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
