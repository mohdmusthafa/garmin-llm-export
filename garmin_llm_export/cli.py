"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .auth import load_env, login
from .cache import ExportCache
from .config import DEFAULT_TOKENSTORE, ExportSettings, settings
from .exporter import GarminExporter
from .rate_limit import configure_limiter

log = logging.getLogger("garmin_llm_export")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="garmin-export",
        description="Export Garmin Connect health & fitness data for LLM analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  garmin-export --login                   Log in and cache tokens
  garmin-export                           Last 30 days, 100 activities
  garmin-export --days 7 --compact        Last week, LLM-friendly size
  garmin-export --all --split             Full history for NotebookLM
  garmin-export --update                  Incremental since last export

Authentication:
  Create .env with GARMIN_EMAIL and GARMIN_PASSWORD, then run --login once.
  Tokens are cached at ~/.garminconnect/ (~1 year).
""",
    )
    parser.add_argument("--all", action="store_true", help="Export complete history")
    parser.add_argument("--days", type=int, default=30, help="Days of daily health (default: 30)")
    parser.add_argument("--activities", type=int, default=100, help="Max activities (default: 100)")
    parser.add_argument("--output", type=str, default="export", help="Output directory")
    parser.add_argument("--tokenstore", type=str, default=None, help="Token cache path")
    parser.add_argument("--delay", type=float, default=0.15, help="Base delay between API calls")
    parser.add_argument("--no-cache", action="store_true", help="Disable resume cache")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Smaller output: strip nulls, downsample time-series",
    )
    parser.add_argument(
        "--split",
        action="store_true",
        help="Split into <500K word files for NotebookLM (implies --compact)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Export only new data since last export (implies --compact)",
    )
    parser.add_argument("--login", action="store_true", help="Authenticate only, then exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("garminconnect").setLevel(logging.WARNING)

    configure_limiter(args.delay)

    compact = args.compact or args.split or args.update
    settings.compact = compact
    settings.split = args.split
    settings.update = args.update

    print("\n  Garmin Connect Data Export")
    print(f"  {'-' * 26}\n")

    load_env()
    tokenstore = Path(args.tokenstore or os.getenv("GARMINTOKENS", DEFAULT_TOKENSTORE)).expanduser()

    try:
        api = login(tokenstore)
    except GarminConnectTooManyRequestsError:
        log.error("Rate limited (429). Wait 10-15 minutes and retry.")
        return 1
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
        log.error("Authentication failed: %s", exc)
        return 1

    if args.login:
        log.info("Login successful -- tokens cached.")
        return 0

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    cache = ExportCache(out, enabled=not args.no_cache)
    log.info("Cache: %s", "enabled" if cache.enabled else "disabled (--no-cache)")

    exporter = GarminExporter(
        api,
        out,
        args.days,
        args.activities,
        fetch_all=args.all,
        cache=cache,
        update_mode=settings.update,
    )
    try:
        exporter.run()
    except KeyboardInterrupt:
        print()
        log.info("Interrupted -- cached data saved, re-run to continue")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
