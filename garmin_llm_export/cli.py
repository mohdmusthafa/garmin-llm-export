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
from .exporter import GarminExporter, SECTION_REGISTRY
from .presets import FOCUS_PRESETS, FOCUS_PRESET_DESCRIPTIONS, expand_focus, list_presets
from .rate_limit import configure_limiter

log = logging.getLogger("garmin_llm_export")


# ---------------------------------------------------------------------------
# Help-text constants
# ---------------------------------------------------------------------------
COMMON_QUERIES_EPILOG = """
Common queries:
  %(prog)s --login                 Log in and cache tokens (~1 year)
  %(prog)s                         Last 30 days, 100 activities
  %(prog)s --days 7 --compact      Last week, LLM-friendly size
  %(prog)s --focus sleep --days 2  Last night's sleep only (< 30 API calls)
  %(prog)s --all --split           Full history for NotebookLM
  %(prog)s --update                Incremental since last export

Discovery:
  %(prog)s --list-presets          List available --focus presets
  %(prog)s --list-sections         List valid --sections values

Authentication: create .env with GARMIN_EMAIL and GARMIN_PASSWORD, then --login.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="garmin-export",
        description=(
            "Export Garmin Connect health & fitness data for LLM analysis. "
            "Use --focus or --sections to fetch only what you need."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=COMMON_QUERIES_EPILOG,
    )

    # --- Data selection -------------------------------------------------
    sel = parser.add_argument_group("Data selection")
    sel.add_argument(
        "--all", action="store_true",
        help="Export complete history (ignores --days and --activities limits).",
    )
    sel.add_argument(
        "--days", type=int, default=30,
        help="Days of daily health data (default: 30).",
    )
    sel.add_argument(
        "--activities", type=int, default=100,
        help="Max activities to export (default: 100).",
    )
    sel.add_argument(
        "--sections", type=str, default=None,
        metavar="ID[,ID...]",
        help="Comma-separated sections to include. Mutually exclusive with --focus. Default: all.",
    )
    sel.add_argument(
        "--focus", type=str, default=None,
        metavar="PRESET",
        choices=sorted(FOCUS_PRESETS.keys()),
        help=f"Section bundle preset ({', '.join(sorted(FOCUS_PRESETS.keys()))}). Mutually exclusive with --sections.",
    )
    sel.add_argument(
        "--list-presets", action="store_true",
        help="List available --focus presets and exit.",
    )
    sel.add_argument(
        "--list-sections", action="store_true",
        help="List valid --sections values and exit.",
    )

    # --- Output control --------------------------------------------------
    out_group = parser.add_argument_group("Output control")
    out_group.add_argument(
        "--output", type=str, default="export",
        help="Output directory (default: export).",
    )
    out_group.add_argument(
        "--compact", action="store_true",
        help="Smaller output: strip nulls, downsample time-series.",
    )
    out_group.add_argument(
        "--split", action="store_true",
        help="Split into <500K word files (implies --compact).",
    )
    out_group.add_argument(
        "--update", action="store_true",
        help="Export only new data since last export (implies --compact).",
    )

    # --- Caching and pacing ---------------------------------------------
    cache_group = parser.add_argument_group("Caching and pacing")
    cache_group.add_argument(
        "--no-cache", action="store_true",
        help="Disable resume cache; re-fetch everything.",
    )
    cache_group.add_argument(
        "--delay", type=float, default=0.15,
        help="Base delay between API calls in seconds (default: 0.15).",
    )

    # --- Authentication and misc ----------------------------------------
    auth_group = parser.add_argument_group("Authentication and misc")
    auth_group.add_argument(
        "--tokenstore", type=str, default=None,
        help="Token cache path (default: ~/.garminconnect).",
    )
    auth_group.add_argument(
        "--login", action="store_true",
        help="Authenticate and cache tokens, then exit.",
    )
    auth_group.add_argument(
        "-v", "--verbose", action="store_true",
        help="Debug logging.",
    )

    return parser


def _print_presets() -> None:
    print("Available --focus presets:\n")
    name_w = max(len(n) for n in FOCUS_PRESETS)
    for name, desc in list_presets():
        marker = " (default)" if name == "all" else ""
        print(f"  {name:<{name_w}}  {desc}{marker}")
    print()
    print("Examples:")
    print("  uv run garmin-export --focus sleep --days 2")
    print("  uv run garmin-export --focus training --days 14")
    print()


def _print_sections() -> None:
    print("Valid --sections values:\n")
    for sid in (s.id for s in SECTION_REGISTRY):
        print(f"  {sid}")
    print()
    print("Combine with commas, e.g.: --sections daily_health,training")
    print()


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

    # Discovery flags short-circuit before any other validation
    if args.list_presets:
        _print_presets()
        return 0
    if args.list_sections:
        _print_sections()
        return 0

    # --focus and --sections are mutually exclusive
    if args.focus and args.sections:
        parser.error("--focus and --sections are mutually exclusive")

    # --focus validation against FOCUS_PRESETS (argparse already enforces choices)
    # --sections validation
    if args.sections:
        requested = [s.strip() for s in args.sections.split(",") if s.strip()]
        valid = {s.id for s in SECTION_REGISTRY}
        invalid = [s for s in requested if s not in valid]
        if invalid:
            parser.error(
                f"unknown --sections value(s): {', '.join(sorted(invalid))}. "
                f"Use --list-sections to see valid values."
            )
        sections_filter: set[str] | None = set(requested)
    elif args.focus:
        sections_filter = set(expand_focus(args.focus))
    else:
        sections_filter = None

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
        sections=sections_filter,
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
