# Financial Assistant

A self-hosted, fault-tolerant market signal aggregator for Indian equities. Connects to your personal Telegram account via MTProto, harvests trading signals from every group and channel you subscribe to, validates each call against live NSE data, and delivers structured briefings to your private bot — hourly during market hours with a full EOD accuracy report and intraday price alerts the moment a target or stop-loss is hit.

No hardcoded channel lists. No subscriptions. No external APIs beyond NSE and Yahoo Finance. Works entirely with what you are already subscribed to.

---

## Architecture

```
Your Telegram account
        │
        │  Pyrogram · MTProto (personal account, not a bot)
        ▼
bridge/tg_bridge.py ─────── live message listener (systemd: fin-bridge)
        │
        │  writes to SQLite
        ▼
store/messages.db ──────────────────────────────────────────────────
        │
        ├── signals/extractor.py  ── regex + heuristic signal parser
        ├── enrichers/            ── NSE live, TA, OI velocity, events
        │
        │  reads/writes
        ▼
main.py [mode] ─────────── analysis engine + Telegram reporter
        │
        ├── preopen   8:45 AM    GIFT Nifty gap · VIX · FII/DII · overnight signals
        ├── hourly    9:45–3:15  New signals · NSE live check · TA · OI · confluence
        ├── eod       3:45 PM    Grade every call: TGT hit / SL hit / open · scorecard
        └── weekly    Mon 8 AM   Hit rate per channel · mute recommendations

scripts/price_monitor.py ── intraday daemon (every 5 min via cron)
        └── alerts the moment any open signal hits its target or SL
```

---

## Resilience — four independent recovery layers

The scheduler is built in concentric rings. Each layer operates completely independently; a failure at any level automatically escalates to the next.

```
Layer 1 · cron_guard.sh
  Wraps every cron job. On failure: retry 3× with 60 s backoff.
  On success: write heartbeat to logs/heartbeats/<job>.last_ok.
  After 3 failures: schedule a one-off recovery attempt via atd,
  then alert Telegram with log tail and manual override hint.

Layer 2 · watchdog.sh  (cron: every 30 min, 7:30 AM – 4:30 PM IST)
  Reads heartbeat files. If any critical job missed its window,
  re-runs it via cron_guard and sends a recovery alert.
  Completely independent of cron_guard — separate cron entry.

Layer 3 · atd fallback
  Scheduled by cron_guard as a last resort when all 3 retries fail.
  One-shot execution 10 minutes after the failure is declared.

Layer 4 · fin-scheduler  (systemd: Restart=always)
  Persistent Python process. Checks the clock every 30 s.
  Fires any job 5 minutes after its scheduled slot if the heartbeat
  shows cron never ran it. Survives crashes via systemd auto-restart.
  Sends [FAILSAFE] Telegram alerts when it acts.
```

If every automated layer is down, send a `/run_preopen` (or any `/run_*`) command to your bot — the `fin-bot-listener` service handles it instantly from your phone.

---

## Tested and verified

The entire recovery chain was stress-tested with a 54-case automated suite (`scripts/stress_test.py`). Tests cover unit logic, component behaviour, cross-layer integration, failure injection, and concurrency. **54 / 54 passed.**

```
Section  Tests  What was covered
───────  ─────  ──────────────────────────────────────────────────────
A           9   is_market_open: all 15 NSE holidays, weekends, future years
B           7   ran_today: missing / empty / corrupt / stale heartbeats
C           8   check_schedule: window boundaries, dedup, holiday skip, full-day sim
D           6   cron_guard.sh: retry exactly 3×, recovery message, heartbeat format
E           6   watchdog.sh: IST date handling, weekend/holiday early exit
F           8   Failure injection: TimeoutExpired, OSError, bad exit, race condition
G           5   Layer handoff: heartbeat written by L1 prevents L4 re-fire, and vice versa
H           5   Stress: 100 concurrent threads, 1000+ poll iterations, SIGKILL recovery
───────  ─────
Total      54   0 failures · 0 errors
```

