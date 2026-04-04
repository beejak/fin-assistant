"""
NSE India API client.
- Direct JSON API calls (no cookie warmup needed from this environment)
- Handles gzip decompression; avoids brotli (requires brotlipy)
- Retries on 401/403 with session refresh
"""
import re
import time
import logging
import requests
from datetime import datetime

log = logging.getLogger(__name__)

BASE = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",   # no 'br' — brotlipy not installed
    "Referer":         "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
}

_sess: requests.Session | None = None


def init():
    global _sess
    _sess = requests.Session()
    _sess.headers.update(HEADERS)


def get(path: str, retries: int = 3) -> dict | list | None:
    global _sess
    if _sess is None:
        init()
    for attempt in range(retries):
        try:
            r = _sess.get(BASE + path, timeout=15)
            if r.status_code in (401, 403):
                log.warning("NSE %d on %s — reinit session", r.status_code, path)
                init()
                time.sleep(1)
                continue
            if r.status_code == 200:
                ct = r.headers.get("Content-Type", "")
                if "json" in ct or r.text.lstrip()[:1] in ("{", "["):
                    return r.json()
                log.warning("NSE non-JSON on %s (ct=%s)", path, ct[:40])
                return None
        except Exception as e:
            log.warning("NSE %s attempt %d: %s", path, attempt + 1, e)
            time.sleep(2)
    return None


# ── Market status ──────────────────────────────────────────────────────────

def market_status() -> str:
    data = get("/api/marketStatus")
    if not data:
        return "UNKNOWN"
    for m in data.get("marketState", []):
        if m.get("market") == "Capital Market":
            return m.get("marketStatus", "UNKNOWN")
    return "UNKNOWN"


def is_market_open() -> bool:
    return market_status() == "Open"


# ── Indices ────────────────────────────────────────────────────────────────

def all_indices() -> dict:
    """Return dict keyed by index name."""
    data = get("/api/allIndices")
    if not data:
        return {}
    return {d["index"]: d for d in data.get("data", [])}


def gift_nifty() -> dict | None:
    """Return GIFT NIFTY data (pre-market indicator)."""
    idx = all_indices()
    return idx.get("GIFT NIFTY") or idx.get("INDIA VIX") and None


def india_vix() -> float | None:
    idx = all_indices()
    v = idx.get("India VIX") or idx.get("INDIA VIX")
    return v.get("last") if v else None


# ── Equity quotes ──────────────────────────────────────────────────────────

def quote(symbol: str) -> dict | None:
    data = get(f"/api/quote-equity?symbol={symbol.upper()}")
    if not data:
        return None
    try:
        pi  = data["priceInfo"]
        hl  = pi.get("intraDayHighLow") or {}
        whl = pi.get("weekHighLow") or {}
        return {
            "symbol": symbol.upper(),
            "ltp":    pi.get("lastPrice"),
            "pct":    pi.get("pChange"),
            "high":   hl.get("max"),
            "low":    hl.get("min"),
            "close":  pi.get("close") or pi.get("previousClose"),
            "wh52":   whl.get("max"),
            "wl52":   whl.get("min"),
        }
    except Exception as e:
        log.warning("Quote parse %s: %s", symbol, e)
        return None


# ── Option chain ───────────────────────────────────────────────────────────

def option_chain(symbol: str) -> dict | None:
    """
    Returns processed option chain for nearest expiry.
    symbol: NIFTY | BANKNIFTY | FINNIFTY
    """
    data = get(f"/api/option-chain-indices?symbol={symbol.upper()}")
    if not data:
        return None
    try:
        records  = data["records"]["data"]
        expiries = data["records"]["expiryDates"]
        expiry   = expiries[0] if expiries else None
        atm_price = data["records"].get("underlyingValue", 0)

        tce = tpe = 0
        max_ce = max_pe = {"oi": 0, "strike": 0}
        strikes = []

        for r in records:
            if r.get("expiryDate") != expiry:
                continue
            sp   = r.get("strikePrice", 0)
            ce   = r.get("CE") or {}
            pe   = r.get("PE") or {}
            co   = ce.get("openInterest", 0) or 0
            po   = pe.get("openInterest", 0) or 0
            cchg = ce.get("changeinOpenInterest", 0) or 0
            pchg = pe.get("changeinOpenInterest", 0) or 0
            tce += co; tpe += po
            if co > max_ce["oi"]: max_ce = {"oi": co, "strike": sp}
            if po > max_pe["oi"]: max_pe = {"oi": po, "strike": sp}
            strikes.append({
                "strike": sp,
                "ce_oi": co, "ce_chg_oi": cchg, "ce_ltp": ce.get("lastPrice"),
                "pe_oi": po, "pe_chg_oi": pchg, "pe_ltp": pe.get("lastPrice"),
            })

        pcr  = round(tpe / tce, 2) if tce else 0
        bias = "BULLISH" if pcr > 1.2 else ("BEARISH" if pcr < 0.8 else "NEUTRAL")
        return {
            "symbol":    symbol.upper(),
            "expiry":    expiry,
            "atm":       atm_price,
            "pcr":       pcr,
            "bias":      bias,
            "max_ce":    max_ce["strike"],
            "max_pe":    max_pe["strike"],
            "total_ce":  tce,
            "total_pe":  tpe,
            "strikes":   strikes,
        }
    except Exception as e:
        log.warning("OC parse %s: %s", symbol, e)
        return None


