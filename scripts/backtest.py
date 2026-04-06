#!/usr/bin/env python3
"""
Backtesting — P&L analysis on historical signal_log data.

Uses day high/low grading (same as EOD grader), so gain/loss figures are
best-case estimates: assumes you got filled at entry and exited at the best
target hit or at SL. Not exact execution prices.

Usage:
    python3 scripts/backtest.py                         # last 30 days, all channels
    python3 scripts/backtest.py --days 90               # last 90 days
    python3 scripts/backtest.py --channel "BNOptions"   # single channel
    python3 scripts/backtest.py --min-confidence HIGH   # HIGH-scored channels only
    python3 scripts/backtest.py --direction BUY         # BUY signals only
    python3 scripts/backtest.py --instrument BANKNIFTY  # one instrument
    python3 scripts/backtest.py --send                  # send result to Telegram

Exit codes: 0 = OK, 1 = no data
"""
import sys
import json
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import db, IST

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [backtest] %(levelname)s %(message)s",
)
log = logging.getLogger("backtest")

COLS = ["id", "date", "channel", "instrument", "direction",
        "entry", "sl", "targets", "result", "result_note"]


# -- Helpers ------------------------------------------------------------------

def _pct(a, b) -> float | None:
    if not a or not b or b == 0:
        return None
    return round((a - b) / b * 100, 2)


def _gain_pct(sig: dict) -> float | None:
    """
    Estimate % gain for a winning signal.
    Uses the highest target that was hit vs entry price.
    """
    if not sig["entry"] or "TGT" not in sig["result"]:
        return None
    try:
        tgts = json.loads(sig["targets"] or "[]")
    except Exception:
        return None
    # How many targets were hit?
    n = int(sig["result"][3])  # TGT1_HIT → 1
    if n < 1 or n > len(tgts):
        return None
    tgt_price = tgts[n - 1]
    if not tgt_price:
        return None
    if sig["direction"] == "BUY":
        return _pct(tgt_price, sig["entry"])
    else:
        return _pct(sig["entry"], tgt_price)


def _loss_pct(sig: dict) -> float | None:
    """Estimate % loss for a stopped-out signal (entry → SL)."""
    if not sig["entry"] or not sig["sl"] or sig["result"] != "SL_HIT":
        return None
    if sig["direction"] == "BUY":
        return _pct(sig["sl"], sig["entry"])   # negative
    else:
        return _pct(sig["entry"], sig["sl"])   # negative


def _stats(signals: list[dict]) -> dict:
    """Compute summary statistics for a list of graded signals."""
    closed  = [s for s in signals if s["result"] != "OPEN"]
    wins    = [s for s in closed  if "TGT" in s["result"]]
    losses  = [s for s in closed  if s["result"] == "SL_HIT"]
    opens   = [s for s in signals if s["result"] == "OPEN"]

    total   = len(signals)
    n_close = len(closed)
    n_win   = len(wins)
    n_loss  = len(losses)
    n_open  = len(opens)

    win_rate = round(n_win / n_close * 100, 1) if n_close else None

    gain_pcts = [g for s in wins   if (g := _gain_pct(s)) is not None]
    loss_pcts = [l for s in losses if (l := _loss_pct(s)) is not None]

    avg_gain = round(sum(gain_pcts) / len(gain_pcts), 2) if gain_pcts else None
    avg_loss = round(sum(loss_pcts) / len(loss_pcts), 2) if loss_pcts else None

    # Expectancy = (win_rate * avg_gain) + (loss_rate * avg_loss)
    # avg_loss is already negative
    expectancy = None
    if win_rate is not None and avg_gain is not None and avg_loss is not None:
        wr = win_rate / 100
        lr = 1 - wr
        expectancy = round(wr * avg_gain + lr * avg_loss, 2)

    # Max consecutive losses
    max_streak = _max_loss_streak(closed)

    return {
        "total":       total,
        "closed":      n_close,
        "wins":        n_win,
        "losses":      n_loss,
        "open":        n_open,
        "win_rate":    win_rate,
        "avg_gain":    avg_gain,
        "avg_loss":    avg_loss,
        "expectancy":  expectancy,
        "max_streak":  max_streak,
    }


