"""
Confluence detection: fire an alert when 2+ independent channels
call the same instrument in the same direction on the same day.

Also provides net_bias(): detects CONSENSUS or SPLIT across channels
for the same instrument when directions disagree.
"""
import logging
from datetime import datetime
from config import db, IST

log = logging.getLogger(__name__)


def get_confluences(date_str: str, min_channels: int = 2) -> list[dict]:
    """
    Return signals where >= min_channels channels independently agree.
    Returns list of {instrument, direction, count, channels, avg_entry, avg_sl}.
    """
    with db() as conn:
        rows = conn.execute("""
            SELECT instrument, direction,
                   COUNT(DISTINCT channel) as cnt,
                   GROUP_CONCAT(DISTINCT channel) as channels,
                   GROUP_CONCAT(entry) as entries,
                   GROUP_CONCAT(sl)    as sls
            FROM signal_log
            WHERE date = ?
              AND direction IN ('BUY', 'SELL')
            GROUP BY instrument, direction
            HAVING cnt >= ?
            ORDER BY cnt DESC
        """, (date_str, min_channels)).fetchall()

    results = []
    for instrument, direction, cnt, channels, entries, sls in rows:
        entry_vals = _parse_nums(entries)
        sl_vals    = _parse_nums(sls)
        results.append({
            "instrument": instrument,
            "direction":  direction,
            "count":      cnt,
            "channels":   channels.split(","),
            "avg_entry":  round(sum(entry_vals) / len(entry_vals), 2) if entry_vals else None,
            "avg_sl":     round(sum(sl_vals)    / len(sl_vals),    2) if sl_vals    else None,
        })
    return results


def net_bias(date_str: str) -> list[dict]:
    """
    For every instrument with signals today, compute the net directional bias.

    Returns list of dicts (only instruments with >1 signal or a disagreement):
      {instrument, buys, sells, total, bias, label}

    bias values:
      STRONG_BUY   — 3+ channels BUY, 0 SELL
      BUY          — more BUY than SELL
      SPLIT        — |buys - sells| <= 1 and both > 0  (disagreement flag)
      SELL         — more SELL than BUY
      STRONG_SELL  — 3+ channels SELL, 0 BUY
    """
    with db() as conn:
        rows = conn.execute("""
            SELECT instrument,
                   SUM(CASE WHEN direction='BUY'  THEN 1 ELSE 0 END) as buys,
                   SUM(CASE WHEN direction='SELL' THEN 1 ELSE 0 END) as sells,
                   COUNT(*) as total
            FROM signal_log
            WHERE date = ?
              AND direction IN ('BUY', 'SELL')
            GROUP BY instrument
            HAVING total > 1
            ORDER BY total DESC
        """, (date_str,)).fetchall()

    results = []
    for instrument, buys, sells, total in rows:
        buys  = buys  or 0
        sells = sells or 0

        if buys >= 3 and sells == 0:
            bias = "STRONG_BUY"
        elif sells >= 3 and buys == 0:
            bias = "STRONG_SELL"
        elif buys > 0 and sells > 0 and abs(buys - sells) <= 1:
            bias = "SPLIT"
        elif buys > sells:
            bias = "BUY"
        elif sells > buys:
            bias = "SELL"
        else:
            bias = "NEUTRAL"

        results.append({
            "instrument": instrument,
            "buys":       buys,
            "sells":      sells,
            "total":      total,
            "bias":       bias,
        })
    return results


def format_confluence_alert(confluences: list[dict]) -> str | None:
    if not confluences:
        return None
    lines = ["[!!] <b>CONFLUENCE ALERT</b> -- Multiple channels agree\n"]
    for c in confluences:
        em     = "[+]" if c["direction"] == "BUY" else "[-]"
        is_opt = "CE" in c["instrument"] or "PE" in c["instrument"]
        line   = f"{em} <b>{c['instrument']}</b>  {c['direction']}  x {c['count']} channels"
        if c["avg_entry"] and is_opt: line += f"  avg entry {c['avg_entry']}"
        if c["avg_sl"]:               line += f"  avg SL {c['avg_sl']}"
        lines.append(line)
        lines.append(f"   └ {', '.join(c['channels'])}")
    return "\n".join(lines)


def format_bias_block(biases: list[dict]) -> str | None:
    """
    Format the net bias summary for inclusion in hourly report.
    Only surfaces STRONG_BUY, STRONG_SELL, and SPLIT — skips plain BUY/SELL
    (those are already visible in the per-channel signal list).
    """
    if not biases:
        return None

    strong  = [b for b in biases if b["bias"] in ("STRONG_BUY", "STRONG_SELL")]
    splits  = [b for b in biases if b["bias"] == "SPLIT"]

    if not strong and not splits:
        return None

    lines = ["[>>>] <b>SIGNAL BIAS</b>"]

    for b in strong:
        em    = "[+]" if b["bias"] == "STRONG_BUY" else "[-]"
        label = "STRONG BUY" if b["bias"] == "STRONG_BUY" else "STRONG SELL"
        lines.append(
            f"  {em} <b>{b['instrument']}</b>  {label}  "
            f"({b['buys']} BUY / {b['sells']} SELL across {b['total']} signals)"
        )

    for b in splits:
        lines.append(
            f"  [!!] <b>{b['instrument']}</b>  SPLIT -- "
            f"{b['buys']} BUY vs {b['sells']} SELL  (channels disagree)"
        )

    return "\n".join(lines)


def _parse_nums(s: str) -> list[float]:
    if not s:
        return []
    result = []
    for x in s.split(","):
        if not x or x == "None":
            continue
        try:
            result.append(float(x))
        except ValueError:
            pass
    return result
