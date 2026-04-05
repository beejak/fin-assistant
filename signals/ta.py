"""
Technical analysis enrichment for stock signals.
Uses yfinance for OHLCV data + pandas_ta for indicators.
"""
import logging
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
try:
    import pandas_ta as ta
    HAS_TA = True
except ImportError:
    HAS_TA = False

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

log = logging.getLogger(__name__)

_cache: dict = {}          # symbol -> {data, fetched_at}
_CACHE_TTL_S  = 3600       # evict entries older than 1 hour
_CACHE_MAXSIZE = 150       # prevent unbounded growth; LRU-style eviction


def _evict_cache() -> None:
    """Remove entries older than TTL, then cap total size."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    stale = [k for k, v in _cache.items()
             if (now - v["fetched_at"]).total_seconds() > _CACHE_TTL_S]
    for k in stale:
        del _cache[k]
    # If still over max, remove oldest entries
    if len(_cache) > _CACHE_MAXSIZE:
        sorted_keys = sorted(_cache, key=lambda k: _cache[k]["fetched_at"])
        for k in sorted_keys[:len(_cache) - _CACHE_MAXSIZE]:
            del _cache[k]


def _fetch(symbol: str, period: str = "3mo") -> pd.DataFrame | None:
    if not HAS_YF:
        return None
    from datetime import datetime, timezone
    _evict_cache()
    cached = _cache.get(symbol)
    if cached:
        age = (datetime.now(timezone.utc) - cached["fetched_at"]).total_seconds()
        if age < _CACHE_TTL_S:
            return cached["data"]
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        df = ticker.history(period=period)
        if df.empty:
            return None
        _cache[symbol] = {"data": df, "fetched_at": datetime.now(timezone.utc)}
        return df
    except Exception as e:
        log.warning("yfinance %s: %s", symbol, e)
        return None


def enrich(symbol: str, ltp: float | None = None) -> dict:
    """
    Return TA state for a stock: RSI, SMA20, 52W position, trend.
    Falls back gracefully if data unavailable.
    """
    result = {
        "rsi":        None,
        "sma20":      None,
        "above_sma20": None,
        "w52_pct":    None,   # where in 52W range (0=at low, 100=at high)
        "trend":      None,   # UP | DOWN | SIDEWAYS
        "adx":        None,
    }

    df = _fetch(symbol)
    if df is None or len(df) < 21:
        return result

    close = df["Close"]

    if HAS_TA:
        try:
            rsi_series = ta.rsi(close, length=14)
            if rsi_series is not None and not rsi_series.empty:
                result["rsi"] = round(float(rsi_series.iloc[-1]), 1)
        except Exception:
            pass

        try:
            sma = ta.sma(close, length=20)
            if sma is not None and not sma.empty:
                s = float(sma.iloc[-1])
                result["sma20"] = round(s, 2)
                ref = ltp or float(close.iloc[-1])
                result["above_sma20"] = ref > s
        except Exception:
            pass

        try:
            adx_df = ta.adx(df["High"], df["Low"], close, length=14)
            if adx_df is not None and not adx_df.empty:
                adx_col = [c for c in adx_df.columns if c.startswith("ADX")]
                if adx_col:
                    result["adx"] = round(float(adx_df[adx_col[0]].iloc[-1]), 1)
        except Exception:
            pass
    else:
        # Fallback: manual RSI (pandas_ta not available)
        try:
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            # Guard: loss == 0 means pure uptrend → RSI = 100
            last_loss = float(loss.iloc[-1])
            if last_loss == 0:
                result["rsi"] = 100.0
            else:
                rs  = gain / loss.replace(0, float("nan"))
                rsi = 100 - (100 / (1 + rs))
                val = float(rsi.iloc[-1])
                if not (val != val):  # NaN check
                    result["rsi"] = round(val, 1)
        except Exception:
            pass
        sma = close.rolling(20).mean()
        result["sma20"] = round(float(sma.iloc[-1]), 2)
        ref = ltp or float(close.iloc[-1])
        result["above_sma20"] = ref > float(sma.iloc[-1])

    # 52W position
    w52_high = float(close.rolling(252, min_periods=50).max().iloc[-1])
    w52_low  = float(close.rolling(252, min_periods=50).min().iloc[-1])
    ref      = ltp or float(close.iloc[-1])
    if w52_high > w52_low:
        result["w52_pct"] = round((ref - w52_low) / (w52_high - w52_low) * 100, 1)

    # Trend (price vs SMA20 + slope)
    if result["sma20"] and result["rsi"] is not None:
        slope = float(close.iloc[-1]) - float(close.iloc[-5])
        if result["above_sma20"] and slope > 0:
            result["trend"] = "UP"
        elif not result["above_sma20"] and slope < 0:
            result["trend"] = "DOWN"
        else:
            result["trend"] = "SIDEWAYS"

    return result


def format_ta(ta_data: dict) -> str:
    """Return compact TA line for signal messages."""
    parts = []
    if ta_data.get("rsi") is not None:
        r = ta_data["rsi"]
        em = "🔥" if r > 70 else ("🧊" if r < 30 else "")
        parts.append(f"RSI {r}{em}")
    if ta_data.get("above_sma20") is not None:
        parts.append("▲SMA20" if ta_data["above_sma20"] else "▼SMA20")
    if ta_data.get("w52_pct") is not None:
        parts.append(f"52W@{ta_data['w52_pct']}%")
    if ta_data.get("trend"):
        em = {"UP": "📈", "DOWN": "📉", "SIDEWAYS": "➡️"}.get(ta_data["trend"], "")
        parts.append(f"{ta_data['trend']}{em}")
    return "  ".join(parts)