def oi_velocity(oc: dict, top_n: int = 5) -> list[dict]:
    """
    Return top N strikes with largest absolute OI change (buildup + unwinding).
    Requires option_chain() output.
    """
    if not oc or not oc.get("strikes"):
        return []
    rows = []
    for s in oc["strikes"]:
        for side, oi, chg in (("CE", s["ce_oi"], s["ce_chg_oi"]),
                               ("PE", s["pe_oi"], s["pe_chg_oi"])):
            if chg and abs(chg) > 0:
                rows.append({
                    "strike": s["strike"], "type": side,
                    "oi": oi, "chg": chg,
                    "ltp": s[f"{side.lower()}_ltp"],
                    "pct_chg": round(chg / (oi - chg) * 100, 1) if (oi - chg) > 0 else 0,
                })
    rows.sort(key=lambda x: abs(x["chg"]), reverse=True)
    return rows[:top_n]


# ── FII / DII ──────────────────────────────────────────────────────────────

def fii_dii() -> dict | None:
    data = get("/api/fiidiiTradeReact")
    if not data:
        return None
    try:
        result = {}
        for row in data:
            cat = row.get("category", "").strip().upper()
            if "FII" in cat or "FPI" in cat:
                result["fii_buy"]  = row.get("buyValue")
                result["fii_sell"] = row.get("sellValue")
                result["fii_net"]  = row.get("netValue")
            elif "DII" in cat:
                result["dii_buy"]  = row.get("buyValue")
                result["dii_sell"] = row.get("sellValue")
                result["dii_net"]  = row.get("netValue")
        return result if result else None
    except Exception as e:
        log.warning("FII/DII parse: %s", e)
        return None


# ── Bulk & block deals ─────────────────────────────────────────────────────

def bulk_deals() -> list[dict]:
    data = get("/api/bulk-deal")
    out  = []
    if not data:
        return out
    for row in (data if isinstance(data, list) else data.get("data", [])):
        out.append({
            "date":   row.get("BD_DT_DATE"),
            "symbol": row.get("BD_SYMBOL"),
            "client": row.get("BD_CLIENT_NAME"),
            "type":   row.get("BD_BUY_SELL"),
            "qty":    row.get("BD_QTY_TRD"),
            "price":  row.get("BD_TP_WATP"),
        })
    return out


def block_deals() -> list[dict]:
    data = get("/api/block-deal")
    out  = []
    if not data:
        return out
    for row in (data if isinstance(data, list) else data.get("data", [])):
        out.append({
            "date":   row.get("BD_DT_DATE"),
            "symbol": row.get("BD_SYMBOL"),
            "client": row.get("BD_CLIENT_NAME"),
            "type":   row.get("BD_BUY_SELL"),
            "qty":    row.get("BD_QTY_TRD"),
            "price":  row.get("BD_TP_WATP"),
        })
    return out


# ── Corporate actions ──────────────────────────────────────────────────────

def corporate_actions(symbol: str) -> list[dict]:
    data = get(f"/api/corporateActions?index=equities&symbol={symbol.upper()}")
    out  = []
    if not data:
        return out
    for row in (data if isinstance(data, list) else []):
        out.append({
            "symbol":  row.get("symbol"),
            "ex_date": row.get("exDate"),
            "purpose": row.get("purpose"),
        })
    return out


def upcoming_events(symbols: list[str], days_ahead: int = 5) -> list[dict]:
    """Return corporate actions for given symbols in the next N days."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).date()
    today  = datetime.now(timezone.utc).date()
    events = []
    for sym in symbols:
        for ev in corporate_actions(sym):
            try:
                ex = datetime.strptime(ev["ex_date"], "%d-%b-%Y").date()
                if today <= ex <= cutoff:
                    ev["days_away"] = (ex - today).days
                    events.append(ev)
            except Exception:
                continue
    return sorted(events, key=lambda x: x["days_away"])
