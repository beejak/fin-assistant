#!/usr/bin/env python3
"""
channel_scout.py — daily scout for publicly recommended Indian trading Telegram channels.

Scrapes public forums and sources (Reddit, Quora threads, TradingQnA, etc.)
for Telegram channel mentions in the Indian trading community. Deduplicates,
scores by mention frequency, and appends new findings to CHANNEL_SCOUT.md.

Run via cron: once daily, outside market hours.
Results are human-reviewed before any channel is added to monitored_channels.

Usage:
  python3 scripts/channel_scout.py            # run scout, update CHANNEL_SCOUT.md
  python3 scripts/channel_scout.py --dry-run  # print findings, don't write file
"""

import sys
import re
import time
import logging
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import db, IST, DB_PATH, BOT_TOKEN, OWNER_CHAT_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scout] %(levelname)s %(message)s",
)
log = logging.getLogger("channel_scout")

REPORT_FILE = ROOT / "CHANNEL_SCOUT.md"

# Known noise / spam handles to exclude regardless of mention count
BLOCKLIST = {
    "telegram", "t.me", "whatsapp", "zerodha", "groww", "upstox",
    "sensibull", "opstra", "stockedge", "moneycontrol", "tradingview",
}

# Regex to find Telegram handles or t.me links in text
TG_HANDLE_RE = re.compile(
    r"(?:t\.me/|@)([A-Za-z][A-Za-z0-9_]{3,})", re.IGNORECASE
)
TG_CHANNEL_WORDS = re.compile(
    r"\b(options?|nifty|banknifty|f&o|fno|equity|trading|signals?|calls?|"
    r"scalp|swing|momentum|technical|analysis|stocks?|sensex|indices|oi|"
    r"futures?|expiry|bull|bear|breakout)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def _fetch(url: str, timeout: int = 15) -> str:
    """HTTP GET, returns text or '' on failure."""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; fin-assistant-scout/1.0)"
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning("Fetch failed %s: %s", url, e)
        return ""


def _reddit_search(query: str, subreddit: str = "") -> list[str]:
    """Search Reddit via old JSON API. Returns list of text snippets."""
    base = f"https://www.reddit.com/r/{subreddit}/search.json" if subreddit else \
           "https://www.reddit.com/search.json"
    url = f"{base}?q={query.replace(' ', '+')}&sort=relevance&limit=25&t=year"
    data = _fetch(url)
    snippets = []
    try:
        import json
        j = json.loads(data)
        for post in j.get("data", {}).get("children", []):
            d = post.get("data", {})
            snippets.append(d.get("title", "") + " " + d.get("selftext", ""))
    except Exception:
        pass
    return snippets


def _tradingqna_search(query: str) -> list[str]:
    """Scrape TradingQnA (Zerodha community) search results."""
    url = f"https://tradingqna.com/search?q={query.replace(' ', '+')}"
    html = _fetch(url)
    # Extract visible text roughly
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return [text[:8000]] if text else []


def gather_mentions() -> dict[str, dict]:
    """
    Scrape public sources for Telegram channel mentions in Indian trading context.
    Returns dict: handle -> {mentions, contexts, sources}
    """
    mentions: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "contexts": [], "sources": set()}
    )

    sources = [
        # Reddit
        ("reddit:IndianStreetBets",
         lambda: _reddit_search("telegram channel options nifty", "IndianStreetBets")),
        ("reddit:IndianStreetBets-equity",
         lambda: _reddit_search("telegram channel stock signals", "IndianStreetBets")),
        ("reddit:IndiaInvestments",
         lambda: _reddit_search("telegram trading channel", "IndiaInvestments")),
        ("reddit:IndianStockMarket",
         lambda: _reddit_search("best telegram channel options signals", "IndianStockMarket")),
        ("reddit:global-search",
         lambda: _reddit_search("best telegram channel nifty options India signals")),
        # TradingQnA
        ("tradingqna:telegram-options",
         lambda: _tradingqna_search("telegram channel options")),
        ("tradingqna:telegram-signals",
         lambda: _tradingqna_search("telegram trading signals India")),
    ]

    for source_name, fetch_fn in sources:
        log.info("Scraping %s ...", source_name)
        try:
            snippets = fetch_fn()
            for snippet in snippets:
                for match in TG_HANDLE_RE.finditer(snippet):
                    handle = match.group(1).lower()
                    if handle in BLOCKLIST or len(handle) < 4:
                        continue
                    # Require trading-related words nearby (±200 chars)
                    start = max(0, match.start() - 200)
                    end   = min(len(snippet), match.end() + 200)
                    context = snippet[start:end].strip()
                    if not TG_CHANNEL_WORDS.search(context):
                        continue
                    mentions[handle]["count"] += 1
                    mentions[handle]["sources"].add(source_name.split(":")[0])
                    if len(mentions[handle]["contexts"]) < 2:
                        clean = re.sub(r"\s+", " ", context)[:200]
                        mentions[handle]["contexts"].append(clean)
        except Exception as e:
            log.error("Source %s failed: %s", source_name, e)
        time.sleep(1.5)   # be polite

    return mentions


