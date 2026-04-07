"""
Bot query handler.

Receives freeform text from the owner via Telegram, extracts the stock/index
symbol, fetches all available data, and replies with a structured snapshot
plus an optional Claude-synthesised analysis (requires ANTHROPIC_API_KEY in .env).
"""
import html
import logging
import re
import time

from config import db, IST
from nse import client as nse
from signals import ta as ta_mod
from signals.extractor import INDICES
from enrichers import events as events_mod
from bot import send

log = logging.getLogger(__name__)

# ── Symbol aliases ────────────────────────────────────────────────────────────
# Maps common spoken names → canonical NSE symbols
_ALIASES: dict[str, str] = {
    "RIL": "RELIANCE",
    "RELIANCE": "RELIANCE",
    "TCS": "TCS",
    "INFOSYS": "INFY",
    "INFY": "INFY",
    "WIPRO": "WIPRO",
    "HCL": "HCLTECH",
    "HCLTECH": "HCLTECH",
    "HDFC": "HDFCBANK",
    "HDFCBANK": "HDFCBANK",
    "ICICI": "ICICIBANK",
    "ICICIBANK": "ICICIBANK",
    "AXIS": "AXISBANK",
    "AXISBANK": "AXISBANK",
    "SBI": "SBIN",
    "SBIN": "SBIN",
    "KOTAK": "KOTAKBANK",
    "KOTAKBANK": "KOTAKBANK",
    "BAJAJ FINANCE": "BAJFINANCE",
    "BAJAJFINANCE": "BAJFINANCE",
    "BAJFINANCE": "BAJFINANCE",
    "BAJAJ FINSERV": "BAJAJFINSV",
    "BAJAJFINSERV": "BAJAJFINSV",
    "BAJAJFINSV": "BAJAJFINSV",
    "MARUTI": "MARUTI",
    "TATAMOTORS": "TATAMOTORS",
    "TATA MOTORS": "TATAMOTORS",
    "TATASTEEL": "TATASTEEL",
    "TATA STEEL": "TATASTEEL",
    "ONGC": "ONGC",
    "BPCL": "BPCL",
    "IOC": "IOC",
    "NTPC": "NTPC",
    "POWERGRID": "POWERGRID",
    "ASIANPAINT": "ASIANPAINT",
    "ASIAN PAINT": "ASIANPAINT",
    "ASIAN PAINTS": "ASIANPAINT",
    "TITAN": "TITAN",
    "BAJAJAUTO": "BAJAJ-AUTO",
    "BAJAJ AUTO": "BAJAJ-AUTO",
    "EICHERMOT": "EICHERMOT",
    "EICHER": "EICHERMOT",
    "HERO": "HEROMOTOCO",
    "HEROMOTOCO": "HEROMOTOCO",
    "LT": "LT",
    "L&T": "LT",
    "ADANIPORTS": "ADANIPORTS",
    "ADANI PORTS": "ADANIPORTS",
    "ADANIENT": "ADANIENT",
    "ADANI ENT": "ADANIENT",
    "SUNPHARMA": "SUNPHARMA",
    "SUN PHARMA": "SUNPHARMA",
    "DRREDDY": "DRREDDY",
    "DR REDDY": "DRREDDY",
    "CIPLA": "CIPLA",
    "DIVISLAB": "DIVISLAB",
    "DIVIS": "DIVISLAB",
    "ULTRACEMCO": "ULTRACEMCO",
    "ULTRATECH": "ULTRACEMCO",
    "SHREECEM": "SHREECEM",
    "NESTLEIND": "NESTLEIND",
    "NESTLE": "NESTLEIND",
    "HINDUNILVR": "HINDUNILVR",
    "HUL": "HINDUNILVR",
    "ITC": "ITC",
    "BRITANNIA": "BRITANNIA",
    "NIFTY": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "BANK NIFTY": "BANKNIFTY",
    "SENSEX": "SENSEX",
    "FINNIFTY": "FINNIFTY",
    "MIDCAPNIFTY": "MIDCPNIFTY",
    "MIDCAP": "MIDCPNIFTY",
}

# Index keys as returned by nse.all_indices()
_INDEX_KEYS = {
    "NIFTY":      "NIFTY 50",
    "BANKNIFTY":  "NIFTY BANK",
    "FINNIFTY":   "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
}

