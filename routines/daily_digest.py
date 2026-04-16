"""Daily digest: posts weather, schedule, and a chunted fortune to Telegram."""

import logging
import os
import sys
from datetime import datetime, timezone, timedelta

import httpx
from dotenv import load_dotenv

from routines.quotes import random_chunted_fortune

# load .env from project root (one level up from routines/)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("daily_digest")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

WEATHER_LOCATION = os.getenv("WEATHER_LOCATION", "Skegness")
SCHEDULE_API_URL = os.getenv("SCHEDULE_API_URL", "https://api.chunt.org/schedule/what")

UK_TZ = timezone(timedelta(hours=0))  # UTC; BST handled by schedule API's dateUK field


def fetch_weather() -> str:
    """Fetch compact weather string from wttr.in."""
    url = f"https://wttr.in/{WEATHER_LOCATION}?format=%C+%t+%w"
    resp = httpx.get(url, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    return resp.text.strip()


def fetch_schedule() -> str:
    """Fetch today's schedule and format as a list of shows."""
    now_utc = datetime.now(timezone.utc)
    today_date = now_utc.date()
    today = now_utc.strftime("%Y-%m-%d")
    resp = httpx.get(
        SCHEDULE_API_URL,
        params={"time": today},
        timeout=15,
    )
    resp.raise_for_status()
    shows = resp.json()

    if not shows:
        return "No shows scheduled today."

    lines = []
    for show in shows:
        start_raw = show.get("startTimestampUTC", "")
        end_raw = show.get("endTimestampUTC", "")
        try:
            start_dt = datetime.fromisoformat(start_raw)
            start = start_dt.strftime("%H:%M")
        except ValueError:
            start_dt = None
            start = "?"
        try:
            end_dt = datetime.fromisoformat(end_raw)
            end = end_dt.strftime("%H:%M")
        except ValueError:
            end_dt = None
            end = "?"

        # label shows that span midnight
        day_prefix = ""
        if start_dt and start_dt.date() < today_date:
            day_prefix = "(yesterday) "
        elif start_dt and start_dt.date() > today_date:
            day_prefix = "(tomorrow) "

        day_suffix = ""
        if end_dt and end_dt.date() > today_date and start_dt and start_dt.date() == today_date:
            day_suffix = " (+1d)"

        title = show.get("title", "Unknown")
        lines.append(f"{day_prefix}{start}-{end}{day_suffix} {title}")

    return "\n".join(lines)


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
        log.info("Digest message sent.")
    else:
        log.error("Telegram API error: %s %s", resp.status_code, resp.text)


def main() -> None:
    log.info("Building daily digest...")

    weather = fetch_weather()
    schedule = fetch_schedule()
    fortune = random_chunted_fortune()

    message = f"gm!\n\n<b>today's weather:</b>\n{weather}\n\n<b>today's schedule (UTC):</b>\n{schedule}\n\nremember: <i>{fortune}</i>"

    send_telegram_message(message)
    log.info("Done.")


if __name__ == "__main__":
    main()
