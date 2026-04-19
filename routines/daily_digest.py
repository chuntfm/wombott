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
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        return resp.text.strip()
    except httpx.HTTPError as exc:
        log.warning("Weather fetch failed: %s", exc)
        return "(weather unavailable)"


def _fetch_schedule_for(date_str: str) -> list:
    resp = httpx.get(SCHEDULE_API_URL, params={"time": date_str}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _format_show_line(show: dict) -> str:
    start_raw = show.get("startTimestampUTC", "")
    end_raw = show.get("endTimestampUTC", "")
    try:
        start = datetime.fromisoformat(start_raw).strftime("%H:%M")
    except ValueError:
        start = "?"
    try:
        end = datetime.fromisoformat(end_raw).strftime("%H:%M")
    except ValueError:
        end = "?"
    title = show.get("title", "Unknown")
    return f"{start}-{end} {title}"


def fetch_schedule() -> str:
    """Fetch shows in the 24h window from now to the next digest, grouped by day."""
    now_utc = datetime.now(timezone.utc)
    window_end = now_utc + timedelta(hours=24)
    today_date = now_utc.date()

    try:
        today_shows = _fetch_schedule_for(now_utc.strftime("%Y-%m-%d"))
    except httpx.HTTPError as exc:
        log.warning("Schedule fetch failed (today): %s", exc)
        today_shows = []
    try:
        tomorrow_shows = _fetch_schedule_for((now_utc + timedelta(days=1)).strftime("%Y-%m-%d"))
    except httpx.HTTPError as exc:
        log.warning("Schedule fetch failed (tomorrow): %s", exc)
        tomorrow_shows = []

    if not today_shows and not tomorrow_shows:
        return "(schedule unavailable)"

    # combine, dedupe, keep only shows overlapping the [now, now+24h] window
    seen = set()
    candidates = []
    for show in today_shows + tomorrow_shows:
        sid = show.get("id") or show.get("uid")
        if sid in seen:
            continue
        try:
            start_dt = datetime.fromisoformat(show.get("startTimestampUTC", ""))
            end_dt = datetime.fromisoformat(show.get("endTimestampUTC", ""))
        except ValueError:
            continue
        if end_dt <= now_utc or start_dt >= window_end:
            continue
        seen.add(sid)
        candidates.append((start_dt, end_dt, show))

    candidates.sort(key=lambda c: c[0])

    today_lines = []
    tomorrow_lines = []
    for start_dt, _end_dt, show in candidates:
        line = _format_show_line(show)
        if start_dt.date() <= today_date:
            today_lines.append(line)
        else:
            tomorrow_lines.append(line)

    if not today_lines and not tomorrow_lines:
        return "No shows scheduled in the next 24 hours."

    # only show day headers when both groups have content
    if today_lines and tomorrow_lines:
        return (
            "<b>today:</b>\n" + "\n".join(today_lines) +
            "\n\n<b>tomorrow (morning):</b>\n" + "\n".join(tomorrow_lines)
        )
    return "\n".join(today_lines or tomorrow_lines)


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

    message = f"gm!\n\n<b>today's weather:</b>\n{weather}\n\n<b>next 24h (UTC):</b>\n{schedule}\n\nremember: <i>{fortune}</i>"

    send_telegram_message(message)
    log.info("Done.")


if __name__ == "__main__":
    main()
