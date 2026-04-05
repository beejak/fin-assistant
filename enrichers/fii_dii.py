"""
FII / DII daily flow tracker.
Stores in fii_dii_daily table and formats for briefings.
"""
import sqlite3
import logging
from datetime import datetime
from config import db, DB_PATH, IST
from nse.client import fii_dii as fetch_fii_dii

log = logging.getLogger(__name__)


def store_today() -> dict | None:
    data = fetch_fii_dii()
    if not data:
        log.warning("FII/DII data unavailable")
        return None

    date_str = datetime.now(IST).strftime("%Y-%m-%d")
    now      = datetime.now(IST).isoformat()

    with db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO fii_dii_daily
              (date, fii_buy, fii_sell, fii_net, dii_buy, dii_sell, dii_net, fetched_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (date_str,
              data.get("fii_buy"), data.get("fii_sell"), data.get("fii_net"),
              data.get("dii_buy"), data.get("dii_sell"), data.get("dii_net"),
              now))
        conn.commit()

    log.info("FII/DII stored: FII net ₹%.0f cr  DII net ₹%.0f cr",
             data.get("fii_net") or 0, data.get("dii_net") or 0)
    return data


def last_n_days(n: int = 5) -> list[dict]:
    with db() as conn:
        rows = conn.execute("""
            SELECT date, fii_buy, fii_sell, fii_net, dii_buy, dii_sell, dii_net
            FROM fii_dii_daily ORDER BY date DESC LIMIT ?
        """, (n,)).fetchall()
    return [{"date": r[0], "fii_buy": r[1], "fii_sell": r[2], "fii_net": r[3],
             "dii_buy": r[4], "dii_sell": r[5], "dii_net": r[6]} for r in rows]


def format_fii_dii(data: dict | None, history: list[dict] | None = None) -> str:
    if not data:
        return "⚠️ FII/DII data unavailable"

    def cr(v):
        return f"₹{abs(v):,.0f}cr" if v else "N/A"

    fnet = data.get("fii_net") or 0
    dnet = data.get("dii_net") or 0
    fem  = "🔴" if fnet < 0 else "🟢"
    dem  = "🔴" if dnet < 0 else "🟢"

    lines = [
        "🏦 <b>FII / DII FLOWS</b>",
        f"  {fem} FII  Buy {cr(data.get('fii_buy'))}  Sell {cr(data.get('fii_sell'))}  "
        f"Net {'–' if fnet < 0 else '+'}{cr(fnet)}",
        f"  {dem} DII  Buy {cr(data.get('dii_buy'))}  Sell {cr(data.get('dii_sell'))}  "
        f"Net {'–' if dnet < 0 else '+'}{cr(dnet)}",
    ]

    if history and len(history) > 1:
        total_fii = sum((r.get("fii_net") or 0) for r in history)
        total_dii = sum((r.get("dii_net") or 0) for r in history)
        fem5 = "🔴" if total_fii < 0 else "🟢"
        dem5 = "🔴" if total_dii < 0 else "🟢"
        lines.append(
            f"  5-day: FII {fem5}{'–' if total_fii < 0 else '+'}{cr(total_fii)}  "
            f"DII {dem5}{'–' if total_dii < 0 else '+'}{cr(total_dii)}"
        )

    return "\n".join(lines)
