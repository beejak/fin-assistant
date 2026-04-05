"""
Confluence detection: fire an alert when 2+ independent channels
call the same instrument in the same direction on the same day.
"""
import sqlite3
import logging
from datetime import datetime
from config import DB_PATH, IST

log = logging.getLogger(__name__)


def get_confluences(date_str: str, min_channels: int = 2) -> list[dict]:
    """
    Return signals where >= min_channels channels independently agree.
    Returns list of {instrument, direction, count, channels, entries, sls, targets}.
    """
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT instrument, direction,
                   COUNT(DISTINCT channel) as cnt,
                   GROUP_CONCAT(DISTINCT channel) as channels,
                   GROUP_CONCAT(entry)   as entries,
                   GROUP_CONCAT(sl)      as sls,
                   GROUP_CONCAT(targets) as targets_raw
            FROM signal_log
            WHERE date = ?
              AND direction IN ('BUY', 'SELL')
            GROUP BY instrument, direction
            HAVING cnt >= ?
            ORDER BY cnt DESC
        """, (date_str, min_channels)).fetchall()

    results = []
    for instrument, direction, cnt, channels, entries, sls, targets_raw in rows:
        # Parse entry prices and compute consensus
        def parse_nums(s):
            if not s: return []
            return [float(x) for x in s.split(",") if x and x != "None"]

        entry_vals = parse_nums(entries)
        sl_vals    = parse_nums(sls)

        results.append({
            "instrument": instrument,
            "direction":  direction,
            "count":      cnt,
            "channels":   channels.split(","),
            "avg_entry":  round(sum(entry_vals) / len(entry_vals), 2) if entry_vals else None,
            "avg_sl":     round(sum(sl_vals) / len(sl_vals), 2) if sl_vals else None,
        })
    return results


def format_confluence_alert(confluences: list[dict]) -> str | None:
    if not confluences:
        return None
    lines = ["[!!] <b>CONFLUENCE ALERT</b> -- Multiple channels agree\n"]
    for c in confluences:
        em = "[+]" if c["direction"] == "BUY" else "[-]"
        line = f"{em} <b>{c['instrument']}</b>  {c['direction']}  x {c['count']} channels"
        if c["avg_entry"]: line += f"  avg entry {c['avg_entry']}"
        if c["avg_sl"]:    line += f"  avg SL {c['avg_sl']}"
        lines.append(line)
        lines.append(f"   └ {', '.join(c['channels'])}")
    return "\n".join(lines)
