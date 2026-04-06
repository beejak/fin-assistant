"""
Extract structured trading signals from raw Telegram message text.
Returns: {direction, instrument, entry, sl, targets} or None.

Index-only mode: only signals for Nifty indices and their options are
extracted. Individual stock signals are ignored entirely.
"""
import re

SL_RE    = re.compile(r'(?:sl|stop\s*loss|stoploss)[:\s\-]*[\u20b9₹]?\s*(\d[\d,]*(?:\.\d+)?)', re.I)
TGT_RE   = re.compile(r'(?:tgt?\d*|trg\d*|target\s*\d?)[:\s\-]*[\u20b9₹]?\s*(\d[\d,]*(?:\.\d+)?)', re.I)
ENTRY_RE = re.compile(r'(?:entry|buy\s+at|cmp|above|near|@)[:\s\-]*[\u20b9₹]?\s*(\d[\d,]*(?:\.\d+)?)', re.I)
BUY_RE   = re.compile(r'\b(buy|long|bullish|accumulate)\b', re.I)
SELL_RE  = re.compile(r'\b(sell|short|bearish|exit|book)\b', re.I)
# Strip optional leading # (e.g. #NIFTY22450PE or #22450PE)
CE_PE_RE      = re.compile(r'#?(\d{4,6})\s*(CE|PE)\b', re.I)
OPT_STRIKE_RE = re.compile(r'^\d+(CE|PE)$', re.I)

# Canonical index names (longest first so BANKNIFTY matches before NIFTY)
# Maps every known alias → canonical name stored in signal_log
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

# Legacy constant kept for compatibility with other modules
NOISE = frozenset()


def _price(m) -> float | None:
    try:
        return float(m.group(1).replace(',', '')) if m else None
    except Exception:
        return None


def _find_index(text: str) -> str:
    """
    Return the canonical Nifty index name found in text, or ''.
    Tries longer/more-specific patterns first to avoid NIFTY matching
    inside BANKNIFTY.
    """
    for pattern, canonical in _INDEX_PATTERNS:
        if pattern.search(text):
            return canonical
    return ""


def extract(text: str) -> dict | None:
    """
    Parse a message for an index trading signal.
    Returns dict or None if no signal detected or instrument is not a
    recognised Nifty index / index option.
    """
    has_buy  = bool(BUY_RE.search(text))
    has_sell = bool(SELL_RE.search(text))
    ce_pe    = CE_PE_RE.search(text)

    if not (has_buy or has_sell or ce_pe):
        return None

    direction = "BUY" if has_buy else ("SELL" if has_sell else "")
    entry     = _price(ENTRY_RE.search(text))
    sl        = _price(SL_RE.search(text))
    targets   = [_price(m) for m in TGT_RE.finditer(text) if _price(m)]

    if ce_pe:
        strike, opt = ce_pe.group(1), ce_pe.group(2).upper()
        index = _find_index(text)
        # Bare strike with no index name → assume NIFTY (most common case)
        if not index:
            index = "NIFTY"
        instrument = f"{index} {strike}{opt}"
        # Entry: fall back to bare number after buy/near/@ keywords
        if not entry:
            m = re.search(
                r'(?:buy|cmp|entry|above|near|@)\s*[\u20b9₹]?\s*(\d+(?:\.\d+)?)',
                text, re.I
            )
            entry = _price(m)
    else:
        # Non-option signal: must name an index explicitly
        index = _find_index(text)
        if not index:
            return None   # individual stock → skip
        instrument = index

    return {
        "direction":  direction,
        "instrument": instrument,
        "entry":      entry,
        "sl":         sl,
        "targets":    targets[:3],
    }


def base_symbol(instrument: str) -> str:
    """Return the underlying index from an instrument string."""
    return instrument.split()[0].upper()


def is_index(instrument: str) -> bool:
    return base_symbol(instrument) in INDICES


def is_option(instrument: str) -> bool:
    return bool(CE_PE_RE.search(instrument))
