"""
Extract structured trading signals from raw Telegram message text.
Returns: {direction, instrument, entry, sl, targets} or None.

Three extraction modes (pass mode= to extract()):

  'indices'  (default) — Nifty indices and their options only.
             NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, NIFTYNXT50.

  'stocks'   — Individual NSE equities and their options.
             Symbol validated against nse_symbols table (falls back to
             a heuristic if table is empty). Excludes index signals.

  'futures'  — Futures contracts for any NSE symbol or index.
             Detects SYMBOL FUT / SYMBOL FUTURES patterns.
             Covers both index futures and stock futures.
"""
import re
import logging

log = logging.getLogger(__name__)

# -- Common regexes -----------------------------------------------------------

SL_RE    = re.compile(r'(?:sl|stop\s*loss|stoploss)[:\s\-]*[\u20b9₹]?\s*(\d[\d,]*(?:\.\d+)?)', re.I)
TGT_RE   = re.compile(r'(?:tgt?\d*|trg\d*|target\s*\d?)[:\s\-]*[\u20b9₹]?\s*(\d[\d,]*(?:\.\d+)?)', re.I)
ENTRY_RE = re.compile(r'(?:entry|buy\s+at|cmp|above|near|@)[:\s\-]*[\u20b9₹]?\s*(\d[\d,]*(?:\.\d+)?)', re.I)
BUY_RE   = re.compile(r'\b(buy|long|bullish|accumulate)\b', re.I)
SELL_RE  = re.compile(r'\b(sell|short|bearish|exit|book)\b', re.I)

# Matches bare index-level strikes (#NIFTY22450PE, 22400CE, 52500 CE)
CE_PE_RE      = re.compile(r'#?(\d{4,6})\s*(CE|PE)\b', re.I)
# Matches stock option strikes: SYMBOL 2950CE / SYMBOL 2950 CE
STOCK_OPT_RE  = re.compile(r'\b([A-Z][A-Z0-9&]{1,14})\s+(\d{3,6})\s*(CE|PE)\b', re.I)
# Futures: SYMBOL FUT / SYMBOL FUTURES
FUT_RE        = re.compile(r'\b([A-Z][A-Z0-9&]{1,14})\s+(?:FUT|FUTURES?)\b', re.I)

OPT_STRIKE_RE = re.compile(r'^\d+(CE|PE)$', re.I)

# -- Index definitions --------------------------------------------------------

_INDEX_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\bBANK\s*NIFTY\b',  re.I), "BANKNIFTY"),
    (re.compile(r'\bBANKNIFTY\b',     re.I), "BANKNIFTY"),
    (re.compile(r'\bBNF\b',           re.I), "BANKNIFTY"),
    (re.compile(r'\bFINNIFTY\b',      re.I), "FINNIFTY"),
    (re.compile(r'\bMIDCPNIFTY\b',    re.I), "MIDCPNIFTY"),
    (re.compile(r'\bNIFTYNXT50\b',    re.I), "NIFTYNXT50"),
    (re.compile(r'\bNIFTY\s*50\b',    re.I), "NIFTY"),
    (re.compile(r'\bNIFTY\b',         re.I), "NIFTY"),
    (re.compile(r'\bSENSEX\b',        re.I), "SENSEX"),
]

INDICES = frozenset(p[1] for p in _INDEX_PATTERNS)

# Legacy constant — kept for compatibility with other modules
NOISE = frozenset()

# Words that are never valid stock symbols
_SYM_NOISE = frozenset({
    "BUY","SELL","LONG","SHORT","EXIT","ABOVE","BELOW","NEAR","TARGET","STOP",
    "LOSS","ENTRY","INTRADAY","BTST","STBT","CALL","PUT","OPTION","FUT",
    "FUTURES","FUTURE","LOT","SL","TGT","CMP","LTP","NSE","BSE","MCX","OI",
    "PCR","ATM","ITM","OTM","CE","PE","WEEKLY","MONTHLY","EXPIRY","MARKET",
    "STOCK","TRADE","TODAY","HIGH","LOW","OPEN","CLOSE","PROFIT","RISK",
    "FREE","PAID","PREMIUM","SIGNAL","ALERT","UPDATE","NEWS","BREAKING",
    "INDIA","GIFT","HOLD","URGENT","INTRA","DAY","WEEK","POSITIONAL","SWING",
    "SCALP","TERM","SAFE","FII","DII","EQUITY","CASH","INDEX","INDICES",
    "BANK","SECTOR","AUTO","PHARMA","METAL","REALTY","INFRA","VIEW","IDEA",
    "SETUP","PATTERN","CHART","TECHNICAL","ANALYSIS","FUNDAMENTAL","MOMENTUM",
    "SUPPORT","RESISTANCE","TREND","BREAKOUT","CORRECTION","RALLY","RECOVERY",
    "RESULT","BONUS","JOIN","TELEGRAM","CHANNEL","GROUP","PRICE","RATE",
    "LEVEL","POINT","POINTS","MOVE","RANGE","ZONE","AREA","LINE","SERIES",
    "NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX","NIFTYNXT50","BNF",
    "TRG","SLS","PROV","PAR","ADMIN","USER","CLIENT","MEMBER",
})

