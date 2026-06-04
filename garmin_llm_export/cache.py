"""Persistent JSON cache for resumable exports."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from .rate_limit import safe_call

log = logging.getLogger(__name__)

def chunked_date_call(fn, start: date, end: date, label: str, chunk_days: int = 365):
    """Call a date-range API in yearly chunks and merge the results.

    Some Garmin endpoints reject ranges longer than ~1 year with a 400.
    This breaks the range into chunks, calls each one, and combines
    the results into a single list.
    """
    all_results = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), end)
        result = safe_call(fn, chunk_start.isoformat(), chunk_end.isoformat(),
                           label=f"{label}_{chunk_start}")
        if result is not None:
            if isinstance(result, list):
                all_results.extend(result)
            else:
                all_results.append(result)
        chunk_start = chunk_end + timedelta(days=1)
    return all_results if all_results else None


# ---------------------------------------------------------------------------
# Cache -- lets interrupted --all exports pick up where they left off.
# Historical days and activities are cached permanently. Days since the
# last run are re-fetched since they weren't complete at cache time.
# ---------------------------------------------------------------------------
class ExportCache:
    """Simple JSON file cache for day-level and activity-level API results.

    Cache lives in {output_dir}/.cache/ and is keyed by date or activity ID.
    Historical data is kept across runs. On startup, any cached days from
    the last run date onward are cleared -- those days may have had
    incomplete data when they were cached.
    """

    def __init__(self, out_dir: Path, enabled: bool = True):
        self.enabled = enabled
        self.cache_dir = out_dir / ".cache"
        self.daily_dir = self.cache_dir / "daily"
        self.activity_dir = self.cache_dir / "activities"
        self.section_dir = self.cache_dir / "sections"
        self.hits = 0
        self.misses = 0

        if not enabled:
            return

        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.activity_dir.mkdir(parents=True, exist_ok=True)
        self.section_dir.mkdir(parents=True, exist_ok=True)

        existing_files = list(self.daily_dir.glob("*.json"))
        daily_health = sum(1 for f in existing_files if f.name[0].isdigit())
        daily_hydration = sum(1 for f in existing_files if f.name.startswith("hydration_"))
        daily_nutrition = sum(1 for f in existing_files if f.name.startswith("nutrition_"))
        existing_acts = len(list(self.activity_dir.glob("*.json")))
        existing_sects = len(list(self.section_dir.glob("*.json")))
        total = len(existing_files) + existing_acts + existing_sects
        if total:
            parts = []
            if daily_health:
                parts.append(f"{daily_health} daily health")
            if daily_hydration:
                parts.append(f"{daily_hydration} hydration")
            if daily_nutrition:
                parts.append(f"{daily_nutrition} nutrition")
            if existing_acts:
                parts.append(f"{existing_acts} activities")
            if existing_sects:
                parts.append(f"{existing_sects} sections")
            log.info(f"Cache: {', '.join(parts)}")

    def _wipe(self):
        """Remove stale cache."""
        import shutil
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir, ignore_errors=True)

    def get_day(self, ds: str) -> Optional[dict]:
        if not self.enabled:
            return None
        path = self.daily_dir / f"{ds}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.hits += 1
                return data
            except (json.JSONDecodeError, OSError):
                pass
        self.misses += 1
        return None

    def put_day(self, ds: str, data: dict):
        if not self.enabled:
            return
        path = self.daily_dir / f"{ds}.json"
        path.write_text(json.dumps(data, default=str, ensure_ascii=False), encoding="utf-8")

    def get_activity(self, activity_id) -> Optional[dict]:
        if not self.enabled:
            return None
        path = self.activity_dir / f"{activity_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.hits += 1
                return data
            except (json.JSONDecodeError, OSError):
                pass
        self.misses += 1
        return None

    def put_activity(self, activity_id, data: dict):
        if not self.enabled:
            return
        path = self.activity_dir / f"{activity_id}.json"
        path.write_text(json.dumps(data, default=str, ensure_ascii=False), encoding="utf-8")

    def get_section(self, name: str) -> Optional[dict]:
        """Get cached data for a whole section (profile, training, etc.)."""
        if not self.enabled:
            return None
        path = self.section_dir / f"{name}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.hits += 1
                return data
            except (json.JSONDecodeError, OSError):
                pass
        self.misses += 1
        return None

    def put_section(self, name: str, data: dict):
        if not self.enabled:
            return
        path = self.section_dir / f"{name}.json"
        path.write_text(json.dumps(data, default=str, ensure_ascii=False), encoding="utf-8")

    def summary(self) -> str:
        total = self.hits + self.misses
        if total == 0:
            return "Cache: no lookups"
        pct = (self.hits / total) * 100
        return f"Cache: {self.hits} hits, {self.misses} misses ({pct:.0f}% hit rate)"