def _max_loss_streak(closed: list[dict]) -> int:
    """Maximum number of consecutive SL hits in chronological order."""
    streak = max_s = 0
    for s in sorted(closed, key=lambda x: x["date"]):
        if s["result"] == "SL_HIT":
            streak += 1
            max_s = max(max_s, streak)
        else:
            streak = 0
    return max_s


# -- Query --------------------------------------------------------------------

def load_signals(
    days: int,
    channel: str | None,
    direction: str | None,
    instrument: str | None,
    min_confidence: str | None,
) -> list[dict]:
    now      = datetime.now(IST)
    date_end = now.strftime("%Y-%m-%d")
    date_start = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    # Optionally filter by confidence via channel_scores table
    high_conf_channels: set[str] | None = None
    if min_confidence:
        conf_order = {"HIGH": 0, "MED": 1, "LOW": 2, "UNKNOWN": 3}
        min_rank   = conf_order.get(min_confidence.upper(), 3)
        with db() as conn:
            rows = conn.execute(
                "SELECT channel, confidence FROM channel_scores"
            ).fetchall()
        high_conf_channels = {
            ch for ch, conf in rows
            if conf_order.get(conf or "UNKNOWN", 3) <= min_rank
        }
        log.info("Confidence filter (%s+): %d channels match",
                 min_confidence, len(high_conf_channels))

    filters = [
        "date BETWEEN ? AND ?",
        "result != 'OPEN'",
        "direction IN ('BUY', 'SELL')",
    ]
    params: list = [date_start, date_end]

    if channel:
        filters.append("channel = ?")
        params.append(channel)
    if direction:
        filters.append("direction = ?")
        params.append(direction.upper())
    if instrument:
        filters.append("instrument LIKE ?")
        params.append(f"%{instrument.upper()}%")

    sql = f"""
        SELECT id, date, channel, instrument, direction,
               entry, sl, targets, result, result_note
        FROM signal_log
        WHERE {' AND '.join(filters)}
        ORDER BY date
    """
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()

    sigs = [dict(zip(COLS, r)) for r in rows]

    if high_conf_channels is not None:
        sigs = [s for s in sigs if s["channel"] in high_conf_channels]

    return sigs


# -- Formatter ----------------------------------------------------------------

