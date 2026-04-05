"""
Global markets snapshot for pre-open context.

Fetches overnight/last-session moves for indices and commodities
that are known to correlate with Indian market direction.

All data via yfinance (free, no API key).
"""
import logging
import yfinance as yf

log = logging.getLogger(__name__)

# Ordered for display: US indices -> Asia -> commodities -> macro
TICKERS = [
    ("S&P 500",    "^GSPC",     "US equity -- strongest single Nifty correlate"),
    ("Nasdaq",     "^IXIC",     "US tech -- leads IT sector stocks"),
    ("Nikkei 225", "^N225",     "Japan -- Asian session direction"),
    ("Hang Seng",  "^HSI",      "China proxy -- affects metals and EM sentiment"),
    ("Crude WTI",  "CL=F",      "Oil -- inflation, OMCs, rupee pressure"),
    ("Gold",       "GC=F",      "Risk-off gauge -- safe-haven flows"),
    ("DXY",        "DX-Y.NYB",  "Dollar index -- strong DXY = FII outflows from India"),
    ("US 10Y",     "^TNX",      "Treasury yield -- risk-off = FII exits EM"),
]


def get_snapshot() -> list[dict]:
    """
    Fetch last price and overnight % change for each tracked ticker.
    Returns list of dicts with keys: label, last, pct, note, unit.
    """
    results = []
    for label, sym, note in TICKERS:
        try:
            fi = yf.Ticker(sym).fast_info
            last  = fi.last_price
            prev  = fi.previous_close
            if last is None or prev is None or prev == 0:
                continue
            pct = round((last - prev) / prev * 100, 2)
            unit = "%" if sym == "^TNX" else ""
            results.append({
                "label": label,
                "sym":   sym,
                "last":  last,
                "prev":  prev,
                "pct":   pct,
                "note":  note,
                "unit":  unit,
            })
        except Exception as e:
            log.debug("global_markets %s: %s", sym, e)
    return results


def format_global_markets(data: list[dict]) -> str:
    if not data:
        return ""
    lines = ["[GLOBAL] <b>GLOBAL MARKETS</b>"]
    for d in data:
        em  = "[+]" if d["pct"] >= 0 else "[-]"
        val = f"{d['last']:,.2f}{d['unit']}"
        pct = f"({d['pct']:+.2f}%)"
        # Flag significant moves that traders should know about
        flag = ""
        if d["sym"] == "^GSPC"    and abs(d["pct"]) >= 1.0: flag = "  [!] strong move"
        if d["sym"] == "^IXIC"    and abs(d["pct"]) >= 1.5: flag = "  [!] strong move"
        if d["sym"] == "CL=F"     and abs(d["pct"]) >= 2.0: flag = "  [!] oil spike"
        if d["sym"] == "DX-Y.NYB" and d["pct"]     >= 0.5:  flag = "  [WARN] USD strength -> FII outflow risk"
        if d["sym"] == "DX-Y.NYB" and d["pct"]     <= -0.5: flag = "  [OK] USD weakness -> FII inflow tailwind"
        if d["sym"] == "^TNX"     and d["last"]     >= 4.5:  flag = "  [WARN] yields elevated"
        lines.append(f"  {em} <b>{d['label']:<12}</b>  {val:<10}  {pct}{flag}")
    return "\n".join(lines)
