#!/usr/bin/env python3
"""
join_scout_channels.py — join curated channels from CHANNEL_SCOUT.md on Telegram
and add them to the monitored_channels DB.

Joins Priority 1 and Priority 2 channels by default.
Pass --priority3 to also join the cautionary Priority 3 channels.
Pass --dry-run to preview without joining anything.

Usage:
  python3 scripts/join_scout_channels.py
  python3 scripts/join_scout_channels.py --priority3
  python3 scripts/join_scout_channels.py --dry-run
"""

import sys
import asyncio
import argparse
import logging
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from pyrogram import Client
from pyrogram.errors import (
    UserAlreadyParticipant, InviteHashExpired, ChannelPrivate,
    FloodWait, UsernameNotOccupied, UsernameInvalid,
)
from config import TG_API_ID, TG_API_HASH, TG_SESSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s [join] %(levelname)s %(message)s")
log = logging.getLogger("join_scout")

# ---------------------------------------------------------------------------
# Curated channel list from CHANNEL_SCOUT.md
# Red-flagged channels are NOT included here — see CHANNEL_SCOUT.md
# ---------------------------------------------------------------------------

PRIORITY_1 = [
    ("fiidata",              "FnO OI data, FII/DII flows, trading psychology"),
    ("Stockizenofficial",    "Equity + options + institutional flows (SEBI INH000017675)"),
    ("RakeshAlgo",           "TA, Nifty, midcap, algo signals (SEBI INH100008984)"),
    ("indextradingnitin",    "Nifty index options, PCR analysis (SMC-affiliated)"),
    ("abhayvarn",            "Nifty, BankNifty options, intraday (SEBI INH300008465)"),
    ("MarketPlusTrading",    "Nifty, BankNifty, FinNifty, FII/DII, OI analysis (SEBI RA)"),
    ("mystockmarketfunda",   "TA, price action, Nifty/BankNifty, commodities"),
    ("PivotFunda",           "Nifty OI, PCR every 5 min, option chain"),
]

PRIORITY_2 = [
    ("STOCKGAINERSS",           "Equity intraday, options, BTST, swing (SEBI INH100007879)"),
    ("equity99",                "Intraday, equity, mutual funds (SEBI registered)"),
    ("chaseAlpha",              "Options, Nifty, BankNifty, equity (SEBI IA)"),
    ("joinstocktime",           "Options, intraday, equity, education (SEBI registered)"),
    ("TradeWithKarol_Prateek",  "Equity, Nifty/BankNifty options, daily setups (SEBI)"),
    ("meharshbhagat01",         "Intraday, swing, positional calls (SEBI registered)"),
    ("Flyingcalls_arjun",       "Stocks, indices, risk management, swing (SEBI RA)"),
]

PRIORITY_3 = [
    ("TradelikeFiis",            "Educational trades, personal analysis — no SEBI"),
    ("Banknifty_specials",       "Nifty + BankNifty options, intraday — no SEBI"),
    ("stock_burner_03",          "Nifty, BankNifty, TA, free calls — no SEBI"),
    ("Ghanshyamtechanalysis0",   "Options strategies, educational TA — no SEBI"),
    ("deltatrading1",            "Options, BankNifty, F&O — SEBI claimed, unverified"),
]


async def join_channels(handles: list[tuple], dry_run: bool) -> dict:
    results = {"joined": [], "already": [], "failed": []}

    if dry_run:
        for handle, desc in handles:
            print(f"  [DRY] would join @{handle}  — {desc}")
        return results

    async with Client(TG_SESSION, api_id=TG_API_ID, api_hash=TG_API_HASH) as app:
        for handle, desc in handles:
            try:
                await app.join_chat(handle)
                log.info("Joined @%s", handle)
                results["joined"].append(handle)
            except UserAlreadyParticipant:
                log.info("Already in @%s", handle)
                results["already"].append(handle)
            except FloodWait as e:
                log.warning("FloodWait %ds before @%s — waiting...", e.value, handle)
                await asyncio.sleep(e.value + 2)
                try:
                    await app.join_chat(handle)
                    results["joined"].append(handle)
                except Exception as e2:
                    log.error("Still failed @%s after wait: %s", handle, e2)
                    results["failed"].append((handle, str(e2)))
            except (UsernameNotOccupied, UsernameInvalid):
                log.warning("@%s — username not found or invalid", handle)
                results["failed"].append((handle, "username not found"))
            except ChannelPrivate:
                log.warning("@%s — channel is private, can't join", handle)
                results["failed"].append((handle, "private channel"))
            except InviteHashExpired:
                log.warning("@%s — invite expired", handle)
                results["failed"].append((handle, "invite expired"))
            except Exception as e:
                log.error("@%s — unexpected error: %s", handle, e)
                results["failed"].append((handle, str(e)))

            time.sleep(1.5)   # be polite to Telegram

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--priority3", action="store_true",
                        help="Also join cautionary Priority 3 channels")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without joining")
    args = parser.parse_args()

    to_join = PRIORITY_1 + PRIORITY_2
    if args.priority3:
        to_join += PRIORITY_3

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Joining {len(to_join)} channels...")
    print(f"  Priority 1: {len(PRIORITY_1)}")
    print(f"  Priority 2: {len(PRIORITY_2)}")
    if args.priority3:
        print(f"  Priority 3: {len(PRIORITY_3)}")
    print()

    results = asyncio.run(join_channels(to_join, dry_run=args.dry_run))

    if not args.dry_run:
        print(f"\nResults:")
        print(f"  Newly joined:      {len(results['joined'])}")
        print(f"  Already member:    {len(results['already'])}")
        print(f"  Failed:            {len(results['failed'])}")

        if results["failed"]:
            print("\nFailed channels:")
            for handle, reason in results["failed"]:
                print(f"  @{handle}: {reason}")

        total_new = len(results["joined"])
        if total_new > 0:
            print(f"\nRunning discover to add {total_new} new channel(s) to DB...")
            from bridge.discover import run as discover
            channels = discover()
            print(f"DB now has {len(channels)} monitored channels.")
        else:
            print("\nNo new channels joined — DB unchanged.")


if __name__ == "__main__":
    main()
