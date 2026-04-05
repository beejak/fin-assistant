"""
Instrument stats: per-instrument, per-direction success rate.

Updated at EOD after grading. Read by hourly to annotate signals.

Tells you: "BANKNIFTY BUY calls: 28% hit rate (41 signals)" so you can
weight signals accordingly. Not a prediction -- just historical base rate.
"""
import sqlite3
import logging
from datetime import datetime

from config import db, DB_PATH, IST

log = logging.getLogger(__name__)

WINDOW_DAYS = 30
MIN_SIGNALS = 5   # don't show stats with fewer than this many closed signals


def _init_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instrument_stats (
            instrument  TEXT NOT NULL,
            direction   TEXT NOT NULL,
            total       INTEGER DEFAULT 0,
            hits        INTEGER DEFAULT 0,
            sl_hits     INTEGER DEFAULT 0,
            hit_rate    REAL,
            updated_at  TEXT,
            PRIMARY KEY (instrument, direction)
        )
    """)


def update() -> dict:
    """
    Recompute stats from signal_log for the last WINDOW_DAYS.
    Returns dict keyed by (instrument, direction).
    """
    with db() as conn:
        _init_table(conn)
        rows = conn.execute("""
            SELECT instrument, direction,
                   COUNT(*) AS total,
                   SUM(CASE WHEN result LIKE 'TGT%' THEN 1 ELSE 0 END) AS hits,
                   SUM(CASE WHEN result = 'SL_HIT'  THEN 1 ELSE 0 END) AS sl_hits
            FROM signal_log
            WHERE date >= DATE('now', ?)
              AND result != 'OPEN'
              AND direction IS NOT NULL
            GROUP BY instrument, direction
        """, (f"-{WINDOW_DAYS} days",)).fetchall()

    stats = {}
    now_iso = datetime.now(IST).isoformat()
    with db() as conn:
        _init_table(conn)
        for instrument, direction, total, hits, sl_hits in rows:
            closed   = hits + sl_hits
            hit_rate = round(hits / closed * 100, 1) if closed > 0 else None
            key = (instrument, direction)
            stats[key] = {
                "total": total, "hits": hits, "sl_hits": sl_hits, "hit_rate": hit_rate
            }
            conn.execute("""
                INSERT INTO instrument_stats
                  (instrument, direction, total, hits, sl_hits, hit_rate, updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(instrument, direction) DO UPDATE SET
                  total=excluded.total, hits=excluded.hits, sl_hits=excluded.sl_hits,
                  hit_rate=excluded.hit_rate, updated_at=excluded.updated_at
            """, (instrument, direction, total, hits, sl_hits, hit_rate, now_iso))

    log.info("Instrument stats updated -- %d instrument/direction pairs", len(stats))
    return stats


def get_stat(instrument: str, direction: str) -> dict | None:
    """Fetch stat for one instrument+direction. Returns None if not enough data."""
    try:
        with db() as conn:
            _init_table(conn)
            row = conn.execute("""
                SELECT total, hits, sl_hits, hit_rate
                FROM instrument_stats
                WHERE instrument=? AND direction=?
            """, (instrument, direction)).fetchone()
        if not row:
            return None
        total, hits, sl_hits, hit_rate = row
        closed = hits + sl_hits
        if closed < MIN_SIGNALS:
            return None    # not enough history to be meaningful
        return {"total": total, "hits": hits, "sl_hits": sl_hits,
                "hit_rate": hit_rate, "closed": closed}
    except sqlite3.OperationalError:
        return None


def format_stat_line(instrument: str, direction: str) -> str:
    """Returns a short annotation string, or '' if no data."""
    s = get_stat(instrument, direction)
    if not s or s["hit_rate"] is None:
        return ""
    rate = s["hit_rate"]
    em   = "[+]" if rate >= 60 else ("[~]" if rate >= 40 else "[-]")
    return f"{em} {direction} hist: {rate:.0f}% hit ({s['closed']} signals, {WINDOW_DAYS}d)"
