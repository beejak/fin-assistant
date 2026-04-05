"""
Channel scoring: rolling 30-day hit rate per channel.

Updated by EOD grader after each grading run.
Read by hourly scanner to rank and annotate signals.

Score = TGT_HIT / (TGT_HIT + SL_HIT)  -- excludes OPEN signals.
Confidence bands:
  >= 60%  -> HIGH   -- show first, no flag
  40-59% -> MED    -- neutral
  < 40%  -> LOW    -- flag with warning
  < 25% with >=10 closed signals -> suggest mute
"""
import sqlite3
import logging
from datetime import datetime

from config import db, DB_PATH, IST

log = logging.getLogger(__name__)

WINDOW_DAYS = 30
MUTE_THRESHOLD = 25    # % hit rate below which mute is suggested
MUTE_MIN_SIGNALS = 10  # minimum closed signals before suggesting mute


def update() -> dict[str, dict]:
    """
    Recompute channel scores from signal_log and write to channel_scores table.
    Returns scores dict keyed by channel name.
    """
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_scores (
                channel     TEXT PRIMARY KEY,
                total       INTEGER DEFAULT 0,
                hits        INTEGER DEFAULT 0,
                sl_hits     INTEGER DEFAULT 0,
                hit_rate    REAL DEFAULT 0,
                confidence  TEXT DEFAULT 'UNKNOWN',
                suggest_mute INTEGER DEFAULT 0,
                updated_at  TEXT
            )
        """)
        rows = conn.execute("""
            SELECT channel,
                   COUNT(*) AS total,
                   SUM(CASE WHEN result LIKE 'TGT%' THEN 1 ELSE 0 END) AS hits,
                   SUM(CASE WHEN result = 'SL_HIT'  THEN 1 ELSE 0 END) AS sl_hits
            FROM signal_log
            WHERE date >= DATE('now', ?)
              AND result != 'OPEN'
            GROUP BY channel
        """, (f"-{WINDOW_DAYS} days",)).fetchall()

    scores = {}
    now_iso = datetime.now(IST).isoformat()
    with db() as conn:
        for channel, total, hits, sl_hits in rows:
            closed = hits + sl_hits
            hit_rate = round(hits / closed * 100, 1) if closed > 0 else None

            if hit_rate is None:
                confidence = "UNKNOWN"
            elif hit_rate >= 60:
                confidence = "HIGH"
            elif hit_rate >= 40:
                confidence = "MED"
            else:
                confidence = "LOW"

            suggest_mute = (
                hit_rate is not None
                and hit_rate < MUTE_THRESHOLD
                and closed >= MUTE_MIN_SIGNALS
            )

            scores[channel] = {
                "total": total,
                "hits": hits,
                "sl_hits": sl_hits,
                "hit_rate": hit_rate,
                "confidence": confidence,
                "suggest_mute": suggest_mute,
            }

            conn.execute("""
                INSERT INTO channel_scores
                  (channel, total, hits, sl_hits, hit_rate, confidence, suggest_mute, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel) DO UPDATE SET
                  total        = excluded.total,
                  hits         = excluded.hits,
                  sl_hits      = excluded.sl_hits,
                  hit_rate     = excluded.hit_rate,
                  confidence   = excluded.confidence,
                  suggest_mute = excluded.suggest_mute,
                  updated_at   = excluded.updated_at
            """, (channel, total, hits, sl_hits, hit_rate, confidence, int(suggest_mute), now_iso))

    log.info("Channel scores updated -- %d channels", len(scores))
    return scores


def get_all() -> dict[str, dict]:
    """Load current scores from DB. Returns {} if table doesn't exist yet."""
    try:
        with db() as conn:
            rows = conn.execute("""
                SELECT channel, total, hits, sl_hits, hit_rate, confidence, suggest_mute
                FROM channel_scores
            """).fetchall()
        return {
            r[0]: {
                "total": r[1], "hits": r[2], "sl_hits": r[3],
                "hit_rate": r[4], "confidence": r[5], "suggest_mute": bool(r[6]),
            }
            for r in rows
        }
    except sqlite3.OperationalError:
        return {}


def format_score_badge(channel: str, scores: dict) -> str:
    """Return a short badge string for a channel, e.g. '*60% [HIGH]'
    Channel names are NOT included in the badge — caller renders the name
    separately with html.escape() before embedding in Telegram HTML messages.
    """
    s = scores.get(channel)
    if not s or s["hit_rate"] is None:
        return ""
    em = {"HIGH": "*", "MED": "o", "LOW": "v"}.get(s["confidence"], "")
    mute = "  [WARN] consider muting" if s["suggest_mute"] else ""
    return f"{em}{s['hit_rate']:.0f}% [{s['confidence']}]{mute}"
