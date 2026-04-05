# Future Ideas

Captured from session discussions. Ordered by impact, not effort.

---

## 1. Close the feedback loop (channel scoring → auto-mute)

`learning/channel_scores.py` computes hit rates. The weekly report shows them.
Nobody acts on them automatically.

What's missing: a threshold rule (e.g. hit rate < 30% over 4 consecutive weeks)
that calls `bridge/discover.py:disable_channel()` and sends a Telegram notice.
One function, wired into the weekly job.

Without this, the EOD grader collects data forever but the system never gets smarter.
This is the highest-leverage gap in the entire codebase.

---

## 2. Log rotation

`logs/cron.log`, `logs/bridge.log`, `logs/price_monitor.log` grow unbounded.
A logrotate config — 20 lines — prevents an eventual disk-full outage.
The healthcheck already alerts at 500 MB free; rotation prevents ever getting there.

---

## 3. Backtesting

Replay `signal_log` entries against historical NSE OHLC data (available via
`yfinance` for daily, NSE for intraday). Compute actual hit rates independent
of the live grader. Useful for:
- Validating the grader's logic against ground truth
- Bootstrapping channel scores before 4 weeks of live data accumulates
- Finding the signal types (BUY/SELL, large/small cap, options/equity) that
  actually work across all channels

Prerequisite: the scoring pipeline (#1) should be working first.

---

## 4. Confidence tiers on hourly alerts

The hourly report already has confluence (2+ channels calling same stock).
Add a simple tier: HIGH (confluence + channel score > 60%), MEDIUM, LOW.
Surface it in the Telegram message so the reader can triage instantly.
No new data needed — everything is already in the DB.

---

## 5. Options OI as independent signals

Unusual strike OI buildup (already tracked in `oi_snapshots`) is currently
context-only. Large sudden buildup at a specific strike often precedes a move.
A simple threshold alert (e.g. OI at strike X doubled in one hour) would
generate signals independent of any Telegram channel — pure market structure.

---

## 6. Web dashboard

A lightweight read-only Flask page:
- Today's open signals with live NSE LTP
- Channel scorecard (hit rate, last 30 days)
- Heartbeat status for all 4 recovery layers
- DB message count, last bridge write time

Currently everything is Telegram-only. One screen to look at during market
hours would be more comfortable than polling the bot.

---

## 7. P&L tracker

If signals are actually acted on, track actual entry/exit against the signal
and compute real returns per channel. Separate from the grader (which uses
day high/low, not actual fill prices). Would require manual or broker-API
trade logging.

---

## 8. DB migration system

Schema changes are currently handled with `ALTER TABLE IF NOT EXISTS` scattered
across multiple files. A single `db/migrate.py` with versioned migrations would
make schema evolution safe and auditable. Low urgency for a solo project.

---

## 9. Broker integration (long-term)

Zerodha Kite API supports paper trading and live orders. High-confidence signals
(tier HIGH from #4) could be auto-executed as paper trades to measure real
slippage and fill rates before committing capital. Long-term only — requires
significant testing and risk controls.

---

## Not worth doing

- WhatsApp group scraping: brittle, ToS violation, Telegram coverage is sufficient
- Grafana/Prometheus: overkill for a single-user system; the healthcheck + Telegram covers it
- Containerisation: adds operational complexity with no benefit on a single machine