**Bugs caught by the tests and a subsequent code sanity pass:**

| Bug | Severity | Fixed |
|---|---|---|
| `scheduler.py` wrote heartbeats with `datetime.utcnow()` while `ran_today()` compared against IST — mismatch after 18:30 IST caused Layer 4 to re-fire jobs that already ran | Critical | `datetime.now(IST)` throughout |
| `watchdog.sh` `ran_today()` used `date -u` (UTC) while `scheduler.py` used IST — same mismatch from the bash side | Critical | `TZ=Asia/Kolkata date` throughout |
| `reports/hourly.py` and `reports/preopen.py` used bare `conn = sqlite3.connect()` + `conn.close()` — connection leaked on any exception between the two calls | Medium | Converted to `with sqlite3.connect() as conn` |
| `bridge/discover.py` built SQL via f-string (`f"... {where}"`) — wrong pattern even though input was safe | Medium | Replaced with two explicit queries |
| `nse/client.py` `gift_nifty()` returned `idx.get("GIFT NIFTY") or idx.get("INDIA VIX") and None` — Python operator precedence made it return `None` whenever GIFT NIFTY was absent and INDIA VIX was present | Medium | Simplified to `idx.get("GIFT NIFTY")` |
| `nse/client.py` `upcoming_events()` — dead function, never called anywhere, used UTC instead of IST, duplicated `enrichers/events.py` | Low | Deleted |
| `cron_guard.sh` has no `flock` — two simultaneous instances both execute the job | Known | Documented; jobs are idempotent so risk is low |

**Key things confirmed working:**

- A heartbeat written by any layer is correctly read by every other layer
- All 15 NSE 2026 holidays block execution in both Python and Bash
- `fin-scheduler` auto-restarts within 12 seconds after `kill -9`
- 100 concurrent `ran_today()` calls return consistent results with zero torn reads
- A full Monday schedule fires exactly 10 slots across 600+ simulated poll minutes

Full findings: [`TEST_REPORT.md`](TEST_REPORT.md)

```bash
# Run the suite yourself (no network required, no live NSE/Telegram calls)
python3 scripts/stress_test.py
```

---

## Setup

### Requirements