# -- NSE symbol set (loaded lazily from DB) -----------------------------------

_nse_symbols: frozenset[str] | None = None


def _load_nse_symbols() -> frozenset[str]:
    global _nse_symbols
    if _nse_symbols is not None:
        return _nse_symbols
    try:
        from config import db as _db
        with _db() as conn:
            rows = conn.execute(
                "SELECT symbol FROM nse_symbols WHERE type='equity'"
            ).fetchall()
        _nse_symbols = frozenset(r[0].upper() for r in rows)
        log.debug("Loaded %d NSE equity symbols", len(_nse_symbols))
    except Exception as e:
        log.warning("Could not load nse_symbols table: %s — stock mode will use heuristic", e)
        _nse_symbols = frozenset()
    return _nse_symbols


def reload_nse_symbols() -> None:
    """Force reload of the NSE symbol cache (call after refresh_nse_symbols runs)."""
    global _nse_symbols
    _nse_symbols = None
    _load_nse_symbols()


# -- Helpers ------------------------------------------------------------------

def _price(m) -> float | None:
    try:
        return float(m.group(1).replace(',', '')) if m else None
    except Exception:
        return None


def _common(text: str) -> tuple[bool, bool, float | None, float | None, list]:
    """Parse direction, entry, SL, targets common to all modes."""
    has_buy  = bool(BUY_RE.search(text))
    has_sell = bool(SELL_RE.search(text))
    entry    = _price(ENTRY_RE.search(text))
    sl       = _price(SL_RE.search(text))
    targets  = [_price(m) for m in TGT_RE.finditer(text) if _price(m)]
    return has_buy, has_sell, entry, sl, targets


def _find_index(text: str) -> str:
    """Return canonical Nifty index name found in text, or ''."""
    for pattern, canonical in _INDEX_PATTERNS:
        if pattern.search(text):
            return canonical
    return ""


def _is_valid_stock(sym: str) -> bool:
    """
    Return True if sym looks like a real NSE equity symbol.
    Uses the DB symbol set if populated; falls back to a heuristic.
    """
    sym = sym.upper()
    if sym in _SYM_NOISE or sym in INDICES:
        return False
    syms = _load_nse_symbols()
    if syms:
        return sym in syms
    # Heuristic when table is empty: 2-15 uppercase letters/digits, starts with letter
    return bool(re.match(r'^[A-Z][A-Z0-9&]{1,14}$', sym))


# -- Mode extractors ----------------------------------------------------------

def _extract_indices(text: str) -> dict | None:
    """Extract signals for Nifty indices and their options only."""
    has_buy, has_sell, entry, sl, targets = _common(text)
    ce_pe = CE_PE_RE.search(text)

    if not (has_buy or has_sell or ce_pe):
        return None

    direction = "BUY" if has_buy else ("SELL" if has_sell else "")

    if ce_pe:
        strike, opt = ce_pe.group(1), ce_pe.group(2).upper()
        strike_int = int(strike)

        # If a non-index stock symbol immediately precedes this CE/PE in the
        # text, it's a stock option — skip it in indices mode.
        stock_m = STOCK_OPT_RE.search(text)
        if stock_m:
            sym_before = stock_m.group(1).upper()
            if sym_before not in INDICES and sym_before not in _SYM_NOISE:
                return None   # e.g. "GODREJCP 1040CE" → stock option, not NIFTY

        # Strike number is unambiguous — override any text-based guess.
        # SENSEX ~75k, BANKNIFTY ~52k, NIFTY ~23k (thresholds with room).
        if strike_int >= 60000:
            index = "SENSEX"
        elif strike_int >= 30000:
            index = "BANKNIFTY"
        else:
            index = _find_index(text) or "NIFTY"
            # Safety net: genuine NIFTY strikes are always >= 15000.
            # Anything lower is a mis-attributed stock/commodity option.
            if strike_int < 15000:
                return None
        instrument = f"{index} {strike}{opt}"
        if not entry:
            m = re.search(
                r'(?:buy|cmp|entry|above|near|@)\s*[\u20b9₹]?\s*(\d+(?:\.\d+)?)',
                text, re.I
            )
            entry = _price(m)
    else:
        index = _find_index(text)
        if not index:
            return None
        instrument = index

    return {"direction": direction, "instrument": instrument,
            "entry": entry, "sl": sl, "targets": targets[:3]}


