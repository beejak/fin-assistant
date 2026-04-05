"""
Weekly channel accuracy scorecard.
Runs every Monday morning -- shows last week's hit rates per channel,
surfaces which channels are worth following and which to mute.
"""
import html
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from collections import defaultdict

from config import db, DB_PATH, IST
from bot import send
from bridge.discover import list_channels, set_active
from learning import channel_scores

log = logging.getLogger(__name__)


def run(dry_run: bool = False) -> None:
    now      = datetime.now(IST)
    week_end = (now - timedelta(days=1)).strftime("%Y-%m-%d")          # yesterday
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    with db() as conn:
        rows = conn.execute("""
            SELECT channel, instrument, direction, entry, sl, targets,
                   result, result_note, date
            FROM signal_log
            WHERE date BETWEEN ? AND ?
              AND result != 'OPEN'
            ORDER BY date DESC
        """, (week_start, week_end)).fetchall()

    if not rows:
        send(f"[DATA] <b>Weekly Scorecard</b>\nNo graded signals for {week_start} -> {week_end}",
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

    # -- Format ---------------------------------------------------------------
    L = []
    L.append(f"[UP] <b>WEEKLY SCORECARD</b>")
    L.append(f"[DATE] {week_start}  ->  {week_end}")
    L.append(f"[DATA] {len(rows)} graded signals across {len(by_channel)} channels")
    L.append("")

    # Sort by hit rate descending
    ranked = sorted(by_channel.items(),
                    key=lambda x: (x[1]["tgt"] / x[1]["total"] if x[1]["total"] else 0),
                    reverse=True)

    L.append("-------------------")
    L.append("<b>[RANK] CHANNEL RANKINGS</b>  (by hit rate)")
    L.append("")

    for i, (channel, stats) in enumerate(ranked, 1):
        t     = stats["total"]
        h     = stats["tgt"]
        s     = stats["sl"]
        o     = t - h - s
        pct   = round(h / t * 100) if t else 0
        medal = {1: "1.", 2: "2.", 3: "3."}.get(i, f"  {i}.")
        bar   = "#" * h + "." * s + "." * o
        verdict = ("[OK] FOLLOW" if pct >= 60 else
                   "[WARN] SELECTIVE" if pct >= 40 else
                   "[FAIL] MUTE")
        L.append(f"{medal} <b>{html.escape(channel[:30])}</b>")
        L.append(f"    {h}/{t} hit ({pct}%)  SL:{s}  Open:{o}  {verdict}")
        L.append(f"    [{bar}]")

    # Top instruments (most called)
    top_instruments = sorted(instrument_stats.items(),
                             key=lambda x: x[1]["total"], reverse=True)[:10]
    if top_instruments:
        L.append("")
        L.append("-------------------")
        L.append("<b>[TOP] TOP INSTRUMENTS THIS WEEK</b>")
        for instr, stats in top_instruments:
            t   = stats["total"]
            h   = stats["tgt"]
            pct = round(h / t * 100) if t else 0
            em  = "[+]" if pct >= 60 else ("[~]" if pct >= 40 else "[-]")
            L.append(f"  {em} <b>{instr}</b>  {h}/{t} called  ({pct}% hit)")

    # Overall
    total_all = sum(v["total"] for v in by_channel.values())
    total_tgt = sum(v["tgt"]   for v in by_channel.values())
    total_sl  = sum(v["sl"]    for v in by_channel.values())
    overall   = round(total_tgt / total_all * 100) if total_all else 0

    L.append("")
    L.append("-------------------")
    L.append(f"<b>Overall:</b>  {total_tgt}/{total_all} hit ({overall}%)  "
             f"SL:{total_sl}  Accuracy grade: "
             f"{'A' if overall>=70 else 'B' if overall>=55 else 'C' if overall>=40 else 'D'}")

    send("\n".join(L), dry_run=dry_run)
    log.info("Weekly scorecard sent")

    # -- Auto-mute: disable channels that consistently underperform -----------
    _auto_mute(dry_run=dry_run)


# Consecutive weeks a channel must underperform before auto-mute triggers.
# Prevents a single bad week from muting an otherwise good channel.
_MUTE_CONSECUTIVE_WEEKS = 4


def _auto_mute(dry_run: bool = False) -> None:
    """
    Check channel_scores for channels flagged suggest_mute=True for
    MUTE_CONSECUTIVE_WEEKS consecutive weekly runs. Disable them via
    set_active() and send a Telegram alert listing what was muted.

    Uses a new table `auto_mute_streak` to count consecutive bad weeks
    per channel. Resets the streak when a channel recovers above threshold.
    """
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auto_mute_streak (
                channel     TEXT PRIMARY KEY,
                streak      INTEGER DEFAULT 0,
                last_seen   TEXT
            )
        """)

    scores = channel_scores.update()
    active = {ch["name"]: ch for ch in list_channels(active_only=True)}
    now_iso = datetime.now(IST).strftime("%Y-%m-%d")

    muted_this_run = []

    with db() as conn:
        for channel, score in scores.items():
            if channel not in active:
                continue   # already inactive

            if score["suggest_mute"]:
                # Increment streak
                conn.execute("""
                    INSERT INTO auto_mute_streak (channel, streak, last_seen)
                    VALUES (?, 1, ?)
                    ON CONFLICT(channel) DO UPDATE SET
                        streak    = streak + 1,
                        last_seen = excluded.last_seen
                """, (channel, now_iso))
                streak = conn.execute(
                    "SELECT streak FROM auto_mute_streak WHERE channel=?",
                    (channel,)
                ).fetchone()[0]

                if streak >= _MUTE_CONSECUTIVE_WEEKS:
                    ch_id = active[channel]["id"]
                    if not dry_run:
                        set_active(ch_id, False)
                    muted_this_run.append(
                        (channel, score["hit_rate"], score["sl_hits"], streak)
                    )
                    log.warning(
                        "Auto-muted %s (hit rate %.0f%%, %d consecutive bad weeks)",
                        channel, score["hit_rate"] or 0, streak
                    )
            else:
                # Channel recovered — reset streak
                conn.execute(
                    "DELETE FROM auto_mute_streak WHERE channel=?", (channel,)
                )

    if not muted_this_run:
        log.info("Auto-mute: no channels muted this week")
        return

    lines = ["[AUTO-MUTE] The following channels were disabled after "
             f"{_MUTE_CONSECUTIVE_WEEKS} consecutive weeks below the "
             f"{channel_scores.MUTE_THRESHOLD}% hit-rate threshold:\n"]
    for ch, hr, sl, streak in muted_this_run:
        lines.append(f"  [-] <b>{ch}</b>  hit rate: {hr:.0f}%  "
                     f"SL hits: {sl}  bad weeks: {streak}")
    lines.append("\nRe-enable with: python main.py enable &lt;channel_id&gt;")

    send("\n".join(lines), dry_run=dry_run)
