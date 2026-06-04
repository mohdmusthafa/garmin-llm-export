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
    """Serialize to JSON; compact mode strips empties and uses single-line output."""
    if settings.compact:
        data = strip_empty(data)
    if settings.split and isinstance(data, dict) and data:
        lines = [f"  {json.dumps(k)}: {json.dumps(v, default=str, ensure_ascii=False)}" for k, v in data.items()]
        return "{\n" + ",\n".join(lines) + "\n}"
    if settings.split and isinstance(data, list) and data:
        lines = [f"  {json.dumps(item, default=str, ensure_ascii=False)}" for item in data]
        return "[\n" + ",\n".join(lines) + "\n]"
    indent = None if settings.compact else 2
    return json.dumps(data, indent=indent, default=str, ensure_ascii=False)


def section(md: list[str], title: str, data: Any, level: int = 3) -> None:
    """Append a titled JSON block to the output."""
    if data is None:
        return
    md.append(f"{title}\n")
    md.append(f"{to_json(data)}\n")


def section_nodata(md: list[str], title: str) -> None:
    """Write a no-data note for an empty category."""
    md.append("No data available.\n")
