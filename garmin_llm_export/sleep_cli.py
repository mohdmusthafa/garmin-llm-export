"""The ``garmin-sleep`` subcommand (GLE-8).

Thin wrapper around the main CLI that defaults to ``--last-sleep`` and
adds a ``--days N`` knob (1 by default = "last night", >1 = "last N
nights" with one summary per night in the output file).

Most of the heavy lifting is done by :mod:`garmin_llm_export.last_sleep`,
which writes a single file per invocation. For multi-night requests, the
wrapper runs the writer once per requested night and concatenates the
results into a single output file.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .auth import load_env, login
from .cache import ExportCache
from .config import DEFAULT_TOKENSTORE
from .exporter import GarminExporter
from .last_sleep import (
    _format_duration,
    _format_score,
    write_last_sleep_file,
)
from .rate_limit import configure_limiter
from .summaries import build_sleep_summary


def _positive_int(value: str) -> int:
    """argparse type for positive integers (>= 1)."""
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}")
    if ivalue < 1:
        raise argparse.ArgumentTypeError(
            f"must be >= 1, got {ivalue}"
        )
    return ivalue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="garmin-sleep",
        description=(
            "Export the most recent night's sleep as a small, "
            "LLM-readable plain-prose file."
        ),
    )
    parser.add_argument(
        "--days", type=_positive_int, default=1,
        help=(
            "Number of nights to include (default: 1 = last night). "
            "When >1, each night is summarised and a trend block is added."
        ),
    )
    parser.add_argument(
        "--output", type=str, default="export",
        help="Output directory (default: export).",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Disable resume cache; re-fetch everything.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Debug logging.",
    )
    return parser


def _build_summaries(
    cache: ExportCache,
    *,
    nights: int,
    tz: str | None = None,
) -> list[tuple[date, dict, dict]]:
    """Build sleep summaries for the most recent `nights` days.

    Returns a list of (date, summary_dict, raw_dto) in reverse chronological
    order. Days without data are skipped.
    """
    results: list[tuple[date, dict, dict]] = []
    today = date.today()
    for offset in range(nights):
        d = today - timedelta(days=offset)
        payload = cache.get_day(d.isoformat())
        if not payload:
            continue
        sleep = payload.get("sleep") if isinstance(payload, dict) else None
        if not sleep or not isinstance(sleep, dict):
            continue
        summary = build_sleep_summary(sleep, tz=tz)
        if summary is None:
            continue
        results.append((d, summary, sleep.get("dailySleepDTO") or {}))
    return results


def _render_multi_night(
    items: list[tuple[date, dict, dict]],
) -> str:
    """Render multiple nightly summaries into a single multi-section file."""
    if not items:
        return ""
    body: list[str] = []
    body.append(f"# Sleep Summary -- {len(items)} night(s)\n")
    body.append("Per-night summaries")
    body.append("")
    for d, summary, raw_dto in items:
        body.append(f"## {summary.get('date') or d.isoformat()}")
        score = summary.get("score", {}) or {}
        stages = summary.get("stages", {}) or {}
        vitals = summary.get("vitals", {}) or {}
        resp = vitals.get("respiration", {}) or {}
        body.append(
            f"  - Bedtime: {(summary.get('bedtime_local') or '?').replace('T', ' ')}"
        )
        body.append(
            f"  - Wake:    {(summary.get('wake_local') or '?').replace('T', ' ')}"
        )
        body.append(
            f"  - Asleep:  {_format_duration(summary.get('asleep_seconds'))}"
        )
        eff = summary.get("sleep_efficiency")
        body.append(
            f"  - Efficiency: {eff}%" if eff is not None else "  - Efficiency: ?"
        )
        body.append(
            f"  - Score: {_format_score(score.get('overall'), score.get('qualifier'))}"
        )
        stage_str = ", ".join(
            f"{stage} {stages[f'{stage}_pct']}%"
            for stage in ("deep", "light", "rem", "awake")
            if f"{stage}_pct" in stages
        )
        body.append(f"  - Stages: {stage_str or '?'}")
        body.append(f"  - Verdict: {summary.get('verdict') or 'No verdict available.'}")
        body.append("")
    # Trend block: average efficiency, average score, average deep pct.
    eff_vals = [
        s["sleep_efficiency"] for _, s, _ in items
        if s.get("sleep_efficiency") is not None
    ]
    score_vals = [
        s["score"].get("overall")
        for _, s, _ in items
        if isinstance(s.get("score"), dict) and s["score"].get("overall") is not None
    ]
    deep_vals = [
        s["stages"].get("deep_pct")
        for _, s, _ in items
        if isinstance(s.get("stages"), dict)
        and s["stages"].get("deep_pct") is not None
    ]
    body.append(f"{len(items)}-night trend")
    if eff_vals:
        body.append(f"  - Avg efficiency: {round(sum(eff_vals) / len(eff_vals), 1)}%")
    if score_vals:
        body.append(
            f"  - Avg sleep score: {round(sum(score_vals) / len(score_vals), 1)}"
        )
    if deep_vals:
        body.append(
            f"  - Avg deep sleep: {round(sum(deep_vals) / len(deep_vals), 1)}%"
        )
    body.append("")
    return "\n".join(body)


def _run_for_nights(
    api,
    out_dir: Path,
    *,
    nights: int,
    cache: ExportCache,
) -> list[Path]:
    """Run the normal exporter for `nights` days and write the summary file(s).

    Returns a list of paths that were written. For ``nights == 1`` the
    result is a single :func:`write_last_sleep_file`-style file. For
    ``nights > 1`` it is a single multi-section file (per-night
    summaries + trend block).
    """
    # Fetch the daily_health for the requested window.
    exporter = GarminExporter(
        api,
        out_dir,
        days=nights,
        max_activities=0,  # no activities for the sleep-only command
        fetch_all=False,
        cache=cache,
        update_mode=False,
        sections={"daily_health"},
        skip_index=True,  # garmin-sleep writes its own small output; skip index
    )
    exporter.run()

    items = _build_summaries(cache, nights=nights)
    if not items:
        return []

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if nights == 1:
        # Single-night: use the canonical last-sleep file writer so the
        # output is byte-identical to `garmin-export --last-sleep`.
        path = write_last_sleep_file(cache, out_dir, now=datetime.now())
        return [path] if path is not None else []

    # Multi-night: write a single multi-section file.
    path = out_dir / f"garmin_sleep_{stamp}_{len(items)}nights.txt"
    body = _render_multi_night(items)
    path.write_text(body, encoding="utf-8")
    return [path]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("garminconnect").setLevel(logging.WARNING)
    log = logging.getLogger("garmin_sleep")

    configure_limiter(0.15)

    print()
    print("  Garmin Sleep Export")
    print(f"  {'-' * 21}\n")

    load_env()
    tokenstore = Path(
        os.getenv("GARMINTOKENS", DEFAULT_TOKENSTORE)
    ).expanduser()

    try:
        api = login(tokenstore)
    except GarminConnectTooManyRequestsError:
        log.error("Rate limited (429). Wait 10-15 minutes and retry.")
        return 1
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
        log.error("Authentication failed: %s", exc)
        return 1

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    cache = ExportCache(out, enabled=not args.no_cache)

    try:
        written = _run_for_nights(
            api, out, nights=args.days, cache=cache,
        )
    except KeyboardInterrupt:
        print()
        log.info("Interrupted -- cached data saved, re-run to continue")
        return 130

    if not written:
        log.warning("No sleep data in the last %s night(s)", args.days)
        return 1
    print()
    log.info("Wrote %s sleep summary file(s) to %s", len(written), out)
    for p in written:
        size_kb = p.stat().st_size / 1024
        log.info("  %s (%.1f KB)", p.name, size_kb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
