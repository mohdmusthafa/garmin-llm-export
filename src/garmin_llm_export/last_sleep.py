"""The ``--last-sleep`` headline feature (GLE-7).

Renders a small (~10 KB) plain-prose file with the most recent night's
sleep summary. Designed to be readable end-to-end by an LLM in a single
``read`` call: a header line, headline bullets, a sub-scores table, a
verdict, and an appendix with the raw ``dailySleepDTO`` JSON for
completeness.

The function is decoupled from the exporter: it reads from the on-disk
cache (or any mapping of date -> sleep payload) and produces a file at
``{out_dir}/garmin_last_sleep_YYYY-MM-DD_HHMMSS.txt``.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .summaries import build_sleep_summary

log = logging.getLogger(__name__)


def _format_duration(seconds: Optional[int]) -> str:
    """Render a number of seconds as ``Hh Mm`` (e.g. 32580 -> '9h 3m')."""
    if seconds is None or seconds < 0:
        return "?"
    hours, rem = divmod(int(seconds), 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def _format_score(score: Optional[int], qualifier: Optional[str]) -> str:
    """Render the overall sleep score with its qualifier, e.g. '75 (fair)'."""
    if score is None:
        return "?"
    if qualifier:
        return f"{score} ({qualifier})"
    return str(score)


def _bullet_list(items: list[tuple[str, str]]) -> list[str]:
    """Render a list of (label, value) pairs as ``- Label: value`` lines."""
    return [f"- {label}: {value}" for label, value in items]


def _subscores_table(subscores: list[dict[str, Any]]) -> list[str]:
    """Render the subscores as a 2-column markdown-ish table."""
    if not subscores:
        return ["  (no subscores)"]
    lines = [f"  {'Sub-score':<22} {'Value':<8}  Qualifier"]
    lines.append(f"  {'-' * 22} {'-' * 8}  {'-' * 9}")
    for sub in subscores:
        name = str(sub.get("name", "?"))
        value = sub.get("value", "-")
        if value is None:
            value = "-"
        qualifier = sub.get("qualifier", "-") or "-"
        lines.append(f"  {name:<22} {str(value):<8}  {qualifier}")
    return lines


def _render(
    summary: dict[str, Any],
    raw_dto: dict[str, Any],
) -> str:
    """Render a summary dict as a plain-prose file body."""
    score = summary.get("score", {}) or {}
    stages = summary.get("stages", {}) or {}
    vitals = summary.get("vitals", {}) or {}
    resp = vitals.get("respiration", {}) or {}

    bullets = _bullet_list([
        ("Date", summary.get("date") or "?"),
        ("Bedtime", (summary.get("bedtime_local") or "?").replace("T", " ")),
        ("Wake", (summary.get("wake_local") or "?").replace("T", " ")),
        ("Time in bed", _format_duration(summary.get("time_in_bed_seconds"))),
        ("Asleep", _format_duration(summary.get("asleep_seconds"))),
        (
            "Efficiency",
            f"{summary['sleep_efficiency']}%"
            if summary.get("sleep_efficiency") is not None
            else "?",
        ),
        (
            "Score",
            _format_score(score.get("overall"), score.get("qualifier")),
        ),
        (
            "Stages",
            ", ".join(
                f"{stage} {stages[f'{stage}_pct']}%"
                for stage in ("deep", "light", "rem", "awake")
                if f"{stage}_pct" in stages
            ) or "?",
        ),
    ])

    vitals_block = _bullet_list([
        ("Resting HR", f"{vitals['resting_hr']} bpm"
         if vitals.get("resting_hr") is not None else "?"),
        ("Avg sleep stress", vitals.get("avg_sleep_stress") or "?"),
        (
            "Respiration (avg/min/max)",
            (
                f"{resp.get('avg')}/{resp.get('min')}/{resp.get('max')} brpm"
                if resp.get("avg") is not None
                else "?"
            ),
        ),
        ("HRV (avg)", vitals.get("hrv_avg") or "?"),
        ("HRV status", vitals.get("hrv_status") or "?"),
        ("Body battery change", vitals.get("body_battery_change") or "?"),
        ("Awake count", vitals.get("awake_count") or "?"),
        ("Restless moments", vitals.get("restless_moments_count") or "?"),
    ])

    body: list[str] = []
    body.append(f"# Last Night's Sleep -- {summary.get('date') or '?'}\n")

    body.append("Headline")
    body.extend(bullets)
    body.append("")

    body.append("Vitals")
    body.extend(vitals_block)
    body.append("")

    body.append("Sub-scores")
    body.extend(_subscores_table(score.get("subscores") or []))
    body.append("")

    body.append("Verdict")
    body.append(f"  {summary.get('verdict') or 'No verdict available.'}")
    body.append("")

    body.append("Raw data (dailySleepDTO)")
    body.append(
        json.dumps(raw_dto, indent=2, default=str, ensure_ascii=False)
    )
    body.append("")

    return "\n".join(body)


def _read_sleep_payloads(
    cache: Any,
    today: date,
    *,
    lookback_days: int = 4,
) -> list[tuple[date, dict[str, Any]]]:
    """Read sleep payloads from the cache for the most recent N days.

    Returns a list of (date, payload) pairs in reverse chronological order.
    Days without cached sleep data are skipped.
    """
    found: list[tuple[date, dict[str, Any]]] = []
    for offset in range(lookback_days):
        d = today - timedelta(days=offset)
        payload = cache.get_day(d.isoformat())
        if not payload:
            continue
        sleep = payload.get("sleep") if isinstance(payload, dict) else None
        if not sleep or not isinstance(sleep, dict):
            continue
        found.append((d, sleep))
    return found


def write_last_sleep_file(
    cache: Any,
    out_dir: Path,
    *,
    tz: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Write the ``--last-sleep`` file from cached data. Returns the path.

    Args:
        cache: An :class:`ExportCache` instance (or anything with a
            ``get_day(date_str) -> dict | None`` method).
        out_dir: Directory in which the file is created.
        tz: Optional IANA timezone for bedtime/wake rendering. When
            omitted, the summary uses UTC.
        now: Override "now" for deterministic tests.

    Returns:
        The path of the file that was written, or ``None`` when no
        sleep data was found in the cache for the last ``lookback_days``.
    """
    today = date.today()
    payloads = _read_sleep_payloads(cache, today)
    if not payloads:
        log.warning("No sleep data in cache -- cannot write --last-sleep file")
        return None

    # Build summaries for each day; pick the most recent non-None.
    picked: Optional[tuple[date, dict[str, Any]]] = None
    for d, payload in payloads:
        summary = build_sleep_summary(payload, tz=tz)
        if summary is not None:
            picked = (d, payload)
            break
    if picked is None:
        log.warning("Sleep payloads exist but none summarise cleanly")
        return None

    d, payload = picked
    summary = build_sleep_summary(payload, tz=tz)
    assert summary is not None  # just built it
    raw_dto = payload.get("dailySleepDTO") or {}

    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    path = out_dir / f"garmin_last_sleep_{stamp}.txt"
    body = _render(summary, raw_dto)
    path.write_text(body, encoding="utf-8")
    log.info(f"Wrote last-sleep summary: {path}")
    return path
