import logging
import os
import time
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("wombott")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))
API_URL = os.getenv(
    "API_URL", "https://api.chunt.org/fm/channels/1/now-playing"
)
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
NOTIFY_RESTREAMS = os.getenv("NOTIFY_RESTREAMS", "false").lower() == "true"
NOTIFY_NOT_LIVE = os.getenv("NOTIFY_NOT_LIVE", "false").lower() == "true"

DEFAULT_LIVE_TEMPLATE = """\
<b>{title}</b>

Started: {start}
Duration: ~{duration} min
{description}
{show_url}"""

LIVE_MESSAGE_TEMPLATE = os.getenv("LIVE_MESSAGE_TEMPLATE", DEFAULT_LIVE_TEMPLATE)


def fetch_now_playing() -> list[dict]:
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


def format_message(show: dict) -> str:
    title = show.get("title", "Unknown")
    description = show.get("description") or ""
    show_url = show.get("show_url") or ""
    duration_secs = show.get("duration")
    start_raw = show.get("start")

    start_str = ""
    if start_raw:
        try:
            start_dt = datetime.fromisoformat(start_raw)
            start_str = start_dt.strftime("%H:%M UTC%z")
        except ValueError:
            start_str = start_raw

    duration_str = str(round(duration_secs / 60)) if duration_secs else ""

    text = LIVE_MESSAGE_TEMPLATE.format(
        title=title,
        description=description,
        show_url=show_url,
        start=start_str,
        duration=duration_str,
    )

    # collapse blank lines from empty fields
    lines = text.split("\n")
    cleaned = [line for line in lines if line.strip()]
    return "\n".join(cleaned)


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
        "Starting wombott -- polling %s every %ds", API_URL, POLL_INTERVAL
    )

    last_seen_title: str | None = None

    while True:
        try:
            shows = fetch_now_playing()
            current_title = shows[0].get("title") if shows else None

            if current_title != last_seen_title:
                log.info("Now playing changed: %s -> %s", last_seen_title, current_title)
                last_seen_title = current_title

                for show in shows:
                    if should_notify(show):
                        log.info("New show detected: %s", show.get("title"))
                        send_telegram_message(format_message(show))

        except httpx.HTTPError as exc:
            log.error("API request failed: %s", exc)
        except Exception:
            log.exception("Unexpected error in poll loop")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
