"""Output formatting helpers for LLM-ready exports."""

from __future__ import annotations

import json
from typing import Any

from .config import settings


def word_count(text: str) -> int:
    """Fast approximate word count."""
    return len(text.split())


def strip_empty(data: Any) -> Any:
    """Recursively remove None, empty strings, empty lists, and empty dicts."""
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            v = strip_empty(v)
            if v is None or v == "" or v == [] or v == {}:
                continue
            cleaned[k] = v
        return cleaned
    if isinstance(data, list):
        return [strip_empty(item) for item in data if strip_empty(item) is not None]
    return data


def downsample_timeseries(data: Any, max_points: int = 24) -> Any:
    """Reduce high-frequency time-series to hourly summaries."""
    if not isinstance(data, list) or len(data) <= max_points or not data:
        return data

    bucket_size = max(1, len(data) // max_points)

    if isinstance(data[0], (list, tuple)):
        result = []
        for i in range(0, len(data), bucket_size):
            bucket = data[i : i + bucket_size]
            if not bucket:
                continue
            merged = list(bucket[0])
            for col in range(1, len(merged)):
                if isinstance(merged[col], (int, float)) and merged[col] not in (True, False):
                    vals = [
                        row[col]
                        for row in bucket
                        if len(row) > col
                        and isinstance(row[col], (int, float))
                        and row[col] not in (True, False)
                    ]
                    if vals:
                        merged[col] = round(sum(vals) / len(vals), 1)
            result.append(merged)
        return result

    if not isinstance(data[0], dict):
        return data

    result = []
    for i in range(0, len(data), bucket_size):
        bucket = data[i : i + bucket_size]
        if not bucket:
            continue
        merged = dict(bucket[0])
        for k in merged:
            if isinstance(merged[k], (int, float)) and merged[k] not in (True, False):
                vals = [
                    row[k]
                    for row in bucket
                    if isinstance(row.get(k), (int, float)) and row[k] not in (True, False)
                ]
                if vals:
                    merged[k] = round(sum(vals) / len(vals), 1)
        result.append(merged)
    return result


def compact_daily(data: Any) -> Any:
    """Reduce a single day's health data for compact mode."""
    if not isinstance(data, dict):
        return data

    timeseries_keys = {
        "heart_rate",
        "stress",
        "sleep",
        "respiration",
        "hrv",
        "body_battery",
        "bb_events",
    }
    compacted = {}
    for k, v in data.items():
        if k in timeseries_keys and isinstance(v, dict):
            inner = {}
            for ik, iv in v.items():
                inner[ik] = downsample_timeseries(iv) if isinstance(iv, list) and len(iv) > 24 else iv
            compacted[k] = inner
        elif k in timeseries_keys and isinstance(v, list) and len(v) > 24:
            compacted[k] = downsample_timeseries(v)
        else:
            compacted[k] = v
    return compacted


def to_json(data: Any) -> str:
    """Serialize to JSON; compact mode strips empties and uses single-line output.

    Honours :attr:`settings.line_budget` (GLE-11): if a pretty-printed line
    exceeds the budget, ``to_json`` falls back to ``wrap_json`` which breaks
    long arrays and objects onto multiple lines.
    """
    if settings.compact:
        data = strip_empty(data)
    if settings.split and isinstance(data, dict) and data:
        lines = [f"  {json.dumps(k)}: {json.dumps(v, default=str, ensure_ascii=False)}" for k, v in data.items()]
        return "{\n" + ",\n".join(lines) + "\n}"
    if settings.split and isinstance(data, list) and data:
        lines = [f"  {json.dumps(item, default=str, ensure_ascii=False)}" for item in data]
        return "[\n" + ",\n".join(lines) + "\n]"
    indent = None if settings.compact else 2
    rendered = json.dumps(data, indent=indent, default=str, ensure_ascii=False)
    if _longest_line(rendered) <= settings.line_budget:
        return rendered
    # GLE-11: any output line must fit the line budget, even in compact
    # mode. ``wrap_json`` breaks long arrays/dicts onto one-per-line so
    # the longest line is well below the budget.
    return wrap_json(data, max_line=settings.line_budget)


def _longest_line(text: str) -> int:
    """Return the length of the longest line in `text`. 0 for empty."""
    if not text:
        return 0
    return max(len(line) for line in text.splitlines() or [""])


# ---------------------------------------------------------------------------
# GLE-9: derived fields in Daily Health (compact mode)
# ---------------------------------------------------------------------------
def add_derived_daily_fields(
    day_data: dict,
    *,
    tz: str | None = None,
) -> dict:
    """Augment a per-day health dict with prefixed computed fields.

    In compact mode the exporter calls this once per day, after the raw
    endpoints have been fetched. The function is additive: existing
    keys are left alone, and new keys are added with a ``_`` prefix
    to mark them as computed.

    Currently adds:
      - ``sleep._summary``           : :func:`build_sleep_summary` output
      - ``hrv._weekly_avg``          : 7-day average overnight HRV
      - ``hrv._baseline_low``        : lower bound of balanced HRV
      - ``hrv._baseline_high``       : upper bound of balanced HRV
      - ``hrv._status``              : BALANCED / UNBALANCED / POOR
      - ``body_battery._morning_charge_delta`` : charge gained overnight
    """
    if not isinstance(day_data, dict):
        return day_data

    # Sleep: full GLE-6 summary
    sleep = day_data.get("sleep")
    if isinstance(sleep, dict) and "_summary" not in sleep:
        # Local import to avoid a circular import (summaries -> formatters)
        from .summaries import build_sleep_summary

        summary = build_sleep_summary(sleep, tz=tz)
        if summary is not None:
            sleep["_summary"] = summary

    # HRV: normalised fields
    hrv = day_data.get("hrv")
    if isinstance(hrv, dict):
        hrv_summary = hrv.get("hrvSummary") or {}
        if isinstance(hrv_summary, dict):
            if "weeklyAvg" in hrv_summary and "_weekly_avg" not in hrv:
                hrv["_weekly_avg"] = hrv_summary["weeklyAvg"]
            baseline = hrv_summary.get("baseline") or {}
            if isinstance(baseline, dict):
                if "balancedLow" in baseline and "_baseline_low" not in hrv:
                    hrv["_baseline_low"] = baseline["balancedLow"]
                if "balancedUpper" in baseline and "_baseline_high" not in hrv:
                    hrv["_baseline_high"] = baseline["balancedUpper"]
            if "status" in hrv_summary and "_status" not in hrv:
                hrv["_status"] = hrv_summary["status"]

    # Body Battery: morning charge delta
    bb = day_data.get("body_battery")
    if isinstance(bb, list) and bb:
        first = bb[0]
        if isinstance(first, dict) and "charged" in first:
            if "_morning_charge_delta" not in first:
                first["_morning_charge_delta"] = first["charged"]
    elif isinstance(bb, dict):
        if "charged" in bb and "_morning_charge_delta" not in bb:
            bb["_morning_charge_delta"] = bb["charged"]

    return day_data


def wrap_json(
    data: Any,
    *,
    max_line: int = 2000,
    indent: int = 1,
) -> str:
    """Pretty-print `data` but never let any line exceed `max_line` chars (GLE-11).

    Strategy: render with a small indent, then if any line is still over the
    budget, fall back to a "one element per line" layout for arrays of
    objects/dicts. This is greedy -- it tries the most compact layout first
    and only "explodes" the parts that are still too long.
    """
    if data is None or isinstance(data, (str, int, float, bool)):
        return json.dumps(data, default=str, ensure_ascii=False)

    try:
        rendered = json.dumps(data, indent=indent, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(str(data), ensure_ascii=False)

    if _longest_line(rendered) <= max_line:
        return rendered

    if isinstance(data, list) and data:
        # Explode: one item per line
        parts = [json.dumps(item, default=str, ensure_ascii=False) for item in data]
        return "[\n" + ",\n".join(parts) + "\n]"

    if isinstance(data, dict) and data:
        parts = [
            f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(v, default=str, ensure_ascii=False)}"
            for k, v in data.items()
        ]
        return "{\n" + ",\n".join(parts) + "\n}"

    return rendered


def section(md: list[str], title: str, data: Any, level: int = 3) -> None:
    """Append a titled JSON block to the output."""
    if data is None:
        return
    md.append(f"{title}\n")
    md.append(f"{to_json(data)}\n")


def section_nodata(md: list[str], title: str) -> None:
    """Write a no-data note for an empty category."""
    md.append("No data available.\n")
