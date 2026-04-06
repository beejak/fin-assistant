-- Financial Assistant — SQLite schema
-- Apply: sqlite3 store/messages.db < db/schema.sql

-- ── Discovered channels (auto-populated by `python main.py discover`) ─────

CREATE TABLE IF NOT EXISTS monitored_channels (
    id             INTEGER PRIMARY KEY,   -- Telegram chat ID
    name           TEXT NOT NULL,
    type           TEXT,                  -- CHANNEL | SUPERGROUP | GROUP
    members_count  INTEGER DEFAULT 0,
    discovered_at  TEXT NOT NULL,
    last_seen      TEXT,
    active         INTEGER DEFAULT 1      -- set 0 to mute without deleting
);

-- ── Messages written by the bridge ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chats (
    jid               TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    last_message_time TEXT,
    channel           TEXT DEFAULT 'telegram',
    is_group          INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages (
    id             TEXT PRIMARY KEY,
    chat_jid       TEXT NOT NULL REFERENCES chats(jid),
    sender         TEXT,
    sender_name    TEXT,
    content        TEXT NOT NULL,
    timestamp      TEXT NOT NULL,
    is_from_me     INTEGER DEFAULT 0,
    is_bot_message INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_jid, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_ts      ON messages(timestamp);

-- ── Signal tracking ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS signal_log (
    id          TEXT PRIMARY KEY,
    date        TEXT NOT NULL,
    channel     TEXT NOT NULL,
    instrument  TEXT NOT NULL,
    direction   TEXT,
    entry       REAL,
    sl          REAL,
    targets     TEXT,          -- JSON array
    raw_text    TEXT,
    sent_at     TEXT NOT NULL,
    result      TEXT DEFAULT 'OPEN',   -- OPEN | TGT1_HIT | TGT2_HIT | TGT3_HIT | SL_HIT
    result_note TEXT,
    graded_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_signal_date  ON signal_log(date);
CREATE INDEX IF NOT EXISTS idx_signal_instr ON signal_log(instrument, date);

-- ── OI snapshots (hourly, for velocity tracking) ───────────────────────────

CREATE TABLE IF NOT EXISTS oi_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    expiry        TEXT NOT NULL,
    strike        REAL NOT NULL,
    opt_type      TEXT NOT NULL,
    oi            REAL,
    chg_in_oi     REAL,
    ltp           REAL,
    snapshot_time TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oi_sym_time ON oi_snapshots(symbol, snapshot_time);

-- ── FII / DII daily flows ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fii_dii_daily (
    date       TEXT PRIMARY KEY,
    fii_buy    REAL, fii_sell REAL, fii_net REAL,
    dii_buy    REAL, dii_sell REAL, dii_net REAL,
    fetched_at TEXT
);

-- ── Bulk & block deals ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bulk_deals (
    id          TEXT PRIMARY KEY,
    date        TEXT,
    symbol      TEXT,
    client_name TEXT,
    trade_type  TEXT,
    quantity    REAL,
    price       REAL,
    fetched_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_bulk_date ON bulk_deals(date);

-- ── Corporate events ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS corporate_events (
    symbol   TEXT NOT NULL,
    ex_date  TEXT NOT NULL,
    purpose  TEXT NOT NULL,
    PRIMARY KEY (symbol, ex_date, purpose)
);

CREATE INDEX IF NOT EXISTS idx_corp_date ON corporate_events(ex_date);

-- ── NSE symbol list (refreshed daily by scripts/refresh_nse_symbols.py) ──────

CREATE TABLE IF NOT EXISTS nse_symbols (
    symbol     TEXT PRIMARY KEY,
    name       TEXT,
    isin       TEXT,
    series     TEXT,
    type       TEXT DEFAULT 'equity',   -- equity | index
    updated_at TEXT
);

-- ── Learning loop ──────────────────────────────────────────────────────────

-- Rolling 30-day hit rate per channel (updated each EOD)
CREATE TABLE IF NOT EXISTS channel_scores (
    channel      TEXT PRIMARY KEY,
    total        INTEGER DEFAULT 0,
    hits         INTEGER DEFAULT 0,
    sl_hits      INTEGER DEFAULT 0,
    hit_rate     REAL,
    confidence   TEXT DEFAULT 'UNKNOWN',  -- HIGH | MED | LOW | UNKNOWN
    suggest_mute INTEGER DEFAULT 0,
    updated_at   TEXT
);

-- Per-instrument, per-direction success rate (updated each EOD)
CREATE TABLE IF NOT EXISTS instrument_stats (
    instrument   TEXT NOT NULL,
    direction    TEXT NOT NULL,
    total        INTEGER DEFAULT 0,
    hits         INTEGER DEFAULT 0,
    sl_hits      INTEGER DEFAULT 0,
    hit_rate     REAL,
    updated_at   TEXT,
    PRIMARY KEY (instrument, direction)
);

-- Daily market regime snapshot (VIX quartile, FII flow, index trend)
CREATE TABLE IF NOT EXISTS market_regime (
    date         TEXT PRIMARY KEY,
    vix          REAL,
    vix_label    TEXT,                    -- HIGH | NORMAL
    fii_net_5d   REAL,
    flow_label   TEXT,                    -- FII_BUYING | FII_SELLING | NEUTRAL
    nifty_close  REAL,
    nifty_5d_pct REAL,
    trend_label  TEXT,                    -- BULLISH | BEARISH | SIDEWAYS
    regime_text  TEXT,
    recorded_at  TEXT
);
