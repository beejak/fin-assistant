"""
Market regime: daily snapshot of macro context.

Stored once per day at EOD. Read at pre-open to frame the day.
Used by the hourly report to adjust confidence language.

Regime is determined by three axes:
  TREND:     BULLISH / BEARISH / SIDEWAYS   (5-day FII net + index direction)
  VOLATILITY: HIGH / NORMAL                 (VIX vs 18 threshold)
  FLOW:       FII_BUYING / FII_SELLING / NEUTRAL

These three combine into a one-line market context string.
"""
import sqlite3
import logging
from datetime import datetime

from config import db, DB_PATH, IST

log = logging.getLogger(__name__)

VIX_HIGH_THRESHOLD = 18.0
FII_TREND_WINDOW = 5        # days to average FII net
FII_SIGNIFICANT = 500       # crores -- net above/below this = directional


def _init_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_regime (
            date        TEXT PRIMARY KEY,
            vix         REAL,
            vix_label   TEXT,
            fii_net_5d  REAL,
            flow_label  TEXT,
            nifty_close REAL,
            nifty_5d_pct REAL,
            trend_label TEXT,
            regime_text TEXT,
            recorded_at TEXT
        )
    """)


def snapshot(vix: float | None, nifty_close: float | None) -> dict:
    """
    Compute and store today's regime. Call from EOD grader.
    Returns the regime dict.
    """
    now      = datetime.now(IST)
    date_str = now.strftime("%Y-%m-%d")

    with db() as conn:
        _init_table(conn)

        # FII net over last N days
        fii_rows = conn.execute("""
            SELECT fii_net FROM fii_dii_daily
            WHERE date <= ? ORDER BY date DESC LIMIT ?
        """, (date_str, FII_TREND_WINDOW)).fetchall()

        fii_net_5d = sum(r[0] for r in fii_rows if r[0] is not None) if fii_rows else None

        # Nifty 5-day % change
        nifty_rows = conn.execute("""
            SELECT nifty_close FROM market_regime
            WHERE date < ? ORDER BY date DESC LIMIT ?
        """, (date_str, FII_TREND_WINDOW)).fetchall()

        nifty_5d_pct = None
        if nifty_close and nifty_rows and nifty_rows[-1][0]:
            old_close = nifty_rows[-1][0]
            nifty_5d_pct = round((nifty_close - old_close) / old_close * 100, 2)

    # Classify
    if vix is None:
        vix_label = "UNKNOWN"
    elif vix > VIX_HIGH_THRESHOLD:
        vix_label = "HIGH"
    else:
        vix_label = "NORMAL"

    if fii_net_5d is None:
        flow_label = "UNKNOWN"
    elif fii_net_5d > FII_SIGNIFICANT:
        flow_label = "FII_BUYING"
    elif fii_net_5d < -FII_SIGNIFICANT:
        flow_label = "FII_SELLING"
    else:
        flow_label = "NEUTRAL"

    if nifty_5d_pct is None:
        trend_label = "UNKNOWN"
    elif nifty_5d_pct > 1.0:
        trend_label = "BULLISH"
    elif nifty_5d_pct < -1.0:
        trend_label = "BEARISH"
    else:
        trend_label = "SIDEWAYS"

    # Compose human-readable regime text
    regime_parts = []
    if trend_label in ("BULLISH", "BEARISH"):
        regime_parts.append(trend_label)
    else:
        regime_parts.append("SIDEWAYS/CHOPPY")
    if vix_label == "HIGH":
        regime_parts.append("High volatility")
    if flow_label == "FII_BUYING":
        regime_parts.append("FII net buying")
    elif flow_label == "FII_SELLING":
        regime_parts.append("FII net selling")

    regime_text = "  |  ".join(regime_parts) if regime_parts else "Neutral"

    record = {
        "date": date_str,
        "vix": vix,
        "vix_label": vix_label,
        "fii_net_5d": fii_net_5d,
        "flow_label": flow_label,
        "nifty_close": nifty_close,
        "nifty_5d_pct": nifty_5d_pct,
        "trend_label": trend_label,
        "regime_text": regime_text,
        "recorded_at": now.isoformat(),
    }

    with db() as conn:
        _init_table(conn)
        conn.execute("""
            INSERT INTO market_regime
              (date, vix, vix_label, fii_net_5d, flow_label,
               nifty_close, nifty_5d_pct, trend_label, regime_text, recorded_at)
            VALUES (:date,:vix,:vix_label,:fii_net_5d,:flow_label,
                    :nifty_close,:nifty_5d_pct,:trend_label,:regime_text,:recorded_at)
            ON CONFLICT(date) DO UPDATE SET
              vix=excluded.vix, vix_label=excluded.vix_label,
              fii_net_5d=excluded.fii_net_5d, flow_label=excluded.flow_label,
              nifty_close=excluded.nifty_close, nifty_5d_pct=excluded.nifty_5d_pct,
              trend_label=excluded.trend_label, regime_text=excluded.regime_text,
              recorded_at=excluded.recorded_at
        """, record)

    log.info("Market regime recorded: %s", regime_text)
    return record


def get_latest() -> dict | None:
    """Load the most recent stored regime."""
    try:
        with db() as conn:
            _init_table(conn)
            row = conn.execute("""
                SELECT date, vix, vix_label, fii_net_5d, flow_label,
                       nifty_close, nifty_5d_pct, trend_label, regime_text
                FROM market_regime ORDER BY date DESC LIMIT 1
            """).fetchone()
        if not row:
            return None
        keys = ["date","vix","vix_label","fii_net_5d","flow_label",
                "nifty_close","nifty_5d_pct","trend_label","regime_text"]
        return dict(zip(keys, row))
    except sqlite3.OperationalError:
        return None


def format_regime_line(regime: dict | None) -> str:
    """One-line regime summary for report headers."""
    if not regime:
        return ""
    em_map = {
        "BULLISH": "[+]", "BEARISH": "[-]", "SIDEWAYS": "[=]",
        "UNKNOWN": "[?]"
    }
    trend_em = em_map.get(regime.get("trend_label", "UNKNOWN"), "[=]")
    vix_tag = "[!] High VIX  " if regime.get("vix_label") == "HIGH" else ""
    flow_map = {"FII_BUYING": "$ FII buying  ", "FII_SELLING": "$- FII selling  ", "NEUTRAL": ""}
    flow_tag = flow_map.get(regime.get("flow_label", ""), "")
    pct = regime.get("nifty_5d_pct")
    pct_str = f"  ({pct:+.1f}% 5d)" if pct is not None else ""
    return f"[UP] Regime: {trend_em} {regime.get('regime_text','')}{pct_str}  {vix_tag}{flow_tag}".rstrip()