def format_report(
    sigs: list[dict],
    days: int,
    channel: str | None,
    direction: str | None,
    instrument: str | None,
    min_confidence: str | None,
) -> str:
    now    = datetime.now(IST)
    st     = _stats(sigs)
    L      = []

    # Header
    L.append(f"[BACKTEST] <b>BACKTEST REPORT</b>  {now.strftime('%Y-%m-%d %H:%M IST')}")
    filters_str = "  |  ".join(filter(None, [
        f"last {days}d",
        f"channel={channel}" if channel else None,
        f"direction={direction}" if direction else None,
        f"instrument={instrument}" if instrument else None,
        f"min_confidence={min_confidence}" if min_confidence else None,
    ]))
    L.append(f"[FILTER] {filters_str}")
    L.append("")

    if not sigs:
        L.append("No graded signals found for the given filters.")
        return "\n".join(L)

    # Summary stats
    wr_str  = f"{st['win_rate']:.1f}%" if st["win_rate"] is not None else "N/A"
    exp_str = (f"{st['expectancy']:+.2f}%" if st["expectancy"] is not None else "N/A")
    ag_str  = (f"+{st['avg_gain']:.2f}%"  if st["avg_gain"]  is not None else "N/A")
    al_str  = (f"{st['avg_loss']:.2f}%"   if st["avg_loss"]  is not None else "N/A")

    L.append("<b>OVERALL</b>")
    L.append(f"  Signals : {st['total']} total  |  {st['closed']} graded  |  {st['open']} still open")
    L.append(f"  Results : {st['wins']} TGT hit  |  {st['losses']} SL hit")
    L.append(f"  Win rate: <b>{wr_str}</b>  (of closed signals)")
    L.append(f"  Avg gain: {ag_str}  |  Avg loss: {al_str}")
    L.append(f"  Expectancy: <b>{exp_str}</b> per trade")
    L.append(f"  Max consec. SL streak: {st['max_streak']}")
    L.append("")

    # Grade distribution
    grade_em = {
        "HIGH": ("A", "≥70% win rate"),
        "MED":  ("B", "55-69%"),
        "LOW":  ("C", "40-54%"),
        "FAIL": ("D", "<40%"),
    }
    if st["win_rate"] is not None:
        grade = ("HIGH" if st["win_rate"] >= 70 else
                 "MED"  if st["win_rate"] >= 55 else
                 "LOW"  if st["win_rate"] >= 40 else "FAIL")
        g_label, g_desc = grade_em[grade]
        L.append(f"  Grade: <b>{g_label}</b>  ({g_desc})")
        L.append("")

    # Per-channel breakdown (top 10 by total signals)
    by_ch: dict[str, list] = defaultdict(list)
    for s in sigs:
        by_ch[s["channel"]].append(s)

    ch_ranked = sorted(
        by_ch.items(),
        key=lambda x: _stats(x[1])["win_rate"] or 0,
        reverse=True
    )[:10]

    if len(by_ch) > 1:
        L.append("-------------------")
        L.append("<b>TOP CHANNELS</b>  (by win rate, min 3 graded signals)")
        for ch, ch_sigs in ch_ranked:
            cst = _stats(ch_sigs)
            if (cst["closed"] or 0) < 3:
                continue
            wr = f"{cst['win_rate']:.0f}%" if cst["win_rate"] is not None else "?"
            exp = (f"{cst['expectancy']:+.2f}%" if cst["expectancy"] is not None else "?")
            verdict = ("[OK]"   if (cst["win_rate"] or 0) >= 60 else
                       "[~]"    if (cst["win_rate"] or 0) >= 40 else "[-]")
            L.append(
                f"  {verdict} <b>{ch[:28]}</b>  "
                f"{cst['wins']}/{cst['closed']} ({wr})  exp {exp}"
            )
        L.append("")

    # Per-instrument breakdown
    by_instr: dict[str, list] = defaultdict(list)
    for s in sigs:
        # Group by base index (NIFTY 22400CE → NIFTY)
        base = s["instrument"].split()[0]
        by_instr[base].append(s)

    if len(by_instr) > 1:
        L.append("-------------------")
        L.append("<b>BY INDEX</b>")
        for instr, isigs in sorted(by_instr.items()):
            ist = _stats(isigs)
            if (ist["closed"] or 0) < 3:
                continue
            wr = f"{ist['win_rate']:.0f}%" if ist["win_rate"] is not None else "?"
            L.append(
                f"  <b>{instr}</b>  {ist['wins']}/{ist['closed']} TGT ({wr})  "
                f"SL:{ist['losses']}  open:{ist['open']}"
            )
        L.append("")

    # Disclaimer
    L.append("<i>Note: P&L based on day high/low — best-case estimates, not exact fills.</i>")
    return "\n".join(L)


# -- Main ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",           type=int,  default=30)
    parser.add_argument("--channel",        type=str,  default=None)
    parser.add_argument("--direction",      type=str,  default=None, choices=["BUY","SELL","buy","sell"])
    parser.add_argument("--instrument",     type=str,  default=None)
    parser.add_argument("--min-confidence", type=str,  default=None, choices=["HIGH","MED","LOW","high","med","low"])
    parser.add_argument("--send",           action="store_true", help="Send result to Telegram")
    args = parser.parse_args()

    sigs = load_signals(
        days=args.days,
        channel=args.channel,
        direction=args.direction,
        instrument=args.instrument,
        min_confidence=args.min_confidence,
    )

    log.info("Loaded %d graded signals", len(sigs))

    report = format_report(
        sigs,
        days=args.days,
        channel=args.channel,
        direction=args.direction,
        instrument=args.instrument,
        min_confidence=args.min_confidence,
    )

    print(report)

    if args.send:
        from bot import send
        send(report)
        log.info("Report sent to Telegram")

    return 0 if sigs else 1


if __name__ == "__main__":
    sys.exit(main())
