"""
EOD grader: grades every signal from today against actual NSE highs/lows.
Also fetches and stores FII/DII flows and bulk/block deals for the day.
"""
import json
import sqlite3
import time
import logging
from datetime import datetime, timezone
from collections import defaultdict

from config import db, DB_PATH, IST
from nse import client as nse
from enrichers.fii_dii import store_today as store_fii, last_n_days, format_fii_dii
from enrichers.bulk_deals import store_today as store_bulk, get_today, format_bulk_deals
from learning import channel_scores as ch_scores
from learning import instrument_stats as instr_stats
from learning import market_regime as regime_mod
from bot import send

log = logging.getLogger(__name__)

COLS = ["id","date","channel","instrument","direction","entry",
        "sl","targets","raw_text","sent_at","result","result_note","graded_at"]


def grade_signal(sig: dict, q: dict | None) -> tuple[str, str]:
    """Return (result_code, note) for one signal given its NSE quote."""
    if not q:
        return "OPEN", "no NSE data"

    ltp    = q.get("ltp") or 0
    high   = q.get("high") or ltp
    low    = q.get("low")  or ltp
    entry  = sig["entry"]
    sl     = sig["sl"]
    tgts   = json.loads(sig["targets"] or "[]")
    direct = sig["direction"]

    if direct == "BUY":
        if sl and low <= sl:
            return "SL_HIT", f"day low {low} <= SL {sl}"
        hits = [t for t in tgts if t and high >= t]
        if hits:
            return f"TGT{len(hits)}_HIT", f"day high {high} reached TGT {hits[-1]}"
        move = round((ltp - entry) / entry * 100, 2) if entry else 0
        return "OPEN", f"LTP {ltp}  ({move:+.1f}% vs entry)"

    elif direct == "SELL":
        if sl and high >= sl:
            return "SL_HIT", f"day high {high} >= SL {sl}"
        hits = [t for t in tgts if t and low <= t]
        if hits:
            return f"TGT{len(hits)}_HIT", f"day low {low} hit TGT {hits[-1]}"
        move = round((entry - ltp) / entry * 100, 2) if entry else 0
        return "OPEN", f"LTP {ltp}  ({move:+.1f}% vs entry)"

    return "OPEN", f"LTP {ltp}"


