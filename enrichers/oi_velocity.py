"""
OI velocity tracker: stores hourly OI snapshots and surfaces
strikes with large buildup or unwinding since the last snapshot.
"""
import sqlite3
import logging
from datetime import datetime
from config import db, DB_PATH, IST
from nse.client import option_chain

log = logging.getLogger(__name__)

SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY"]


def snapshot(symbols: list[str] = SYMBOLS) -> dict:
    """Fetch current OI for all strikes and store in oi_snapshots table."""
    now = datetime.now(IST).isoformat()
    stored = {}
    with db() as conn:
        for sym in symbols:
            oc = option_chain(sym)
            if not oc:
                log.warning("OC unavailable for %s", sym)
                continue
            expiry = oc["expiry"]
            count  = 0
            for s in oc["strikes"]:
                for side in ("CE", "PE"):
                    oi  = s[f"{side.lower()}_oi"]
                    chg = s[f"{side.lower()}_chg_oi"]
                    ltp = s[f"{side.lower()}_ltp"]
                    conn.execute("""
                        INSERT INTO oi_snapshots
                          (symbol, expiry, strike, opt_type, oi, chg_in_oi, ltp, snapshot_time)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (sym, expiry, s["strike"], side, oi, chg, ltp, now))
                    count += 1
            conn.commit()
            stored[sym] = count
            log.info("OI snapshot: %s — %d strikes saved", sym, count)
    return stored


def velocity_alerts(symbols: list[str] = SYMBOLS, top_n: int = 5,
                    min_pct: float = 25.0) -> dict:
    """
    Compare latest two snapshots per symbol.
    Return strikes with OI change ≥ min_pct % between snapshots.
    """
    alerts = {}
    with db() as conn:
        for sym in symbols:
            # Get last two snapshot timestamps
            times = conn.execute("""
                SELECT DISTINCT snapshot_time FROM oi_snapshots
                WHERE symbol = ? ORDER BY snapshot_time DESC LIMIT 2
            """, (sym,)).fetchall()
            if len(times) < 2:
                continue
            t_now, t_prev = times[0][0], times[1][0]

            rows = conn.execute("""
                SELECT a.strike, a.opt_type,
                       a.oi as oi_now, b.oi as oi_prev,
                       a.ltp,
                       ROUND((a.oi - b.oi) / NULLIF(b.oi, 0) * 100, 1) as pct_chg
                FROM oi_snapshots a
                JOIN oi_snapshots b
                  ON a.symbol=b.symbol AND a.expiry=b.expiry
                 AND a.strike=b.strike AND a.opt_type=b.opt_type
                 AND b.snapshot_time=?
                WHERE a.symbol=? AND a.snapshot_time=?
                  AND ABS((a.oi - b.oi) / NULLIF(b.oi, 0) * 100) >= ?
                ORDER BY ABS(pct_chg) DESC
                LIMIT ?
            """, (t_prev, sym, t_now, min_pct, top_n)).fetchall()

            if rows:
                alerts[sym] = [
                    {"strike": r[0], "type": r[1],
                     "oi_now": r[2], "oi_prev": r[3],
                     "ltp": r[4], "pct_chg": r[5]}
                    for r in rows
                ]
    return alerts


def format_oi_velocity(alerts: dict) -> str | None:
    if not alerts:
        return None
    lines = ["📊 <b>OI VELOCITY</b> — Large OI shifts since last hour\n"]
    for sym, rows in alerts.items():
        lines.append(f"<b>{sym}</b>")
        for r in rows:
            action = "📈 BUILDUP" if r["pct_chg"] > 0 else "📉 UNWIND"
            em     = "🐻" if r["type"] == "PE" and r["pct_chg"] > 0 else (
                     "🐂" if r["type"] == "CE" and r["pct_chg"] > 0 else "")
            lines.append(
                f"  {em} {r['strike']}{r['type']}  {action}  "
                f"{r['pct_chg']:+.1f}%  OI {r['oi_now']:,.0f}  LTP {r['ltp']}"
            )
    return "\n".join(lines)