def _load_known() -> set[str]:
    """Load handles already in CHANNEL_SCOUT.md to avoid re-reporting."""
    if not REPORT_FILE.exists():
        return set()
    text = REPORT_FILE.read_text()
    return {m.group(1).lower() for m in re.finditer(r"@([A-Za-z0-9_]+)", text)}


def _load_monitored() -> set[str]:
    """Load channel names already in monitored_channels DB."""
    try:
        with db() as conn:
            rows = conn.execute("SELECT name FROM monitored_channels").fetchall()
        return {r[0].lower() for r in rows}
    except Exception:
        return set()


def send_alert(text: str) -> None:
    if not BOT_TOKEN or not OWNER_CHAT_ID:
        return
    try:
        import urllib.request, json
        payload = json.dumps({
            "chat_id": OWNER_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.error("Alert failed: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(dry_run: bool = False) -> int:
    log.info("Channel scout starting")
    now_ist = datetime.now(IST)
    date_str = now_ist.strftime("%Y-%m-%d")

    mentions = gather_mentions()
    known     = _load_known()
    monitored = _load_monitored()

    # Filter: ≥2 mentions, not already known or monitored
    new_finds = {
        handle: data
        for handle, data in mentions.items()
        if data["count"] >= 2
        and handle not in known
        and handle not in monitored
    }

    log.info(
        "Scout complete: %d total handles found, %d new (≥2 mentions)",
        len(mentions), len(new_finds)
    )

    if not new_finds:
        log.info("No new channels to report")
        return 0

    # Sort by mention count descending
    ranked = sorted(new_finds.items(), key=lambda x: x[1]["count"], reverse=True)

    # Build report section
    lines = [
        f"\n---\n",
        f"## Scouted {date_str}\n",
        f"Sources checked: Reddit (IndianStreetBets, IndiaInvestments, IndianStockMarket), TradingQnA  ",
        f"New channels found: {len(ranked)}  \n",
        "| Handle | Mentions | Sources | Sample context |",
        "|---|---|---|---|",
    ]
    for handle, data in ranked:
        sources = ", ".join(sorted(data["sources"]))
        ctx = data["contexts"][0].replace("|", "/") if data["contexts"] else ""
        ctx = ctx[:120]
        lines.append(f"| @{handle} | {data['count']} | {sources} | {ctx} |")

    lines.append(
        "\n> Status: PENDING REVIEW — add promising channels with "
        "`python main.py discover` after subscribing on Telegram.\n"
    )

    report_block = "\n".join(lines)

    if dry_run:
        print(report_block)
        return len(ranked)

    # Append to CHANNEL_SCOUT.md
    if not REPORT_FILE.exists():
        REPORT_FILE.write_text(
            "# Channel Scout — Daily Findings\n\n"
            "Automatically updated. Review before subscribing to any channel.\n"
            "Channels are found by scraping public forums for Telegram mentions "
            "in Indian trading discussions.\n"
        )

    with REPORT_FILE.open("a") as f:
        f.write(report_block)

    log.info("CHANNEL_SCOUT.md updated with %d new channels", len(ranked))

    # Telegram alert
    alert_lines = [f"[SCOUT] <b>{len(ranked)} new Telegram channels found</b> ({date_str})"]
    for handle, data in ranked[:5]:
        alert_lines.append(f"  @{handle}  ({data['count']} mentions, {', '.join(data['sources'])})")
    if len(ranked) > 5:
        alert_lines.append(f"  ... and {len(ranked)-5} more — see CHANNEL_SCOUT.md")
    send_alert("\n".join(alert_lines))

    return len(ranked)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    found = run(dry_run=args.dry_run)
    sys.exit(0 if found >= 0 else 1)