- Ubuntu / Debian / WSL2 (tested on Ubuntu 22.04 and WSL2)
- Python 3.11+
- A personal Telegram account (MTProto — not a bot token)
- A Telegram bot for receiving reports (create one via [@BotFather](https://t.me/BotFather))
- `atd` running (`apt-get install at`)

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/fin-assistant.git
cd fin-assistant
./scripts/setup.sh
```

### 2. Configure

```bash
cp .env.example .env
nano .env
```

| Variable | Where to get it |
|---|---|
| `TG_API_ID` / `TG_API_HASH` | [my.telegram.org/apps](https://my.telegram.org/apps) |
| `TG_SESSION` | Local path for the Pyrogram session file (no extension) |
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) |
| `OWNER_CHAT_ID` | Send `/start` to your bot, then `getUpdates` via the Bot API |

### 3. Discover your channels (one-time; re-run after joining new ones)

```bash
python main.py discover --dry    # preview what will be found
python main.py discover          # save to DB
python main.py channels          # verify the list
```

Selectively mute irrelevant sources without losing history:

```bash
python main.py disable -1001234567890   # stop monitoring a channel
python main.py enable  -1001234567890   # re-enable it
```

### 4. Install cron jobs and systemd services

```bash
# Cron (all market-hour jobs including watchdog and price monitor)
crontab systemd/crontab.txt

# Core bridge
cp systemd/fin-bridge.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fin-bridge

# Failsafe scheduler (Layer 4)
cp systemd/fin-scheduler.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fin-scheduler

# Telegram bot command listener
cp systemd/fin-bot-listener.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fin-bot-listener
```

### 5. Backfill and verify

```bash
python main.py fetch 7           # backfill last 7 days of history
python main.py hourly --dry-run  # print a report without sending to Telegram
python main.py eod    --dry-run
```

---

## Schedule (Mon–Fri, IST)

| Time | Job | What it does |
|---|---|---|
| 8:00 AM (Mon) | weekly | Channel hit-rate scorecard, mute recommendations |
| 8:30 AM | healthcheck --report | Full stack status summary to Telegram before market opens |
| 8:45 AM | preopen | GIFT Nifty gap · VIX · FII/DII · overnight signal digest |
| 9:45 – 3:15 PM | hourly ×7 | New signals · NSE live validation · TA · OI · confluence |
| 3:45 PM | eod | Grade every open call: target hit / SL hit / still open |
| Every 5 min (9:15–3:30) | price_monitor | Intraday alert the moment a target or SL is breached |
| Every 15 min (9:00–3:45) | healthcheck | Bridge alive · NSE reachable · DB writable · disk OK |
| Every 30 min (7:30–4:30) | watchdog | Re-run any cron job that missed its heartbeat window |
| 11:30 PM | backup | DB + session + .env → tar.gz, optional upload to Google Drive |

All times are IST. Cron entries use UTC (IST = UTC+5:30). See [`systemd/crontab.txt`](systemd/crontab.txt) for the exact entries.

---

## Market holidays

All jobs — scheduler, watchdog, price monitor, healthcheck — automatically skip NSE market holidays. No manual intervention needed.

The 2026 NSE equity holiday list is maintained in `config.py` (`NSE_HOLIDAYS`) and mirrored in `watchdog.sh`. Both use IST date for comparison so there is no UTC/IST boundary issue.

**2026 NSE trading holidays (equity / cash segment):**

| Date | Day | Holiday |
|---|---|---|
| 26 Jan | Monday | Republic Day |
| 03 Mar | Tuesday | Holi |
| 26 Mar | Thursday | Shri Ram Navami |
| 31 Mar | Tuesday | Shri Mahavir Jayanti |
| 03 Apr | Friday | Good Friday |
| 14 Apr | Tuesday | Dr. Baba Saheb Ambedkar Jayanti |
| 01 May | Friday | Maharashtra Day |
| 28 May | Thursday | Bakri Id (Eid ul-Adha) |
| 26 Jun | Friday | Muharram |
| 14 Sep | Monday | Ganesh Chaturthi |
| 02 Oct | Friday | Mahatma Gandhi Jayanti |
| 20 Oct | Tuesday | Dussehra |
| 10 Nov | Tuesday | Diwali — Balipratipada |
| 24 Nov | Tuesday | Prakash Gurpurb (Guru Nanak Jayanti) |
| 25 Dec | Friday | Christmas |

Source: NSE annual circular, verified via Groww and IntegratedIndia. To update for a new year, add an entry to `NSE_HOLIDAYS` in `config.py` and the corresponding array in `scripts/watchdog.sh`.

---

## Per-signal enrichment

Every extracted trading call is automatically enriched before appearing in any report:

| Enrichment | What it adds |
|---|---|
| **NSE live price** | Is the entry still valid? Has the SL already been hit? |
| **Technical state** | RSI(14), SMA(20) position, 52-week percentile, trend direction |
| **OI velocity** | Which strikes are seeing large buildup or unwinding this hour |
| **Confluence** | Same stock called by 2+ independent channels → elevated alert |
| **Event flag** | Earnings / dividend / split within 5 calendar days |

---

## Intraday price alerts

`scripts/price_monitor.py` runs every 5 minutes during market hours. For every open signal it checks:

- **BUY signal** — alert if `day_high >= target` or `day_low <= stop_loss`
- **SELL signal** — alert if `day_low <= target` or `day_high >= stop_loss`

Each event fires exactly once per signal per session. Alert history is persisted in `signal_log.intraday_alerts` (JSON) so duplicate alerts are never sent across restarts.

---

## Bot commands

Send these to your bot at any time. Only messages from `OWNER_CHAT_ID` are processed.

| Command | What it does |
|---|---|
| `/run_preopen` | Run the pre-open briefing immediately |
| `/run_hourly` | Run an hourly signal scan immediately |
| `/run_eod` | Run the EOD grader immediately |
| `/run_weekly` | Run the weekly scorecard immediately |
| `/health` | Run a full healthcheck and report to Telegram |
| `/status` | Show bridge status, open signals today, DB message count |

These are handled by `fin-bot-listener` (systemd, Restart=always). If cron and the failsafe scheduler are both down, the bot listener is a completely independent path to trigger any job from your phone.

---

## Channel management

The `monitored_channels` table is the source of truth. The bridge reads it at startup.

```bash
python main.py channels          # list all with status (ON/OFF)
python main.py disable <id>      # mute without deleting history
python main.py enable  <id>      # unmute
python main.py discover          # re-scan after joining new channels
systemctl restart fin-bridge     # pick up channel list changes
```

Direct SQL:

```sql
-- Most active channels by message volume
SELECT name, COUNT(*) AS msgs
FROM messages m JOIN chats c ON m.chat_jid = c.jid
GROUP BY name ORDER BY msgs DESC;

-- Mute a channel by name
UPDATE monitored_channels SET active=0 WHERE name LIKE '%SomeChannel%';
```

---

## Manual commands

```bash
# Channel management
python main.py discover          # re-scan Telegram for channels
python main.py channels          # list monitored channels
python main.py fetch 7           # backfill 7 days of history

# Trigger reports on demand
python main.py preopen           # pre-open briefing
python main.py hourly            # hourly signal scan
python main.py eod               # EOD grader
python main.py weekly            # weekly scorecard
python main.py oi-snapshot       # manual OI snapshot

# Dry run (print to terminal, do not send to Telegram)
python main.py hourly --dry-run
python main.py eod    --dry-run

# Logs
tail -f logs/bridge.log          # live bridge output
tail -f logs/cron.log            # scheduled job output
tail -f logs/price_monitor.log   # intraday alert output
tail -f logs/watchdog.log        # watchdog recovery output
journalctl -fu fin-scheduler     # failsafe scheduler output
journalctl -fu fin-bot-listener  # bot command listener output

# Healthcheck
python scripts/healthcheck.py --report   # full status report
```

---

## Database schema

| Table | Contents |
|---|---|
| `monitored_channels` | All Telegram groups/channels with active on/off toggle |
| `messages` | Raw messages from all active channels |
| `signal_log` | Extracted signals, EOD grades, intraday alert history |
| `oi_snapshots` | Hourly OI per strike (NIFTY / BANKNIFTY / FINNIFTY) |
| `fii_dii_daily` | FII/DII provisional net flows per day |
| `bulk_deals` | Institutional bulk and block trades >= Rs. 10 cr |
| `corporate_events` | Earnings, dividends, splits |

Full schema: [`db/schema.sql`](db/schema.sql)

---

## Healthcheck probes

`scripts/healthcheck.py` runs every 15 minutes during market hours and every 2 hours outside them. It checks:

- Logs directory is writable
- Disk free >= 500 MB
- SQLite DB is reachable and writable
- `fin-bridge` systemd service is active
- Bridge wrote to DB within the last 5 minutes (liveness, not just service status)
- NSE API is reachable
- Yahoo Finance is reachable
- ForexFactory is reachable (macro event calendar)
- Telegram Bot API token is valid

Any failure triggers an immediate Telegram alert. `fin-bridge` is auto-restarted if it is found inactive.

---

## Backup and restore

```bash
# Create backup (DB + .env + session file)
./scripts/backup.sh ~/backups
# Output: fin-assistant-backup-YYYYMMDD_HHMM.tar.gz

# Optional: configure Google Drive upload
./scripts/setup-gdrive.sh       # sets up rclone; backup.sh uploads automatically

# Restore on a new machine
tar -xzf fin-assistant-backup-*.tar.gz -C ~
cd fin-assistant && ./scripts/setup.sh
# Restore .env manually; copy the .session file to the path set in TG_SESSION
systemctl start fin-bridge fin-scheduler fin-bot-listener
```

---

## Disclaimer

Personal research tool only. Not financial advice. Trade at your own risk.
