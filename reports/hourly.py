"""
Hourly signal scanner.
- New signals since last run (deduped via signal_log)
- NSE live price cross-check
- TA state (RSI, SMA20, 52W position)
- OI velocity alerts
- Confluence detection
- Corporate event flags
"""
import html
import re
import sqlite3
import time
import logging
from datetime import datetime, timezone
from collections import defaultdict

from config import db, DB_PATH, IST, IGNORED_CHAT_IDS
from nse import client as nse
from signals.extractor import extract, INDICES, NOISE, OPT_STRIKE_RE, base_symbol, is_index, is_future, is_option
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
    with db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS signal_log (
                id TEXT PRIMARY KEY, date TEXT NOT NULL, channel TEXT NOT NULL,
                instrument TEXT NOT NULL, direction TEXT, entry REAL, sl REAL,
                targets TEXT, raw_text TEXT, sent_at TEXT NOT NULL,
                result TEXT DEFAULT 'OPEN', result_note TEXT, graded_at TEXT
            )
        """)


def already_sent(channel, instrument, direction, date_str):
    with db() as c:
        return bool(c.execute(
            "SELECT 1 FROM signal_log WHERE date=? AND channel=? AND instrument=? AND direction=?",
            (date_str, channel, instrument, direction)
        ).fetchone())


def log_signal(sig, date_str):
    import json
    sig_id = re.sub(r'[^a-zA-Z0-9_]', '_',
                    f"{date_str}_{sig['channel']}_{sig['instrument']}_{sig['direction']}")[:120]
    with db() as c:
        c.execute("""
            INSERT OR IGNORE INTO signal_log
              (id, date, channel, instrument, direction, entry, sl, targets, raw_text, sent_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (sig_id, date_str, sig["channel"], sig["instrument"], sig["direction"],
              sig.get("entry"), sig.get("sl"),
              json.dumps(sig.get("targets", [])),
              sig.get("text", "")[:500], datetime.now(IST).isoformat()))


# -- Main ---------------------------------------------------------------------

