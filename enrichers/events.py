"""
Corporate event overlay: earnings, dividends, splits, rights.
Flags signals where the stock has a corporate action within N days.
"""
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from config import db, DB_PATH, IST
from nse.client import corporate_actions

log = logging.getLogger(__name__)


def refresh_events(symbols: list[str], days_ahead: int = 7) -> int:
    """Fetch and store upcoming corporate events for a list of symbols."""
    now      = datetime.now(IST).isoformat()
    stored   = 0
    with db() as conn:
        for sym in symbols:
            for ev in corporate_actions(sym):
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO corporate_events (symbol, ex_date, purpose)
                        VALUES (?,?,?)
                    """, (sym, ev.get("ex_date",""), ev.get("purpose","")))
                    stored += 1
                except Exception:
                    pass
        conn.commit()
    log.info("Corporate events: %d rows stored for %d symbols", stored, len(symbols))
    return stored


def get_events_for(symbols: list[str], days_ahead: int = 5) -> dict[str, list[dict]]:
    """Return upcoming events (within days_ahead) keyed by symbol."""
    today   = datetime.now(IST).date()
    cutoff  = today + timedelta(days=days_ahead)
    result  = {}

    with db() as conn:
        for sym in symbols:
            rows = conn.execute("""
                SELECT ex_date, purpose FROM corporate_events
                WHERE symbol=?
                ORDER BY ex_date
            """, (sym.upper(),)).fetchall()
            events = []
            for ex_date_str, purpose in rows:
                try:
                    ex = datetime.strptime(ex_date_str, "%d-%b-%Y").date()
                    if today <= ex <= cutoff:
                        days_away = (ex - today).days
                        events.append({"ex_date": ex_date_str, "purpose": purpose,
                                       "days_away": days_away})
                except Exception:
                    continue
            if events:
                result[sym] = sorted(events, key=lambda x: x["days_away"])

    return result


def format_event_flag(symbol: str, events: list[dict]) -> str:
    """Return a compact warning line for a signal with upcoming events."""
    if not events:
        return ""
    ev   = events[0]
    days = ev["days_away"]
    when = "TODAY" if days == 0 else ("TOMORROW" if days == 1 else f"in {days}d")
    return f"  ⚡ {ev['purpose']} {when} ({ev['ex_date']})"