_SKIP_WORDS = {
    "CAN", "FOR", "THE", "AND", "ARE", "NOT", "BUY", "SELL", "HOLD",
    "WHAT", "IS", "IT", "IN", "ON", "AT", "DO", "BE", "ME", "WE",
    "LONG", "TERM", "SHORT", "TARGET", "STOP", "LOSS", "VIEW", "CALL",
    "GOOD", "BAD", "SHOULD", "WILL", "HAS", "HAVE", "GET", "SET",
    "HOW", "WHY", "WHEN", "WHO", "YES", "NO", "OK", "OKAY",
}


# ── Public entry point ────────────────────────────────────────────────────────

def handle(text: str, chat_id: int) -> None:
    """Route an incoming Telegram message to the right handler."""
    t = text.strip()
    if t.startswith("/"):
        _handle_command(t, chat_id)
    else:
        sym = _extract_symbol(t)
        if sym:
            _respond(sym, t, chat_id)
        else:
            send(
                "Ask about any NSE stock or index — just mention the name.\n\n"
                "Examples:\n"
                "  <code>can I hold RIL long term?</code>\n"
                "  <code>INFY outlook</code>\n"
                "  <code>/q TATAMOTORS</code>\n\n"
                "Commands: /q SYMBOL  /help",
                chat_id=chat_id,
            )


# ── Commands ──────────────────────────────────────────────────────────────────

def _handle_command(text: str, chat_id: int) -> None:
    parts = text.split()
    cmd   = parts[0].lower()
    if cmd in ("/q", "/quote") and len(parts) > 1:
        raw = " ".join(parts[1:]).upper()
        sym = _ALIASES.get(raw, raw)
        _respond(sym, f"/q {raw}", chat_id)
    elif cmd == "/help":
        send(
            "<b>nanoclaw — query mode</b>\n\n"
            "Ask naturally or use commands:\n\n"
            "<code>/q SYMBOL</code>  — live snapshot (quote + TA + channel signals)\n"
            "<code>/q NIFTY</code>   — index snapshot\n\n"
            "Or just type:\n"
            "  <code>can I hold RIL long term?</code>\n"
            "  <code>INFY next week?</code>\n"
            "  <code>what channels are bullish on BankNifty?</code>\n\n"
            "<i>Tip: add ANTHROPIC_API_KEY to .env for AI-synthesised answers</i>",
            chat_id=chat_id,
        )
    else:
        send("Unknown command. Try <code>/help</code>", chat_id=chat_id)


# ── Symbol extraction ─────────────────────────────────────────────────────────

def _extract_symbol(text: str) -> str | None:
    upper = text.upper()
    # Longer aliases first to avoid partial matches (e.g. "BAJAJ FINANCE" before "BAJAJ")
    for alias in sorted(_ALIASES, key=len, reverse=True):
        if re.search(r'\b' + re.escape(alias) + r'\b', upper):
            return _ALIASES[alias]
    # Last resort: grab first word-like token that looks like a ticker
    for m in re.findall(r'\b([A-Z&]{2,12})\b', upper):
        if m not in _SKIP_WORDS:
            return m
    return None


# ── Main responder ────────────────────────────────────────────────────────────