def run(dry_run: bool = False, mode: str = "indices") -> None:
    """
    mode: 'indices' | 'stocks' | 'futures'
    Each mode runs as a separate cron job so they don't block each other.
    """
    now      = datetime.now(IST)
    date_str = now.strftime("%Y-%m-%d")
    db_init()

    # -- 1. Read new messages -------------------------------------------------
    ignored_list = list(IGNORED_CHAT_IDS) or [""]
    ignored_placeholders = ",".join("?" * len(ignored_list))
    with db() as conn:
        rows = conn.execute(f"""
            SELECT c.name, m.content, m.timestamp
            FROM messages m JOIN chats c ON m.chat_jid = c.jid
            WHERE m.chat_jid NOT IN ({ignored_placeholders})
              AND m.content IS NOT NULL
              AND m.timestamp >= datetime('now', '-65 minutes')
            ORDER BY m.timestamp DESC
        """, ignored_list).fetchall()
    log.info("Hourly: %d messages", len(rows))

    # -- 2. Extract & deduplicate ---------------------------------------------
    new_sigs   = []
    by_channel = defaultdict(list)
    for name, text, ts in rows:
        sig = extract(text, mode=mode)
        if not sig: continue
        if already_sent(name, sig["instrument"], sig["direction"], date_str): continue
        sig.update({"channel": name, "ts": ts, "text": text})
        new_sigs.append(sig)
        by_channel[name].append(sig)

    # Within-scan dedup: same channel calling same instrument+direction multiple
    # times in one session (e.g. channel spamming identical call 10×) →
    # keep only the most recent occurrence so the log stays clean.
    _seen: dict = {}
    for sig in new_sigs:
        key = (sig["channel"], sig["instrument"], sig["direction"])
        if key not in _seen or sig["ts"] > _seen[key]["ts"]:
            _seen[key] = sig
    new_sigs = list(_seen.values())

    log.info("%d new signals", len(new_sigs))
    if not new_sigs:
        log.info("Nothing new -- skipping send")
        return

    # -- 3. NSE data ----------------------------------------------------------
    nse.init()
    idx    = nse.all_indices()
    nifty  = idx.get("NIFTY 50", {})
    bnf    = idx.get("NIFTY BANK", {})
    sensex = nse.sensex()
    oc_n   = nse.option_chain("NIFTY")
    oc_b   = nse.option_chain("BANKNIFTY")
    vix    = nse.india_vix()

    # OI velocity (compare to last snapshot, then store new snapshot)
    oi_alerts = oi_mod.velocity_alerts()
    try:
        oi_mod.snapshot()
    except Exception as e:
        log.warning("OI snapshot: %s", e)

    # NSE quotes for mentioned symbols
    # Cap: indices=8, stocks=5, futures=5 — keeps each mode run under 2 min
    quote_cap = 8 if mode == "indices" else 5
    syms_needing_quote = {
        base_symbol(s["instrument"]) for s in new_sigs
        if not is_index(s["instrument"])
        and not base_symbol(s["instrument"])[0].isdigit()
    }
    quotes = {}
    for sym in sorted(syms_needing_quote)[:quote_cap]:
        time.sleep(0.5)   # NSE recommends ≥0.5s between requests
        q = nse.quote(sym)
        if q and q.get("ltp"):
            quotes[sym] = q

    # TA enrichment — indices and stocks only, skip for futures (less relevant)
    ta_cache = {}
    if mode != "futures":
        for sym in list(quotes.keys())[:5]:
            try:
                ta_cache[sym] = ta_mod.enrich(sym, ltp=quotes[sym].get("ltp"))
            except Exception as e:
                log.warning("TA %s: %s", sym, e)

    # Confluence + bias check
    confluences = conf_mod.get_confluences(date_str, min_channels=2)
    biases      = conf_mod.net_bias(date_str)

    # Corporate events for mentioned stocks
    all_stock_syms = list(syms_needing_quote)
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
    mode_label = {"indices": "INDEX", "stocks": "STOCKS", "futures": "FUTURES"}.get(mode, mode.upper())
    L.append(f"[LIVE] <b>{mode_label} SCAN  {now.strftime('%H:%M IST')}</b>  ({len(new_sigs)} new)")

    # Compact index bar — single line
    parts = []
    for label, d in [("NIFTY", nifty), ("BNIFTY", bnf), ("SENSEX", sensex)]:
        if not d or not d.get("last"): continue
        pct = d.get("percentChange", 0) or 0
        em  = "+" if pct >= 0 else ""
        parts.append(f"<b>{label}</b> {d['last']:,.0f} ({em}{pct:.1f}%)")
    if parts:
        L.append("  ".join(parts))

    if vix:
        if vix > 20:
            L.append(f"<b>VIX {vix:.1f} — ELEVATED</b>  ← expect whipsaws")
        elif vix > 15:
            L.append(f"VIX {vix:.1f} — watch volatility")
        else:
            L.append(f"VIX {vix:.1f}")

    if oc_n:
        bias_em = {"BULLISH": "BULL bias", "BEARISH": "BEAR bias", "NEUTRAL": "NEUTRAL"}.get(oc_n["bias"], "")
        L.append(f"NIFTY OC: PCR {oc_n['pcr']}  {bias_em}  R {oc_n['max_ce']}  S {oc_n['max_pe']}")

    # TL;DR — one-line verdict
    strong_biases  = [b for b in biases if b["bias"] in ("STRONG_BUY", "STRONG_SELL")]
    split_biases   = [b for b in biases if b["bias"] == "SPLIT"]
    digest_count   = 0   # filled in later, placeholder
    if strong_biases:
        sb = strong_biases[0]
        verdict = f"STRONG {'BUY' if sb['bias']=='STRONG_BUY' else 'SELL'} on {sb['instrument']} ({sb['total']} channels agree)"
    elif split_biases:
        sb = split_biases[0]
        verdict = f"SPLIT on {sb['instrument']} ({sb['buys']} BUY vs {sb['sells']} SELL) — no clear edge, be selective"
    elif new_sigs:
        verdict = f"{len(new_sigs)} new signals, no strong consensus"
    else:
        verdict = "No new signals"
    L.append(f"\n<b>Verdict:</b> {verdict}")

    # Macro events
    macro_text = format_macro_events(macro_today)
    if macro_text:
        L.append(macro_text)

    L.append("")

    # Confluence — only show if ALL channels are HIGH/MED confidence
    strong_conf = [c for c in confluences
                   if all((scores.get(ch) or {}).get("confidence", "UNKNOWN") in ("HIGH", "MED")
                          for ch in c["channels"])]
    if strong_conf:
        L.append(conf_mod.format_confluence_alert(strong_conf))
        L.append("")
    elif confluences:
        # Weaker confluences — show count only, don't clutter
        instr_list = ", ".join(c["instrument"] for c in confluences[:3])
        L.append(f"[~] Confluence (low-confidence channels): {instr_list}")
        L.append("")

    # Net bias block (STRONG or SPLIT only)
    bias_text = conf_mod.format_bias_block(biases)
    if bias_text:
        L.append(bias_text)
        L.append("")

    # OI velocity
    oi_text = oi_mod.format_oi_velocity(oi_alerts)
    if oi_text:
        L.append(oi_text)
        L.append("")

    # Digest — top actionable signals (entry + SL + direction, ranked by confidence)
    # For bare index spot signals, skip entries that look like option premiums (e.g. NIFTY @ 65)
    def _is_credible_entry(sig: dict) -> bool:
        if not sig.get("entry") or not sig.get("sl") or not sig.get("direction"):
            return False
        if not is_option(sig["instrument"]) and base_symbol(sig["instrument"]) in INDICES:
            if sig["entry"] < 1000:
                return False
        return True

    conf_instruments = {c["instrument"] for c in confluences}
    # Deduplicate by instrument — merge channels calling the same thing
    digest_map: dict = {}
    for s in new_sigs:
        if not _is_credible_entry(s):
            continue
        ch_conf = (scores.get(s["channel"]) or {}).get("confidence", "UNKNOWN")
        in_conf = s["instrument"] in conf_instruments
        if ch_conf not in ("HIGH", "MED") and not in_conf:
            continue
        key = s["instrument"]
        if key not in digest_map:
            digest_map[key] = {"s": s, "channels": [s["channel"]], "in_conf": in_conf,
                               "rank": (0 if in_conf else 1, conf_order.get(ch_conf, 2))}
        else:
            digest_map[key]["channels"].append(s["channel"])

    digest = sorted(digest_map.values(), key=lambda x: x["rank"])
    if digest:
        L.append("<b>── WATCHLIST  top actionable signals ──</b>")
        for item in digest[:5]:
            s    = item["s"]
            em   = "▲" if s["direction"] == "BUY" else "▼"
            tgt  = "/".join(str(t) for t in s["targets"]) if s.get("targets") else ""
            chs  = item["channels"]
            line = f"  {em} <b>{s['instrument']}</b>  @ {s['entry']}  SL {s['sl']}"
            if tgt:
                line += f"  TGT {tgt}"
                # R:R ratio
                if s.get("entry") and s.get("sl") and s.get("targets"):
                    risk = abs(s["entry"] - s["sl"])
                    if risk > 0:
                        reward = abs(s["targets"][0] - s["entry"])
                        line += f"  <i>R:R {reward/risk:.1f}</i>"
            # Stale signal check
            try:
                ts_s = datetime.fromisoformat(s["ts"]).replace(tzinfo=timezone.utc).astimezone(IST)
                if (now - ts_s).total_seconds() / 3600 > 2:
                    line += "  <i>STALE</i>"
            except Exception:
                pass
            if len(chs) > 1:
                line += f"  — {len(chs)} channels"
            else:
                badge = ch_scores.format_score_badge(chs[0], scores)
                line += f"  — {html.escape(chs[0])}"
                if badge: line += f"  <i>{badge}</i>"
            L.append(line)
        L.append("")

    # Digest instruments — expand these fully; collapse everything else
    digest_instruments = {item["s"]["instrument"] for item in digest}

    # Signals by channel (sorted by confidence: HIGH -> MED -> UNKNOWN -> LOW)
    for channel, sigs in by_channel.items():
        score_badge = ch_scores.format_score_badge(channel, scores)
        ch_conf     = (scores.get(channel) or {}).get("confidence", "UNKNOWN")

        # Signals in this channel that are in the digest get full treatment
        digest_sigs = [s for s in sigs if s["instrument"] in digest_instruments]
        other_sigs  = [s for s in sigs if s["instrument"] not in digest_instruments]

        if not digest_sigs and ch_conf not in ("HIGH", "MED"):
            # Low-confidence channel with no digest signals — one-liner only
            instruments = ", ".join(
                f"{'▲' if s['direction']=='BUY' else ('▼' if s['direction']=='SELL' else '—')} {s['instrument']}"
                for s in sigs
            )
            badge_str = f"  <i>{score_badge}</i>" if score_badge else ""
            L.append(f"  <b>{html.escape(channel)}</b>{badge_str}: {instruments}")
            continue

        ch_header = f"<b>&gt;&gt; {html.escape(channel)}</b>  ({len(sigs)})"
        if score_badge:
            ch_header += f"  <i>{score_badge}</i>"
        L.append(ch_header)

        for s in digest_sigs + other_sigs:
            ts_ist = (datetime.fromisoformat(s["ts"])
                      .replace(tzinfo=timezone.utc).astimezone(IST))
            em    = "▲" if s["direction"] == "BUY" else ("▼" if s["direction"] == "SELL" else "—")
            stale = "  <i>STALE</i>" if (now - ts_ist).total_seconds() / 3600 > 2 else ""

            parts = [f"{em} <b>{s['instrument']}</b>"]
            if s.get("entry"):   parts.append(f"@ {s['entry']}")
            if s.get("sl"):      parts.append(f"SL {s['sl']}")
            if s.get("targets"): parts.append("TGT " + "/".join(str(t) for t in s["targets"]))
            parts.append(f"[{ts_ist.strftime('%H:%M')}]")
            L.append("  " + "  ".join(parts) + stale)

            # Only show enrichment for digest signals
            if s["instrument"] not in digest_instruments:
                continue

            sym = base_symbol(s["instrument"])

            if sym in quotes:
                q    = quotes[sym]
                pct  = q.get("pct") or 0
                arr  = "▲" if pct >= 0 else "▼"
                nline = f"  └ NSE {q['ltp']}  {arr}{abs(pct):.1f}%"
                if s.get("entry") and q.get("ltp") and not is_option(s["instrument"]):
                    diff = (q["ltp"] - s["entry"]) / s["entry"] * 100
                    if abs(diff) > 3:
                        nline += f"  ← entry {s['entry']} ({abs(diff):.0f}% away)"
                    elif s["direction"] == "BUY" and q["ltp"] >= s["entry"]:
                        nline += "  ✓ setup valid"
                    elif s["direction"] == "SELL" and q["ltp"] <= s["entry"]:
                        nline += "  ✓ setup valid"
                if s.get("sl") and q.get("ltp"):
                    if (s["direction"] == "BUY" and q["ltp"] < s["sl"]) or \
                       (s["direction"] == "SELL" and q["ltp"] > s["sl"]):
                        nline += "  ✗ SL breached"
                L.append(nline)
            elif sym == "NIFTY" and nifty.get("last"):
                L.append(f"  └ NIFTY {nifty['last']:,.0f}  ({nifty.get('percentChange',0):+.2f}%)")
            elif sym in ("BANKNIFTY", "BNF") and bnf.get("last"):
                L.append(f"  └ BANKNIFTY {bnf['last']:,.0f}  ({bnf.get('percentChange',0):+.2f}%)")
            elif sym == "SENSEX" and sensex and sensex.get("last"):
                L.append(f"  └ SENSEX {sensex['last']:,.0f}  ({sensex.get('percentChange',0):+.2f}%)")

            if sym in ta_cache:
                ta_line = ta_mod.format_ta(ta_cache[sym])
                if ta_line:
                    L.append(f"  └ {ta_line}")

            stat_line = instr_stats.format_stat_line(s["instrument"], s["direction"])
            if stat_line:
                L.append(f"  └ {stat_line}")

            if sym in events_map:
                L.append(events_mod.format_event_flag(sym, events_map[sym]))

        L.append("")

    # Channel leaderboard — only when we have graded data
    channels_ranked = sorted(
        [(ch, s) for ch, s in scores.items() if s.get("hit_rate") is not None],
        key=lambda x: x[1]["hit_rate"] or 0, reverse=True
    )
    if channels_ranked:
        L.append("<b>Channel Leaderboard</b>")
        for ch, sc in channels_ranked[:6]:
            flag = "  ← mute?" if sc.get("suggest_mute") else ""
            bar = "█" * int((sc["hit_rate"] or 0) // 20)   # 0-5 blocks, 20% each
            L.append(f"  <code>{bar:<5}</code> {html.escape(ch[:28])}  {sc['hit_rate']:.0f}% ({sc['total']} sig){flag}")

    # Log & send
    if not dry_run:
        for s in new_sigs:
            log_signal(s, date_str)

    send("\n".join(L), dry_run=dry_run)
    log.info("Hourly scan sent")