def run(dry_run: bool = False) -> None:
    now      = datetime.now(IST)
    date_str = now.strftime("%Y-%m-%d")
    log.info("EOD grader -- %s", date_str)

    # -- Fetch supplementary data ---------------------------------------------
    nse.init()
    try:
        store_fii()
    except Exception as e:
        log.warning("FII/DII store: %s", e)
    try:
        store_bulk()
    except Exception as e:
        log.warning("Bulk deals store: %s", e)

    # -- Load today's open signals --------------------------------------------
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM signal_log WHERE date=? AND result='OPEN'", (date_str,)
        ).fetchall()

    if not rows:
        log.info("No open signals to grade")
        send(f"[DATA] <b>EOD {date_str}</b>\nNo signals logged today.", dry_run=dry_run)
        return

    sigs = [dict(zip(COLS, r)) for r in rows]

    # Fetch NSE quotes for all mentioned instruments
    from signals.extractor import INDICES, NOISE, OPT_STRIKE_RE, base_symbol, is_index
    idx   = nse.all_indices()
    nifty = idx.get("NIFTY 50", {})
    bnf   = idx.get("NIFTY BANK", {})

    stock_syms = {
        base_symbol(s["instrument"]) for s in sigs
        if not is_index(s["instrument"])
        and base_symbol(s["instrument"]) not in NOISE
        and not OPT_STRIKE_RE.match(base_symbol(s["instrument"]))
        and not base_symbol(s["instrument"])[0].isdigit()
    }
    quotes = {}
    for sym in sorted(stock_syms):
        time.sleep(0.3)
        q = nse.quote(sym)
        if q and q.get("ltp"):
            quotes[sym] = q

    # -- Grade each signal ----------------------------------------------------
    graded = []
    with db() as conn:
        for s in sigs:
            sym = base_symbol(s["instrument"])
            q   = quotes.get(sym)
            if not q:
                if sym == "NIFTY":
                    q = {"ltp": nifty.get("last"), "high": nifty.get("high"), "low": nifty.get("low")}
                elif sym in ("BANKNIFTY", "BNF"):
                    q = {"ltp": bnf.get("last"), "high": bnf.get("high"), "low": bnf.get("low")}

            result, note = grade_signal(s, q)
            conn.execute("""
                UPDATE signal_log SET result=?, result_note=?, graded_at=? WHERE id=?
            """, (result, note, now.isoformat(), s["id"]))
            graded.append({"sig": s, "result": result, "note": note, "q": q})
        conn.commit()

    # -- Format EOD report ----------------------------------------------------
    hits  = sum(1 for g in graded if "TGT" in g["result"])
    sls   = sum(1 for g in graded if g["result"] == "SL_HIT")
    opens = sum(1 for g in graded if g["result"] == "OPEN")

    L = []
    L.append(f"[DATA] <b>EOD REPORT -- {date_str}</b>")
    L.append(f"[OK] {hits} TGT hit  [ALERT] {sls} SL hit  [OPEN] {opens} open  (of {len(graded)} calls)")
    L.append("")

    # Sort: TGT hits first, then open, then SL hits
    order = {"TGT3_HIT":0,"TGT2_HIT":1,"TGT1_HIT":2,"OPEN":3,"SL_HIT":4}
    graded.sort(key=lambda x: order.get(x["result"], 5))

    by_ch = defaultdict(list)
    for g in graded:
        by_ch[g["sig"]["channel"]].append(g)

    for channel, items in sorted(by_ch.items()):
        L.append(f"<b>-- {channel} --</b>")
        for g in items:
            s   = g["sig"]
            res = g["result"]
            em  = "[OK]" if "TGT" in res else ("[ALERT]" if res == "SL_HIT" else "[OPEN]")
            dem = "[+]" if s["direction"] == "BUY" else ("[-]" if s["direction"] == "SELL" else "[=]")
            tgts = json.loads(s["targets"] or "[]")
            line = f"  {em} {dem} <b>{s['instrument']}</b>"
            if s["entry"]:  line += f" @ {s['entry']}"
            if s["sl"]:     line += f"  SL {s['sl']}"
            if tgts:        line += "  TGT " + "/".join(str(t) for t in tgts)
            L.append(line)
            if g["note"]:
                L.append(f"    └ {res}: {g['note']}")
        L.append("")

    # Channel scorecard
    L.append("-------------------")
    L.append("<b>[UP] CHANNEL SCORECARD</b>")
    for channel, items in sorted(by_ch.items()):
        h = sum(1 for g in items if "TGT" in g["result"])
        s = sum(1 for g in items if g["result"] == "SL_HIT")
        o = sum(1 for g in items if g["result"] == "OPEN")
        t = len(items)
        pct = round(h / t * 100) if t else 0
        bar = "#" * h + "." * s + "." * o
        L.append(f"  {channel[:28]:<28}  {h}/{t} ({pct}%)  [{bar}]")

    # FII/DII
    fii_hist = last_n_days(5)
    L.append("")
    L.append(format_fii_dii(fii_hist[0] if fii_hist else None, fii_hist))

    # Bulk deals
    deals = get_today(date_str)
    if deals:
        L.append("")
        L.append(format_bulk_deals(deals))

    # -- Learning loop: update scores and regime AFTER grading ---------------
    try:
        ch_scores.update()
        instr_stats.update()
        # Regime snapshot: use today's VIX and Nifty close from already-fetched data
        vix_val    = nse.india_vix()
        nifty_data = nse.all_indices()
        nifty_close = (nifty_data.get("NIFTY 50") or {}).get("last")
        regime_mod.snapshot(vix=vix_val, nifty_close=nifty_close)
        log.info("Learning loop updated: channel scores, instrument stats, market regime")
    except Exception as e:
        log.warning("Learning loop update failed: %s", e)

    send("\n".join(L), dry_run=dry_run)
    log.info("EOD report sent")
