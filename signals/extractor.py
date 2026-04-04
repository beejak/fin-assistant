"""
Extract structured trading signals from raw Telegram message text.
Returns: {direction, instrument, entry, sl, targets} or None.
"""
import re

SL_RE    = re.compile(r'(?:sl|stop\s*loss|stoploss)[:\s\-]*[\u20b9₹]?\s*(\d[\d,]*(?:\.\d+)?)', re.I)
TGT_RE   = re.compile(r'(?:tgt?\d*|target\s*\d*)[:\s\-]*[\u20b9₹]?\s*(\d[\d,]*(?:\.\d+)?)', re.I)
ENTRY_RE = re.compile(r'(?:entry|buy\s+at|cmp|above|near|@)[:\s\-]*[\u20b9₹]?\s*(\d[\d,]*(?:\.\d+)?)', re.I)
BUY_RE   = re.compile(r'\b(buy|long|bullish|accumulate)\b', re.I)
SELL_RE  = re.compile(r'\b(sell|short|bearish|exit|book)\b', re.I)
CE_PE_RE = re.compile(r'\b(\d{4,6})\s*(CE|PE)\b', re.I)
OPT_STRIKE_RE = re.compile(r'^\d+(CE|PE)$', re.I)

INDICES = frozenset({"NIFTY", "BANKNIFTY", "BNF", "SENSEX", "FINNIFTY", "MIDCPNIFTY"})

# Words that look like stock tickers but aren't
NOISE = frozenset({
    "BUY","SELL","LONG","SHORT","EXIT","ABOVE","BELOW","NEAR","TARGET","STOP",
    "LOSS","ENTRY","INTRADAY","BTST","STBT","CALL","PUT","OPTION","FUT",
    "FUTURES","LOT","SL","TGT","CMP","LTP","NSE","BSE","MCX","OI","PCR",
    "ATM","ITM","OTM","CE","PE","WEEKLY","MONTHLY","EXPIRY","APRIL","MAY",
    "JUNE","MARCH","JULY","MARKET","STOCK","TRADE","TODAY","HIGH","LOW",
    "OPEN","CLOSE","PROFIT","RISK","FREE","PAID","PREMIUM","DEMO","SIGNAL",
    "ALERT","UPDATE","NEWS","BREAKING","ALL","NOW","GOOD","MORNING","EVERY",
    "ONLY","ONE","PER","GLOBAL","INDIA","GIFT","WATCH","RISKY","HERO","SLS",
    "HOLD","URGENT","SMALL","LARGE","MEDIUM","INTRA","DAY","WEEK",
    "POSITIONAL","SWING","SCALP","HEDGED","TERM","BEING","SAFE","INVESTORS",
    "INVESTOR","TRUMP","MODI","IRAN","WAR","VIEW","IDEA","SETUP","PATTERN",
    "CHART","TECHNICAL","ANALYSIS","FUNDAMENTAL","MOMENTUM","VOLUME",
    "VOLATILITY","LIQUIDITY","CAPITAL","PORTFOLIO","WEALTH","GROWTH",
    "VALUE","QUALITY","DIVIDEND","YIELD","RETURN","ALPHA","BETA","GAMMA",
    "THETA","DELTA","VEGA","SERIES","POSITION","HEDGE","STRANGLE",
    "STRADDLE","SPREAD","CALENDAR","DIAGONAL","ALMOST","BAD","AND",
    "ASIAN","FOR","THE","THIS","THAT","ARE","WAS","HAS","HAVE","HAD",
    "WILL","WOULD","COULD","SHOULD","MAY","MIGHT","NOT","YES","CAN",
    "WHO","WHY","HOW","WHAT","WHEN","WHERE","WHICH","SOME","MANY","MUCH",
    "MORE","LESS","MOST","LAST","NEXT","EACH","BOTH","JUST","ALSO","STILL",
    "AGAIN","EVEN","BACK","AWAY","ALREADY","ALWAYS","NEVER","OFTEN",
    "VERY","WELL","BEST","NEW","OLD","BIG","STRONG","WEAK","POOR","RICH",
    "FAST","SLOW","FULL","HALF","PART","TOTAL","BOOKING","JACKPOT",
    "RESULT","BONUS","EARN","INVEST","JOIN","CONTACT","WHATSAPP",
    "TELEGRAM","CHANNEL","GROUP","RUNNING","MADE","TIME","PRICE","RATE",
    "LEVEL","POINT","POINTS","MOVE","RANGE","ZONE","AREA","LINE",
    "SUPPORT","RESISTANCE","TREND","BREAKOUT","BREAKDOWN","CORRECTION",
    "RALLY","FALL","RISE","DROP","SURGE","JUMP","CRASH","RECOVERY",
    "REBOUND","RETRACE","BOUNCE","FII","DII","FIIS","DIIS","PROVISIONAL",
    "PROV","EQUITY","CASH","INDICES","INDEX","DERIVATIVE","NIFTY50",
    "BANK","BANKING","SECTOR","AUTO","PHARMA","METAL","REALTY","INFRA",
    "NEAR","INSTL","PAR","OPERATORS","ADMIN","MEMBER","USER","CLIENT",
})


def _price(m) -> float | None:
    try:
        return float(m.group(1).replace(',', '')) if m else None
    except Exception:
        return None


def _find_instrument(text: str) -> str:
    """Return first uppercase token that looks like a real instrument name."""
    for word in re.findall(r'\b([A-Z][A-Z0-9&]{2,14})\b', text.upper()):
        if word not in NOISE and not OPT_STRIKE_RE.match(word) and not word[0].isdigit():
            return word
    return ""


def extract(text: str) -> dict | None:
    """
    Parse a message for a trading signal.
    Returns dict or None if no signal detected.
    """
    has_buy  = bool(BUY_RE.search(text))
    has_sell = bool(SELL_RE.search(text))
    ce_pe    = CE_PE_RE.search(text)

    if not (has_buy or has_sell or ce_pe):
        return None

    direction  = "BUY" if has_buy else ("SELL" if has_sell else "")
    instrument = _find_instrument(text)
    entry      = _price(ENTRY_RE.search(text))
    sl         = _price(SL_RE.search(text))
    targets    = [_price(m) for m in TGT_RE.finditer(text) if _price(m)]

    if ce_pe:
        strike, opt = ce_pe.group(1), ce_pe.group(2).upper()
        # Prefer index name over generic word
        for idx in ("SENSEX", "BANKNIFTY", "NIFTY", "FINNIFTY", "MIDCPNIFTY"):
            if idx in text.upper():
                instrument = idx
                break
        instrument = f"{instrument} {strike}{opt}".strip()
        if not entry:
            m = re.search(r'(?:buy|cmp|entry|above|near|@)\s*[\u20b9₹]?\s*(\d+(?:\.\d+)?)', text, re.I)
            entry = _price(m)

    if not instrument:
        return None

    return {
        "direction":  direction,
        "instrument": instrument,
        "entry":      entry,
        "sl":         sl,
        "targets":    targets[:3],
    }


def base_symbol(instrument: str) -> str:
    """Return the underlying symbol from an instrument string."""
    return instrument.split()[0].upper()


def is_index(instrument: str) -> bool:
    return base_symbol(instrument) in INDICES


def is_option(instrument: str) -> bool:
    return bool(CE_PE_RE.search(instrument))