def _respond(sym: str, query: str, chat_id: int) -> None:
    nse.init()
    lines:   list[str] = []
    context: list[str] = []   # plain text fed to Claude

    lines.append(f"<b>{html.escape(sym)}</b>  <i>{html.escape(query[:60])}</i>")
    lines.append("")

    # ── Live price ────────────────────────────────────────────────────────────
    is_idx = sym in _INDEX_KEYS or sym == "SENSEX"
    q_data = None

    if is_idx:
        if sym == "SENSEX":
            d = nse.sensex()
        else:
            d = nse.all_indices().get(_INDEX_KEYS[sym])
        if d and d.get("last"):
            pct = d.get("percentChange", 0) or 0
            arr = "▲" if pct >= 0 else "▼"
            lines.append(f"{d['last']:,.0f}  {arr}{abs(pct):.1f}% today")
            context.append(f"{sym} at {d['last']:,.0f} ({pct:+.1f}% today)")
    else:
        q_data = nse.quote(sym)
        if q_data and q_data.get("ltp"):
            ltp = q_data["ltp"]
            pct = q_data.get("pct") or 0
            arr = "▲" if pct >= 0 else "▼"
            lines.append(f"LTP <b>{ltp:,.2f}</b>  {arr}{abs(pct):.1f}%")
            if q_data.get("high52") and q_data.get("low52"):
                h52, l52 = q_data["high52"], q_data["low52"]
                rng = h52 - l52
                pct_rng = (ltp - l52) / rng * 100 if rng else 0
                lines.append(f"52W  H {h52:,.2f}  L {l52:,.2f}  ({pct_rng:.0f}% of range)")
                context.append(
                    f"{sym} LTP {ltp:,.2f} ({pct:+.1f}% today), "
                    f"52W H {h52:,.2f} L {l52:,.2f} ({pct_rng:.0f}% of range)"
                )
        else:
            lines.append(f"<i>NSE quote unavailable for {html.escape(sym)}</i>")
            context.append(f"{sym}: live price not available")

    # ── TA ────────────────────────────────────────────────────────────────────
    if not is_idx:
        try:
            time.sleep(0.3)
            ltp = (q_data or {}).get("ltp")
            ta  = ta_mod.enrich(sym, ltp=ltp)
            ta_line = ta_mod.format_ta(ta)
            if ta_line:
                lines.append(f"TA: {ta_line}")
                context.append(f"Technical indicators: {ta_line}")
        except Exception as e:
            log.debug("TA %s: %s", sym, e)

    # ── Signal history (last 30 days) ─────────────────────────────────────────
    try:
        with db() as conn:
            rows = conn.execute("""
                SELECT channel, direction, entry, result, date
                FROM signal_log
                WHERE (instrument LIKE ? OR instrument = ?)
                  AND date >= DATE('now', '-30 days')
                ORDER BY date DESC
                LIMIT 10
            """, (f"%{sym}%", sym)).fetchall()
        if rows:
            lines.append("")
            lines.append(f"<b>Channel signals — last 30d</b>")
            for ch, direction, entry, result, date in rows[:6]:
                em   = "▲" if direction == "BUY" else "▼"
                res  = f" → {result}" if result and result != "OPEN" else " (open)"
                estr = f"@ {entry}  " if entry else ""
                lines.append(f"  {em} {html.escape(ch[:24])}  {estr}{res}  <i>{date}</i>")
            context.append(
                f"Channel signals for {sym} in last 30 days: "
                + "; ".join(
                    f"{r[1]} from {r[0][:20]} @ {r[2] or '?'} → {r[3] or 'OPEN'}"
                    for r in rows[:6]
                )
            )
        else:
            context.append(f"No recent channel signals for {sym}")
    except Exception as e:
        log.debug("Signal history %s: %s", sym, e)

    # ── Corporate events ──────────────────────────────────────────────────────
    if not is_idx:
        try:
            ev_map = events_mod.get_events_for([sym], days_ahead=60)
            if ev_map.get(sym):
                ev_line = events_mod.format_event_flag(sym, ev_map[sym])
                lines.append("")
                lines.append(ev_line)
                context.append(f"Upcoming events: {ev_line}")
        except Exception as e:
            log.debug("Events %s: %s", sym, e)

    # ── Claude synthesis (via local claude CLI) ───────────────────────────────
    if context:
        synthesis = _claude_synthesis(sym, query, "\n".join(context))
        if synthesis:
            lines.append("")
            lines.append("<b>Analysis</b>")
            lines.append(html.escape(synthesis))

    send("\n".join(lines), chat_id=chat_id)


def _claude_synthesis(sym: str, query: str, context: str, _unused_key: str = "") -> str | None:
    """Call the local `claude` CLI for synthesis — no separate API key needed."""
    try:
        import subprocess
        prompt = (
            f"User question about {sym}: {query}\n\n"
            f"Available market data:\n{context}\n\n"
            "Answer in 3-5 sentences. Be direct. "
            "Data is technical (price, TA, Telegram channel signals), not fundamental "
            "(no P/E or earnings). If the question needs fundamentals, note that briefly "
            "then give the technical view."
        )
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=30
        )
        out = result.stdout.strip()
        return out if out else None
    except Exception as e:
        log.warning("Claude CLI synthesis: %s", e)
        return None
