"""
NSE bulk & block deal tracker.
Large institutional trades are strong signals — fund conviction at scale.
"""
import sqlite3
import logging
from datetime import datetime
from config import db, DB_PATH, IST
from nse.client import bulk_deals as fetch_bulk, block_deals as fetch_block

log = logging.getLogger(__name__)

MIN_VALUE_CR = 10  # only care about deals ≥ ₹10cr


def store_today() -> list[dict]:
    date_str = datetime.now(IST).strftime("%Y-%m-%d")
    now      = datetime.now(IST).isoformat()
    all_deals = []

    for source, fn in (("BULK", fetch_bulk), ("BLOCK", fetch_block)):
        deals = fn()
        for d in deals:
            if not d.get("symbol") or not d.get("qty") or not d.get("price"):
                continue
            value_cr = (d["qty"] * d["price"]) / 1e7
            if value_cr < MIN_VALUE_CR:
                continue
            deal_id = f"{source}_{d.get('date','')}_{d.get('symbol','')}_{d.get('client','')}"
            all_deals.append({**d, "source": source, "value_cr": round(value_cr, 1), "id": deal_id})

    if all_deals:
        with db() as conn:
            for d in all_deals:
                conn.execute("""
                    INSERT OR IGNORE INTO bulk_deals
                      (id, date, symbol, client_name, trade_type, quantity, price, fetched_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (d["id"], d.get("date", date_str), d["symbol"],
                      d.get("client"), d.get("type"), d.get("qty"),
                      d.get("price"), now))
            conn.commit()
        log.info("Stored %d bulk/block deals", len(all_deals))

    return all_deals


def get_today(date_str: str | None = None) -> list[dict]:
    if not date_str:
        date_str = datetime.now(IST).strftime("%Y-%m-%d")
    with db() as conn:
        rows = conn.execute("""
            SELECT symbol, client_name, trade_type, quantity, price, date
            FROM bulk_deals WHERE date=?
            ORDER BY quantity * price DESC
        """, (date_str,)).fetchall()
    return [{"symbol": r[0], "client": r[1], "type": r[2],
             "qty": r[3], "price": r[4], "date": r[5],
             "value_cr": round((r[3] or 0) * (r[4] or 0) / 1e7, 1)} for r in rows]


def format_bulk_deals(deals: list[dict]) -> str | None:
    if not deals:
        return None
    lines = ["🐋 <b>BULK / BLOCK DEALS</b>  (institutional trades today)\n"]
    for d in deals[:10]:
        em = "🟢" if (d.get("type") or "").upper() == "BUY" else "🔴"
        lines.append(
            f"{em} <b>{d['symbol']}</b>  {d.get('type','')}  "
            f"₹{d['value_cr']:.0f}cr  @ {d['price']}  |  {d.get('client','?')[:30]}"
        )
    return "\n".join(lines)
