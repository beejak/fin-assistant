#!/usr/bin/env python3
"""
Financial Assistant — entry point.

Usage:
  python main.py discover            Scan your Telegram account and store all
                                     groups/channels in DB for monitoring
  python main.py discover --dry      Print discovered channels without saving

  python main.py channels            List all channels currently in DB
  python main.py disable <id>        Stop monitoring a specific channel
  python main.py enable  <id>        Re-enable a disabled channel

  python main.py fetch [days] [lim]  Backfill N days of history (default 3)

  python main.py preopen             8:45 AM pre-open briefing
  python main.py hourly              Hourly signal scan (run via cron)
  python main.py eod                 EOD grader + FII/DII + deals
  python main.py weekly              Monday scorecard

  python main.py oi-snapshot         Manual OI snapshot

  Add --dry-run to any report command to print instead of sending to Telegram.
"""
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)


def usage():
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    args    = sys.argv[1:]
    dry_run = "--dry-run" in args or "--dry" in args
    args    = [a for a in args if a not in ("--dry-run", "--dry")]
    mode    = args[0] if args else None

    if mode == "discover":
        from bridge.discover import run
        run(dry=dry_run)

    elif mode == "channels":
        from bridge.discover import list_channels
        channels = list_channels()
        if not channels:
            print("No channels in DB. Run: python main.py discover")
        else:
            print(f"{'Status':<8} {'Type':<12} {'ID':<16} {'Members':<8} Name")
            print("-" * 72)
            for ch in channels:
                status = "ON " if ch["active"] else "OFF"
                print(f"{status:<8} {ch['type']:<12} {ch['id']:<16} "
                      f"{ch['members']:<8} {ch['name']}")
            active = sum(1 for c in channels if c["active"])
            print(f"\n{active} active / {len(channels)} total")

    elif mode == "disable" and len(args) > 1:
        from bridge.discover import set_active
        set_active(int(args[1]), False)
        print(f"Channel {args[1]} disabled")

    elif mode == "enable" and len(args) > 1:
        from bridge.discover import set_active
        set_active(int(args[1]), True)
        print(f"Channel {args[1]} enabled")

    elif mode == "fetch":
        days  = int(args[1]) if len(args) > 1 else 3
        limit = int(args[2]) if len(args) > 2 else 500
        import subprocess
        subprocess.run(
            [sys.executable, "bridge/fetch.py", str(days), str(limit)],
            check=True
        )

    elif mode == "preopen":
        from reports.preopen import run; run(dry_run=dry_run)

    elif mode == "hourly":
        from reports.hourly import run; run(dry_run=dry_run)

    elif mode == "eod":
        from reports.eod import run; run(dry_run=dry_run)

    elif mode == "weekly":
        from reports.weekly import run; run(dry_run=dry_run)

    elif mode == "oi-snapshot":
        from enrichers.oi_velocity import snapshot; snapshot()

    else:
        usage()
