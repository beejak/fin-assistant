#!/usr/bin/env python3
"""
Fetch the NSE equity symbol list and store it in the nse_symbols table.

Sources (tried in order):
  1. NSE archives  -- EQUITY_L.csv (full equity list, ~2000 symbols)
  2. yfinance      -- fallback: fetch a small known set to confirm connectivity

Run daily via cron before market open. The table is used for cross-checking
extracted signals and is available for future features (e.g. stock mode).

Usage:
    python3 scripts/refresh_nse_symbols.py
    python3 scripts/refresh_nse_symbols.py --dry-run   # print counts, no DB write
"""
import sys
import csv
import io
import logging
import argparse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import db, IST

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [refresh_nse_symbols] %(levelname)s %(message)s",
)
log = logging.getLogger("refresh_nse_symbols")

NSE_EQUITY_CSV = (
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
)
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.nseindia.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Known Nifty indices to always keep in the table
NIFTY_INDICES = [
    ("NIFTY",       "Nifty 50"),
    ("BANKNIFTY",   "Nifty Bank"),
    ("FINNIFTY",    "Nifty Financial Services"),
    ("MIDCPNIFTY",  "Nifty Midcap Select"),
    ("NIFTYNXT50",  "Nifty Next 50"),
    ("SENSEX",      "BSE Sensex"),
]


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nse_symbols (
            symbol     TEXT PRIMARY KEY,
            name       TEXT,
            isin       TEXT,
            series     TEXT,
            type       TEXT DEFAULT 'equity',   -- equity | index
            updated_at TEXT
        )
    """)


def fetch_equity_csv() -> list[dict]:
    """Download EQUITY_L.csv from NSE archives. Returns list of row dicts."""
    import requests
    log.info("Fetching NSE equity list from archives...")
    try:
        r = requests.get(NSE_EQUITY_CSV, headers=NSE_HEADERS, timeout=30)
        r.raise_for_status()
        text = r.content.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows = []
        for row in reader:
            sym = (row.get("SYMBOL") or row.get(" SYMBOL") or "").strip().upper()
            name = (row.get("NAME OF COMPANY") or "").strip()
            isin = (row.get("ISIN NUMBER") or "").strip()
            series = (row.get("SERIES") or "").strip()
            if sym and series in ("EQ", "BE", "SM", "ST"):
                rows.append({"symbol": sym, "name": name,
                             "isin": isin, "series": series})
        log.info("Fetched %d equity symbols from NSE", len(rows))
        return rows
    except Exception as e:
        log.warning("NSE CSV fetch failed: %s", e)
        return []


def fetch_yfinance_fallback() -> list[dict]:
    """
    Fallback: fetch a handful of well-known symbols via yfinance to confirm
    connectivity. Returns a small list — not a full symbol set.
    """
    try:
        import yfinance as yf
        known = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
                 "SBIN", "BAJFINANCE", "MARUTI", "TITAN", "WIPRO"]
        valid = []
        for sym in known:
            try:
                fi = yf.Ticker(f"{sym}.NS").fast_info
                if fi.last_price:
                    valid.append({"symbol": sym, "name": sym,
                                  "isin": "", "series": "EQ"})
            except Exception:
                pass
        log.info("yfinance fallback: %d symbols confirmed", len(valid))
        return valid
    except Exception as e:
        log.warning("yfinance fallback failed: %s", e)
        return []


def run(dry_run: bool = False) -> int:
    rows = fetch_equity_csv()
    if not rows:
        log.warning("NSE CSV empty — trying yfinance fallback")
        rows = fetch_yfinance_fallback()

    if not rows:
        log.error("No symbols fetched from any source")
        return 1

    now_iso = datetime.now(IST).isoformat()

    if dry_run:
        log.info("Dry-run: would upsert %d equity + %d index symbols",
                 len(rows), len(NIFTY_INDICES))
        return 0

    with db() as conn:
        _ensure_table(conn)

        # Upsert equities
        conn.executemany("""
            INSERT INTO nse_symbols (symbol, name, isin, series, type, updated_at)
            VALUES (:symbol, :name, :isin, :series, 'equity', :ts)
            ON CONFLICT(symbol) DO UPDATE SET
                name       = excluded.name,
                isin       = excluded.isin,
                series     = excluded.series,
                type       = 'equity',
                updated_at = excluded.updated_at
        """, [{**r, "ts": now_iso} for r in rows])

        # Always upsert Nifty indices
        conn.executemany("""
            INSERT INTO nse_symbols (symbol, name, isin, series, type, updated_at)
            VALUES (?, ?, '', 'INDEX', 'index', ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name       = excluded.name,
                type       = 'index',
                updated_at = excluded.updated_at
        """, [(sym, name, now_iso) for sym, name in NIFTY_INDICES])

        total = conn.execute("SELECT COUNT(*) FROM nse_symbols").fetchone()[0]

    log.info("nse_symbols table: %d total symbols (%d equity + %d indices)",
             total, len(rows), len(NIFTY_INDICES))
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
