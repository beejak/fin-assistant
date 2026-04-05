"""
Hourly signal scanner.
- New signals since last run (deduped via signal_log)
- NSE live price cross-check
- TA state (RSI, SMA20, 52W position)
- OI velocity alerts
- Confluence detection
- Corporate event flags
"""
import re
import sqlite3
import time
import logging
from datetime import datetime, timezone
from collections import defaultdict

from config import DB_PATH, IST
from nse import client as nse
from signals.extractor import extract, INDICES, NOISE, OPT_STRIKE_RE, base_symbol, is_index
from signals import confluence as conf_mod
from signals import ta as ta_mod
from enrichers import oi_velocity as oi_mod
from enrichers import events as events_mod
from learning import channel_scores as ch_scores
from learning import instrument_stats as instr_stats
from learning import market_regime as regime_mod
from enrichers.macro_calendar import get_upcoming, format_macro_events
from bot import send

log = logging.getLogger(__name__)

# -- DB helpers ---------------------------------------------------------------

def db_init():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS signal_log (
                id TEXT PRIMARY KEY, date TEXT NOT NULL, channel TEXT NOT NULL,
                instrument TEXT NOT NULL, direction TEXT, entry REAL, sl REAL,
                targets TEXT, raw_text TEXT, sent_at TEXT NOT NULL,
                result TEXT DEFAULT 'OPEN', result_note TEXT, graded_at TEXT
            )
        """)


def already_sent(channel, instrument, direction, date_str):
    with sqlite3.connect(DB_PATH) as c:
        return bool(c.execute(
            "SELECT 1 FROM signal_log WHERE date=? AND channel=? AND instrument=? AND direction=?",
            (date_str, channel, instrument, direction)
        ).fetchone())


def log_signal(sig, date_str):
    import json
    sig_id = re.sub(r'[^a-zA-Z0-9_]', '_',
                    f"{date_str}_{sig['channel']}_{sig['instrument']}_{sig['direction']}")[:120]
    with sqlite3.connect(DB_PATH) as c:
        c.execute("""
            INSERT OR IGNORE INTO signal_log
              (id, date, channel, instrument, direction, entry, sl, targets, raw_text, sent_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (sig_id, date_str, sig["channel"], sig["instrument"], sig["direction"],
              sig.get("entry"), sig.get("sl"),
              json.dumps(sig.get("targets", [])),
              sig.get("text", "")[:500], datetime.now(IST).isoformat()))


# -- Main ---------------------------------------------------------------------