def _extract_stocks(text: str) -> dict | None:
    """
    Extract signals for individual NSE equity stocks and their options.
    Index signals are excluded.
    """
    has_buy, has_sell, entry, sl, targets = _common(text)

    # Try stock option first: SYMBOL STRIKEPE/CE
    stock_opt = STOCK_OPT_RE.search(text)
    if stock_opt:
        sym    = stock_opt.group(1).upper()
        strike = stock_opt.group(2)
        opt    = stock_opt.group(3).upper()
        # Exclude if sym is an index
        if sym in INDICES:
            return None
        if not _is_valid_stock(sym):
            return None
        direction  = "BUY" if has_buy else ("SELL" if has_sell else "")
        instrument = f"{sym} {strike}{opt}"
        if not entry:
            m = re.search(
                r'(?:buy|cmp|entry|above|near|@)\s*[\u20b9₹]?\s*(\d+(?:\.\d+)?)',
                text, re.I
            )
            entry = _price(m)
        return {"direction": direction, "instrument": instrument,
                "entry": entry, "sl": sl, "targets": targets[:3]}

    # Plain stock signal: need BUY or SELL keyword + valid symbol
    if not (has_buy or has_sell):
        return None

    # Find first valid stock symbol in text (scan tokens in order)
    for token in re.findall(r'\b([A-Z][A-Z0-9&]{1,14})\b', text.upper()):
        if token in INDICES:
            continue
        if _is_valid_stock(token):
            direction = "BUY" if has_buy else "SELL"
            return {"direction": direction, "instrument": token,
                    "entry": entry, "sl": sl, "targets": targets[:3]}

    return None


def _extract_futures(text: str) -> dict | None:
    """
    Extract futures signals: SYMBOL FUT / SYMBOL FUTURES.
    Covers both index futures (NIFTY FUT) and stock futures (RELIANCE FUT).
    """
    has_buy, has_sell, entry, sl, targets = _common(text)
    fut = FUT_RE.search(text)

    if not fut:
        return None
    if not (has_buy or has_sell):
        return None

    sym = fut.group(1).upper()

    # Resolve index alias
    for _, canonical in _INDEX_PATTERNS:
        if sym == canonical or sym in ("BNF",):
            sym = "BANKNIFTY" if sym == "BNF" else canonical
            break

    # Validate: must be an index or a known stock
    if sym not in INDICES and not _is_valid_stock(sym):
        return None

    direction  = "BUY" if has_buy else "SELL"
    instrument = f"{sym} FUT"
    return {"direction": direction, "instrument": instrument,
            "entry": entry, "sl": sl, "targets": targets[:3]}


# -- Public API ---------------------------------------------------------------

def extract(text: str, mode: str = "indices") -> dict | None:
    """
    Parse a message for a trading signal.

    mode:
      'indices'  — Nifty indices + index options (default)
      'stocks'   — Individual NSE equities + stock options
      'futures'  — Futures contracts (index or stock)

    Returns dict {direction, instrument, entry, sl, targets} or None.
    """
    if mode == "indices":
        return _extract_indices(text)
    elif mode == "stocks":
        return _extract_stocks(text)
    elif mode == "futures":
        return _extract_futures(text)
    else:
        raise ValueError(f"Unknown extraction mode: {mode!r}")


def base_symbol(instrument: str) -> str:
    """Return the underlying symbol from an instrument string."""
    return instrument.split()[0].upper()


def is_index(instrument: str) -> bool:
    return base_symbol(instrument) in INDICES


def is_option(instrument: str) -> bool:
    return bool(CE_PE_RE.search(instrument) or STOCK_OPT_RE.search(instrument))


def is_future(instrument: str) -> bool:
    return instrument.upper().endswith(" FUT")
