"""
Macro economic calendar -- high-impact events for the next N days.

Source: ForexFactory public JSON feed (no API key required).
Filters to USD and INR events with HIGH impact only.

Converts all event times from US/Eastern to IST for display.
"""
import logging
import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from config import IST

log = logging.getLogger(__name__)

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
WATCH_COUNTRIES = {"USD", "INR"}
WATCH_IMPACTS   = {"High"}

# Countries whose events materially move Indian markets
COUNTRY_LABELS = {
    "USD": "US",
    "INR": "IN",
    "EUR": "EU",
    "GBP": "UK",
    "JPY": "JP",
    "CNY": "CN",
}

ET = ZoneInfo("America/New_York")   # ForexFactory times are US/Eastern


def get_upcoming(days_ahead: int = 2) -> list[dict]:
    """
    Returns high-impact USD and INR events in the next `days_ahead` days.
    Times converted to IST.
    """
    try:
        r = requests.get(CALENDAR_URL, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        log.warning("macro_calendar fetch failed: %s", e)
        return []

    now_ist   = datetime.now(IST)
    cutoff    = now_ist + timedelta(days=days_ahead)
    events    = []

    for ev in raw:
        if ev.get("impact") not in WATCH_IMPACTS:
            continue
        if ev.get("country") not in WATCH_COUNTRIES:
            continue
        try:
            # ForexFactory dates include UTC offset (e.g. -04:00 for EDT)
            dt_raw = datetime.fromisoformat(ev["date"])
            if dt_raw.tzinfo is None:
                dt_raw = dt_raw.replace(tzinfo=ET)
            dt_ist = dt_raw.astimezone(IST)
        except Exception:
            continue

        if dt_ist < now_ist or dt_ist > cutoff:
            continue

        events.append({
            "title":    ev.get("title", ""),
            "country":  ev.get("country", ""),
            "dt_ist":   dt_ist,
            "forecast": ev.get("forecast", ""),
            "previous": ev.get("previous", ""),
        })

    events.sort(key=lambda x: x["dt_ist"])
    return events


def format_macro_events(events: list[dict]) -> str:
    if not events:
        return ""
    lines = ["[DATE] <b>MACRO EVENTS (next 48h, high impact)</b>"]
    for ev in events:
        country_label = COUNTRY_LABELS.get(ev["country"], ev["country"])
        time_str      = ev["dt_ist"].strftime("%a %H:%M IST")
        title         = ev["title"]
        parts         = [f"  [WARN] {country_label}  <b>{title}</b>  [{time_str}]"]
        if ev["forecast"]:
            parts[0] += f"  fcst {ev['forecast']}"
        if ev["previous"]:
            parts[0] += f"  prev {ev['previous']}"
        lines.append(parts[0])
    return "\n".join(lines)
