# Financial Assistant

A self-hosted market signal aggregator for Indian stocks. Connects to your own Telegram account, discovers every group and channel you're subscribed to, extracts trading signals from their messages, cross-validates each call against live NSE data, and delivers structured briefings to your bot — hourly during market hours with a full EOD accuracy report.

No hardcoded channels. No subscriptions. No external APIs beyond NSE. Works with whatever you're already subscribed to.

---

## How it works

```
Your Telegram account
        │
        │  Pyrogram (MTProto)
        ▼
bridge/tg_bridge.py ──── listens to all your groups/channels
        │
        │  writes
        ▼
store/messages.db ─────── SQLite: messages + signals + enrichments
        │
        │  reads
        ▼
main.py [mode] ─────────── analysis + reporting
        │
        ├── Pre-open  8:45 AM   GIFT Nifty gap, VIX, FII/DII, overnight signals
        ├── Hourly    9:45–3:15 New signals, NSE live check, TA, OI velocity, confluence
        ├── EOD       3:45 PM   Grade every call: TGT/SL/Open, channel scorecard
        └── Weekly    Mon 8 AM  Hit rate per channel, mute recommendations
```

### Per-signal enrichment
Every extracted call gets:
- **NSE live price** — entry still valid? SL already hit?
- **TA state** — RSI(14), SMA(20) position, 52-week percentile, trend
- **OI velocity** — which strikes are seeing large buildup/unwinding this hour
- **Confluence** — same stock called by 2+ channels independently → elevated alert
- **Event flag** — earnings/dividend/split within 5 days

### Daily data stored
- FII/DII provisional flows
- Bulk & block deals ≥ ₹10cr
- OI snapshots (hourly during market hours)
- Corporate actions calendar

---

## Setup

### Requirements
- Ubuntu / Debian / WSL2
- Python 3.11+
- A Telegram account (Pyrogram uses MTProto — your personal account, not a bot)
- A Telegram bot to receive reports (create via [@BotFather](https://t.me/BotFather))

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
| `OWNER_CHAT_ID` | Send `/start` to your bot, then call `getUpdates` via the Bot API |

### 3. Discover your channels (one-time, re-run when you join new ones)
```bash
python main.py discover --dry    # preview what will be found
python main.py discover          # save to DB
python main.py channels          # verify the list
```

This scans every group and channel in your Telegram account. You can selectively mute any that aren't relevant:
```bash
python main.py disable -1001234567890   # stop monitoring a channel
python main.py enable  -1001234567890   # re-enable it
```

### 4. Start monitoring
```bash
python main.py fetch 7           # backfill last 7 days of history
systemctl start fin-bridge       # start live bridge (runs 24/7)
```

### 5. Test a report
```bash
python main.py hourly --dry-run  # prints to terminal instead of Telegram
python main.py eod    --dry-run
```

---

## Channel management

The `monitored_channels` table is the source of truth. The bridge reads it at startup.

```bash
python main.py channels          # list all with status (ON/OFF)
python main.py disable <id>      # mute without deleting history
python main.py enable  <id>      # unmute
python main.py discover          # re-scan after joining new channels
systemctl restart fin-bridge     # pick up changes
```

You can also query/edit directly:
```sql
-- Show most active channels
SELECT name, COUNT(*) as msgs
FROM messages m JOIN chats c ON m.chat_jid = c.jid
GROUP BY name ORDER BY msgs DESC;

-- Disable a channel by name
UPDATE monitored_channels SET active=0 WHERE name LIKE '%SomeChannel%';
```

---

## Schedule (Mon–Fri)

| Time IST | What runs |
|---|---|
| 8:45 AM | Pre-open: GIFT Nifty gap, VIX, FII/DII, overnight signals |
| 9:45 AM | First hourly scan after market open |
| 10:45 – 3:15 PM | Hourly scans (new signals only, deduped) |
| 3:45 PM | EOD grader |
| Mon 8:00 AM | Weekly channel scorecard |

---

## Database tables

| Table | Contents |
|---|---|
| `monitored_channels` | All your Telegram groups/channels, with on/off toggle |
| `messages` | Raw messages from all active channels |
| `signal_log` | Extracted signals + EOD grades |
| `oi_snapshots` | Hourly OI per strike (NIFTY/BANKNIFTY/FINNIFTY) |
| `fii_dii_daily` | FII/DII net flows by day |
| `bulk_deals` | Institutional bulk/block trades ≥ ₹10cr |
| `corporate_events` | Earnings, dividends, splits |

Full schema: [`db/schema.sql`](db/schema.sql)

---

## Manual commands

```bash
python main.py discover          # re-scan Telegram for channels
python main.py channels          # list monitored channels
python main.py fetch 7           # backfill 7 days

python main.py preopen           # send pre-open briefing now
python main.py hourly            # send hourly scan now
python main.py eod               # send EOD report now
python main.py weekly            # send weekly scorecard now
python main.py oi-snapshot       # manual OI snapshot

python main.py hourly --dry-run  # print instead of sending

tail -f logs/bridge.log          # live bridge output
tail -f logs/cron.log            # scheduled report output
```

---

## Backup and restore

```bash
./scripts/backup.sh ~/backups
# → fin-assistant-backup-YYYYMMDD_HHMM.tar.gz

# Restore on new machine:
tar -xzf fin-assistant-backup-*.tar.gz -C ~
cd fin-assistant && ./scripts/setup.sh
# Restore .env manually, copy tg_session file
systemctl start fin-bridge
```

---

## Disclaimer

For personal research only. Not financial advice.