def run(dry_run: bool = False) -> None:
    now      = datetime.now(IST)
    date_str = now.strftime("%Y-%m-%d")
    db_init()

    # -- 1. Read new messages -------------------------------------------------
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT c.name, m.content, m.timestamp
            FROM messages m JOIN chats c ON m.chat_jid = c.jid
            WHERE m.chat_jid != 'tg:476254580'
              AND m.timestamp >= datetime('now', '-65 minutes')
            ORDER BY m.timestamp DESC
        """).fetchall()
    log.info("Hourly: %d messages", len(rows))

    # -- 2. Extract & deduplicate ---------------------------------------------
    new_sigs   = []
    by_channel = defaultdict(list)
    for name, text, ts in rows:
        sig = extract(text)
        if not sig: continue
        if already_sent(name, sig["instrument"], sig["direction"], date_str): continue
        sig.update({"channel": name, "ts": ts, "text": text})
        new_sigs.append(sig)
        by_channel[name].append(sig)

    log.info("%d new signals", len(new_sigs))
    if not new_sigs:
        log.info("Nothing new -- skipping send")
        return

    # -- 3. NSE data ----------------------------------------------------------
    nse.init()
    idx   = nse.all_indices()
    nifty = idx.get("NIFTY 50", {})
    bnf   = idx.get("NIFTY BANK", {})
    oc_n  = nse.option_chain("NIFTY")
    oc_b  = nse.option_chain("BANKNIFTY")
    vix   = nse.india_vix()

    # OI velocity (compare to last snapshot, then store new snapshot)
    oi_alerts = oi_mod.velocity_alerts()
    try:
        oi_mod.snapshot()
    except Exception as e:
        log.warning("OI snapshot: %s", e)

    # NSE quotes for mentioned stocks
    stock_syms = {
        base_symbol(s["instrument"]) for s in new_sigs
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

    # TA enrichment for stock signals
    ta_cache = {}
    for sym in list(quotes.keys())[:8]:  # cap at 8 to avoid slow runs
        try:
            ta_cache[sym] = ta_mod.enrich(sym, ltp=quotes[sym].get("ltp"))
        except Exception as e:
            log.warning("TA %s: %s", sym, e)

    # Confluence check
    confluences = conf_mod.get_confluences(date_str, min_channels=2)

    # Corporate events for mentioned stocks
    all_stock_syms = list(stock_syms)
    events_map = events_mod.get_events_for(all_stock_syms, days_ahead=5) if all_stock_syms else {}

    # -- 4. Macro events due today --------------------------------------------
    macro_today = get_upcoming(days_ahead=1)

    # -- 5. Load learning context ---------------------------------------------
    scores = ch_scores.get_all()
    regime = regime_mod.get_latest()

    # Sort channels: HIGH-confidence first, LOW last
    conf_order = {"HIGH": 0, "MED": 1, "UNKNOWN": 2, "LOW": 3}
    by_channel = dict(sorted(
        by_channel.items(),
        key=lambda x: (conf_order.get((scores.get(x[0]) or {}).get("confidence", "UNKNOWN"), 2), -len(x[1]))
    ))

    # -- 6. Format ------------------------------------------------------------
    L = []
    L.append(f"[LIVE] <b>SIGNAL SCAN  {now.strftime('%H:%M IST')}</b>  ({len(new_sigs)} new)")

    # Compact index bar
    def idx_bar(label, d, oc=None):
        if not d or not d.get("last"): return
        pct = d.get("percentChange", 0) or 0
        em  = "[+]" if pct >= 0 else "[-]"
        line = f"{em} <b>{label}</b> {d['last']:,.0f} ({pct:+.2f}%)"
        if oc:
            bias_em = {"BULLISH": "BULL", "BEARISH": "BEAR", "NEUTRAL": "NEU"}.get(oc["bias"], "")
            line += f"  PCR {oc['pcr']}{bias_em}  R:{oc['max_ce']}  S:{oc['max_pe']}"
        L.append(line)

    idx_bar("NIFTY", nifty, oc_n)
    idx_bar("BANKNIFTY", bnf, oc_b)
    if vix:
        vem = "[!!!]" if vix > 20 else ("[!!]" if vix > 15 else "[.]")
        L.append(f"{vem} VIX {vix:.2f}")

    # Market regime context (from yesterday's EOD snapshot)
    regime_line = regime_mod.format_regime_line(regime)
    if regime_line:
        L.append(regime_line)

    # Macro events firing today
    macro_text = format_macro_events(macro_today)
    if macro_text:
        L.append(macro_text)

    L.append("")

    # Confluence alert (fire first if present)
    if confluences:
        L.append(conf_mod.format_confluence_alert(confluences))
        L.append("")

    # OI velocity
    oi_text = oi_mod.format_oi_velocity(oi_alerts)
    if oi_text:
        L.append(oi_text)
        L.append("")

    # Signals by channel (sorted by confidence: HIGH -> MED -> UNKNOWN -> LOW)
    for channel, sigs in by_channel.items():
        score_badge = ch_scores.format_score_badge(channel, scores)
        ch_header = f"<b>>> {channel}</b>  ({len(sigs)})"
        if score_badge:
            ch_header += f"  <i>{score_badge}</i>"
        L.append(ch_header)
        for s in sigs:
            ts_ist = (datetime.fromisoformat(s["ts"])
                      .replace(tzinfo=timezone.utc).astimezone(IST))
            em = "[+]" if s["direction"] == "BUY" else ("[-]" if s["direction"] == "SELL" else "[=]")

            parts = [f"{em} <b>{s['instrument']}</b>"]
            if s.get("entry"):   parts.append(f"@ {s['entry']}")
            if s.get("sl"):      parts.append(f"SL {s['sl']}")
            if s.get("targets"): parts.append("TGT " + "/".join(str(t) for t in s["targets"]))
            parts.append(f"[{ts_ist.strftime('%H:%M')}]")
            L.append("  " + "  ".join(parts))

            sym = base_symbol(s["instrument"])

            # NSE live check
            if sym in quotes:
                q   = quotes[sym]
                pct = q.get("pct") or 0
                arr = "^" if pct >= 0 else "v"
                nline = f"  └ NSE Rs.{q['ltp']}  {arr}{abs(pct):.1f}%"
                if s.get("entry") and q.get("ltp"):
                    diff = (q["ltp"] - s["entry"]) / s["entry"] * 100
                    if abs(diff) > 3:
                        nline += f"  [WARN] entry {s['entry']} ({abs(diff):.0f}% away)"
                    elif s["direction"] == "BUY" and q["ltp"] >= s["entry"]:
                        nline += "  [OK]"
                    elif s["direction"] == "SELL" and q["ltp"] <= s["entry"]:
                        nline += "  [OK]"
                if s.get("sl") and q.get("ltp"):
                    if (s["direction"] == "BUY" and q["ltp"] < s["sl"]) or \
                       (s["direction"] == "SELL" and q["ltp"] > s["sl"]):
                        nline += "  [ALERT] SL HIT"
                L.append(nline)
            elif sym == "NIFTY" and nifty.get("last"):
                L.append(f"  └ NIFTY {nifty['last']:,.0f}  ({nifty.get('percentChange',0):+.2f}%)")
            elif sym in ("BANKNIFTY", "BNF") and bnf.get("last"):
                L.append(f"  └ BANKNIFTY {bnf['last']:,.0f}  ({bnf.get('percentChange',0):+.2f}%)")

            # TA state
            if sym in ta_cache:
                ta_line = ta_mod.format_ta(ta_cache[sym])
                if ta_line:
                    L.append(f"  └ TA: {ta_line}")

            # Historical hit rate for this instrument + direction
            stat_line = instr_stats.format_stat_line(s["instrument"], s["direction"])
            if stat_line:
                L.append(f"  └ {stat_line}")

            # Corporate event warning
            if sym in events_map:
                L.append(events_mod.format_event_flag(sym, events_map[sym]))

        L.append("")

    # Log & send
    for s in new_sigs:
        log_signal(s, date_str)

    send("\n".join(L), dry_run=dry_run)
    log.info("Hourly scan sent")
