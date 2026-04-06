#!/usr/bin/env python3
"""
Intraday price monitor — runs every 5 minutes during market hours.

For each OPEN signal logged today:
  BUY : alert if day high >= target (TGT hit) or day low <= SL (SL breach)
  SELL: alert if day low  <= target (TGT hit) or day high >= SL (SL breach)

Each event fires exactly once — tracked in signal_log.intraday_alerts (JSON).
Run via cron every 5 minutes between 9:15 AM and 3:30 PM IST, Mon-Fri.
"""
import sys
import json
import time
import sqlite3
import logging
import argparse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import db, DB_PATH, IST, BOT_TOKEN, OWNER_CHAT_ID, is_market_open
from nse import client as nse
from signals.extractor import base_symbol, is_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monitor] %(levelname)s %(message)s",
)
log = logging.getLogger("price_monitor")

MARKET_OPEN  = (9, 15)
MARKET_CLOSE = (15, 30)


# -- Helpers ------------------------------------------------------------------

def is_market_hours() -> bool:
    now = datetime.now(IST)
    if not is_market_open(now):
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE


def send_alert(text: str) -> bool:
    if not BOT_TOKEN or not OWNER_CHAT_ID:
        return False
    try:
        import requests as req
        r = req.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": OWNER_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        log.error("Alert send failed: %s", e)
        return False


def _init_col(conn):
    """Add intraday_alerts column to signal_log if not present."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signal_log)")}
    if "intraday_alerts" not in cols:
        conn.execute("ALTER TABLE signal_log ADD COLUMN intraday_alerts TEXT DEFAULT '{}'")
        conn.commit()
        log.info("Added intraday_alerts column to signal_log")


def _load_alerts(raw: str | None) -> dict:
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


# -- Core check ---------------------------------------------------------------

def check_signals(dry_run: bool = False) -> int:
    """Check all open signals for today. Returns count of alerts fired."""
    now      = datetime.now(IST)
    date_str = now.strftime("%Y-%m-%d")

    COLS = ["id", "channel", "instrument", "direction",
            "entry", "sl", "targets", "intraday_alerts"]

    with db() as conn:
        _init_col(conn)
        rows = conn.execute("""
            SELECT id, channel, instrument, direction,
                   entry, sl, targets, intraday_alerts
            FROM signal_log
            WHERE date = ? AND result = 'OPEN'
              AND direction IN ('BUY', 'SELL')
        """, (date_str,)).fetchall()

    if not rows:
        log.info("No open signals for %s", date_str)
        return 0

    sigs = [dict(zip(COLS, r)) for r in rows]
    log.info("%d open signals to check", len(sigs))

    # -- Fetch live prices --
    nse.init()
    idx   = nse.all_indices()
    nifty = idx.get("NIFTY 50", {})
    bnf   = idx.get("NIFTY BANK", {})

    stock_syms = {
        base_symbol(s["instrument"]) for s in sigs
        if not is_index(s["instrument"])
    }
    quotes = {}
    for sym in sorted(stock_syms):
        time.sleep(0.5)   # NSE recommends ≥0.5s between requests
        q = nse.quote(sym)
        if q and q.get("ltp"):
            quotes[sym] = q

    alerts_fired = 0

    with db() as conn:
        _init_col(conn)
        for s in sigs:
            sym   = base_symbol(s["instrument"])
            dirn  = s["direction"]

            # Resolve live price data
            if sym in quotes:
                q = quotes[sym]
            elif sym == "NIFTY":
                q = {"ltp": nifty.get("last"), "high": nifty.get("high"), "low": nifty.get("low")}
            elif sym in ("BANKNIFTY", "BNF"):
                q = {"ltp": bnf.get("last"), "high": bnf.get("high"), "low": bnf.get("low")}
            else:
                continue

            ltp  = q.get("ltp")
            high = q.get("high") or ltp
            low  = q.get("low")  or ltp

            if not ltp:
                continue

            sl      = s["sl"]
            try:
                targets = json.loads(s["targets"] or "[]")
            except (json.JSONDecodeError, TypeError):
                log.warning("Corrupted targets JSON for signal rowid=%s instrument=%s — skipping",
                            s.get("rowid"), s.get("instrument"))
                targets = []
            alerted = _load_alerts(s["intraday_alerts"])
            changed = False
            msgs    = []

            # -- SL breach check --
            if sl and not alerted.get("sl"):
                sl_hit = (dirn == "BUY"  and low  <= sl) or \
                         (dirn == "SELL" and high >= sl)
                if sl_hit:
                    alerted["sl"] = now.strftime("%H:%M")
                    changed = True
                    msgs.append(
                        f"[ALERT] <b>SL HIT</b> -- {dirn} <b>{s['instrument']}</b>\n"
                        f"  SL {sl}  |  LTP {ltp}  |  [{s['channel']}]"
                    )

            # -- Target hit checks --
            for i, tgt in enumerate(targets, 1):
                if not tgt:
                    continue
                key = f"tgt{i}"
                if alerted.get(key):
                    continue
                tgt_hit = (dirn == "BUY"  and high >= tgt) or \
                          (dirn == "SELL" and low  <= tgt)
                if tgt_hit:
                    alerted[key] = now.strftime("%H:%M")
                    changed = True
                    msgs.append(
                        f"[OK] <b>TARGET {i} HIT</b> -- {dirn} <b>{s['instrument']}</b>\n"
                        f"  TGT{i} {tgt}  |  LTP {ltp}  |  [{s['channel']}]"
                    )

            if changed:
                conn.execute(
                    "UPDATE signal_log SET intraday_alerts=? WHERE id=?",
                    (json.dumps(alerted), s["id"])
                )
                conn.commit()

            for msg in msgs:
                log.info("Alert: %s", msg.replace("\n", " "))
                if not dry_run:
                    send_alert(msg)
                alerts_fired += 1

    return alerts_fired


# -- Main ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Check prices and log alerts but do not send to Telegram")
    parser.add_argument("--force",   action="store_true",
                        help="Run even outside market hours (for testing)")
    args = parser.parse_args()

    if not args.force and not is_market_hours():
        log.info("Outside market hours -- exiting (use --force to override)")
        return 0

    log.info("=== Price monitor starting ===")
    fired = check_signals(dry_run=args.dry_run)
    log.info("=== Done: %d alert(s) fired ===", fired)
    return 0


if __name__ == "__main__":
    sys.exit(main())
