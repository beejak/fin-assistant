"""
Weekly channel accuracy scorecard.
Runs every Monday morning — shows last week's hit rates per channel,
surfaces which channels are worth following and which to mute.
"""
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from collections import defaultdict

from config import DB_PATH, IST
from bot import send

log = logging.getLogger(__name__)


def run(dry_run: bool = False) -> None:
    now      = datetime.now(IST)
    week_end = (now - timedelta(days=1)).strftime("%Y-%m-%d")          # yesterday
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT channel, instrument, direction, entry, sl, targets,
                   result, result_note, date
            FROM signal_log
            WHERE date BETWEEN ? AND ?
              AND result != 'OPEN'
            ORDER BY date DESC
        """, (week_start, week_end)).fetchall()

    if not rows:
        send(f"📊 <b>Weekly Scorecard</b>\nNo graded signals for {week_start} → {week_end}",
             dry_run=dry_run)
        return

    by_channel = defaultdict(lambda: {"total":0,"tgt":0,"sl":0,"calls":[]})
    instrument_stats = defaultdict(lambda: {"total":0,"tgt":0,"sl":0})

    for channel, instrument, direction, entry, sl, targets, result, note, date in rows:
        ch = by_channel[channel]
        ch["total"] += 1
        if "TGT" in result: ch["tgt"] += 1
        elif result == "SL_HIT": ch["sl"] += 1
        ch["calls"].append({"instrument": instrument, "direction": direction,
                            "result": result, "date": date})

        ist = instrument_stats[instrument]
        ist["total"] += 1
        if "TGT" in result: ist["tgt"] += 1
        elif result == "SL_HIT": ist["sl"] += 1

    # ── Format ────────────────────────────────────────────────────────────
    L = []
    L.append(f"📈 <b>WEEKLY SCORECARD</b>")
    L.append(f"📅 {week_start}  →  {week_end}")
    L.append(f"📊 {len(rows)} graded signals across {len(by_channel)} channels")
    L.append("")

    # Sort by hit rate descending
    ranked = sorted(by_channel.items(),
                    key=lambda x: (x[1]["tgt"] / x[1]["total"] if x[1]["total"] else 0),
                    reverse=True)

    L.append("━━━━━━━━━━━━━━━━━━━")
    L.append("<b>🏆 CHANNEL RANKINGS</b>  (by hit rate)")
    L.append("")

    for i, (channel, stats) in enumerate(ranked, 1):
        t     = stats["total"]
        h     = stats["tgt"]
        s     = stats["sl"]
        o     = t - h - s
        pct   = round(h / t * 100) if t else 0
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"  {i}.")
        bar   = "▓" * h + "░" * s + "·" * o
        verdict = ("✅ FOLLOW" if pct >= 60 else
                   "⚠️ SELECTIVE" if pct >= 40 else
                   "❌ MUTE")
        L.append(f"{medal} <b>{channel[:30]}</b>")
        L.append(f"    {h}/{t} hit ({pct}%)  SL:{s}  Open:{o}  {verdict}")
        L.append(f"    [{bar}]")

    # Top instruments (most called)
    top_instruments = sorted(instrument_stats.items(),
                             key=lambda x: x[1]["total"], reverse=True)[:10]
    if top_instruments:
        L.append("")
        L.append("━━━━━━━━━━━━━━━━━━━")
        L.append("<b>🎯 TOP INSTRUMENTS THIS WEEK</b>")
        for instr, stats in top_instruments:
            t   = stats["total"]
            h   = stats["tgt"]
            pct = round(h / t * 100) if t else 0
            em  = "🟢" if pct >= 60 else ("🟡" if pct >= 40 else "🔴")
            L.append(f"  {em} <b>{instr}</b>  {h}/{t} called  ({pct}% hit)")

    # Overall
    total_all = sum(v["total"] for v in by_channel.values())
    total_tgt = sum(v["tgt"]   for v in by_channel.values())
    total_sl  = sum(v["sl"]    for v in by_channel.values())
    overall   = round(total_tgt / total_all * 100) if total_all else 0

    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━")
    L.append(f"<b>Overall:</b>  {total_tgt}/{total_all} hit ({overall}%)  "
             f"SL:{total_sl}  Accuracy grade: "
             f"{'A' if overall>=70 else 'B' if overall>=55 else 'C' if overall>=40 else 'D'}")

    send("\n".join(L), dry_run=dry_run)
    log.info("Weekly scorecard sent")
