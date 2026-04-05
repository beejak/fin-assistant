"""
Pre-open briefing (runs at 8:45 AM IST, before 9:15 market open).
Covers: GIFT Nifty gap, India VIX, FII/DII yesterday, key events today,
        OI snapshot baseline, and any overnight channel signals.
"""
import sqlite3
import time
import logging
from datetime import datetime, timezone, timedelta

from config import db, DB_PATH, IST, BOT_TOKEN, OWNER_CHAT_ID, IGNORED_CHAT_IDS
from nse import client as nse
from enrichers.fii_dii import last_n_days, format_fii_dii
from enrichers.bulk_deals import get_today, format_bulk_deals
from enrichers.oi_velocity import snapshot as oi_snapshot
from enrichers.global_markets import get_snapshot as get_global, format_global_markets
from enrichers.macro_calendar import get_upcoming, format_macro_events
from signals.extractor import extract, INDICES
from learning import market_regime as regime_mod
from learning import channel_scores as ch_scores
from bot import send

log = logging.getLogger(__name__)


def run(dry_run: bool = False) -> None:
    now = datetime.now(IST)
    log.info("Pre-open briefing -- %s", now.strftime("%Y-%m-%d %H:%M IST"))

    nse.init()

    # -- Index snapshot --------------------------------------------------------
    idx    = nse.all_indices()
    nifty  = idx.get("NIFTY 50", {})
    bnf    = idx.get("NIFTY BANK", {})
    fins   = idx.get("NIFTY FIN SERVICE", {})
    gift   = idx.get("GIFT NIFTY", {})
    vix_v  = nse.india_vix()

    # Take OI baseline snapshot for today's velocity tracking
    try:
        oi_snapshot()
    except Exception as e:
        log.warning("OI snapshot failed: %s", e)

    # -- Global markets & macro calendar --------------------------------------
    global_data  = get_global()
    macro_events = get_upcoming(days_ahead=2)

    # -- FII/DII last 5 days --------------------------------------------------
    fii_history = last_n_days(5)
    fii_today   = fii_history[0] if fii_history else None

    # -- Overnight signals from DB --------------------------------------------
    ignored_placeholders = ",".join("?" * len(IGNORED_CHAT_IDS))
    ignored_list = list(IGNORED_CHAT_IDS) or [""]
    with db() as conn:
        rows = conn.execute(f"""
            SELECT c.name, m.content, m.timestamp
            FROM messages m JOIN chats c ON m.chat_jid = c.jid
            WHERE m.chat_jid NOT IN ({ignored_placeholders or '?'})
              AND m.content IS NOT NULL
              AND m.timestamp >= datetime('now', '-12 hours')
            ORDER BY m.timestamp DESC
        """, ignored_list).fetchall()

    overnight_sigs = []
    seen = set()
    for name, text, ts in rows:
        sig = extract(text)
        if sig:
            key = (sig["instrument"], sig["direction"])
            if key not in seen:
                seen.add(key)
                sig.update({"channel": name, "ts": ts})
                overnight_sigs.append(sig)

    # -- Load memory context --------------------------------------------------
    regime      = regime_mod.get_latest()
    scores      = ch_scores.get_all()
    mute_list   = [ch for ch, s in scores.items() if s.get("suggest_mute")]

    # -- Format ---------------------------------------------------------------
    L = []
    L.append("[PRE-OPEN] <b>PRE-OPEN BRIEFING</b>")
    L.append(f"[DATE] {now.strftime('%a %d %b %Y  %H:%M IST')}")
    L.append(f"Market opens in ~{max(0, (now.replace(hour=9,minute=15,second=0)-now).seconds//60)} min")
    L.append("")

    # Yesterday's regime (persistent market memory)
    regime_line = regime_mod.format_regime_line(regime)
    if regime_line:
        L.append(f"<b>Yesterday's regime:</b> {regime_line.replace('[UP] Regime: ','')}")
        L.append("")

    # GIFT Nifty (SGX gap indicator)
    if gift and gift.get("last"):
        prev_close = nifty.get("previousClose") or nifty.get("last", 0)
        gap        = (gift["last"] - prev_close) if prev_close else 0
        gap_pct    = gap / prev_close * 100 if prev_close else 0
        gap_em     = "[+]" if gap > 0 else ("[-]" if gap < 0 else "[=]")
        L.append(f"{gap_em} <b>GIFT NIFTY</b>  {gift['last']:,.0f}  "
                 f"Gap {'+'if gap>=0 else ''}{gap:.0f} pts ({gap_pct:+.2f}%)")
    else:
        L.append("[=] GIFT NIFTY -- data unavailable (pre-market or holiday)")

    # VIX
    if vix_v:
        vix_em = "[!!!]" if vix_v > 20 else ("[!!]" if vix_v > 15 else "[.]")
        L.append(f"{vix_em} <b>India VIX</b>  {vix_v:.2f}  "
                 f"({'HIGH -- expect volatility' if vix_v > 20 else 'ELEVATED' if vix_v > 15 else 'NORMAL'})")

    # Previous close
    def idx_prev(label, d):
        if not d or not d.get("last"): return
        pct = d.get("percentChange", 0) or 0
        em  = "[+]" if pct >= 0 else "[-]"
        L.append(f"{em} <b>{label}</b>  {d['last']:,.0f}  ({pct:+.2f}%)  "
                 f"52W H {d.get('yearHigh','?')}  L {d.get('yearLow','?')}")

    L.append("")
    L.append("[DATA] <b>Previous close</b>")
    idx_prev("NIFTY 50   ", nifty)
    idx_prev("BANK NIFTY ", bnf)
    idx_prev("FIN SERVICE", fins)

    # Global markets
    global_text = format_global_markets(global_data)
    if global_text:
        L.append("")
        L.append(global_text)

    # Macro calendar
    macro_text = format_macro_events(macro_events)
    if macro_text:
        L.append("")
        L.append(macro_text)

    # FII/DII
    L.append("")
    L.append(format_fii_dii(fii_today, fii_history))

    # Overnight signals
    if overnight_sigs:
        L.append("")
        L.append(f"[OVERNIGHT] <b>OVERNIGHT SIGNALS  ({len(overnight_sigs)})</b>")
        for s in overnight_sigs[:10]:
            em = "[+]" if s["direction"] == "BUY" else ("[-]" if s["direction"] == "SELL" else "[=]")
            parts = [f"{em} <b>{s['instrument']}</b>"]
            if s.get("entry"):   parts.append(f"@ {s['entry']}")
            if s.get("sl"):      parts.append(f"SL {s['sl']}")
            if s.get("targets"): parts.append("TGT " + "/".join(str(t) for t in s["targets"]))
            parts.append(f"[{s['channel']}]")
            L.append("  " + "  ".join(parts))

    # Channels flagged for muting (learned from past performance)
    if mute_list:
        L.append("")
        L.append("-------------------")
        L.append(f"[WARN] <b>Low-accuracy channels ({len(mute_list)})</b> -- consider muting:")
        for ch in mute_list[:5]:
            s = scores[ch]
            L.append(f"  - {ch}  {s['hit_rate']:.0f}% hit rate ({s['sl_hits']+s['hits']} closed signals, 30d)")
        if len(mute_list) > 5:
            L.append(f"  ... and {len(mute_list)-5} more. Run: python3 main.py channels")

    L.append("")
    L.append("-------------------")
    L.append("[LIVE] Bridge live  |  Hourly scans start at 9:45 AM  |  EOD at 3:45 PM")

    send("\n".join(L), dry_run=dry_run)
    log.info("Pre-open briefing sent")
