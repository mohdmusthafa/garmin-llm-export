"""Garmin Connect data exporter."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple, Optional, Set

from garminconnect import Garmin

from .cache import ExportCache, chunked_date_call
from .config import settings
from .formatters import (
    compact_daily,
    section,
    section_nodata,
    to_json,
    word_count,
)
from .rate_limit import get_limiter, safe_call

log = logging.getLogger(__name__)


class SectionDef(NamedTuple):
    """Metadata for a single export section.

    - id: short identifier used on the CLI (e.g. "daily_health").
    - display: human-readable name used in the file (e.g. "Daily Health").
    - description: one-liner used in the table of contents.
    - method_name: name of the method on GarminExporter that emits this section.
    - cache_key: key under which this section is cached as a whole, or None
      for per-day / per-activity sections that use a different cache scheme.
    """

    id: str
    display: str
    description: str
    method_name: str
    cache_key: Optional[str]


# Canonical section registry. The order here defines the order sections
# appear in the file. Each section is identified by a short id used on the
# CLI; cache_key=None means the section is per-day or per-activity and uses
# ExportCache.get_day / get_activity rather than get_section.
SECTION_REGISTRY: tuple[SectionDef, ...] = (
    SectionDef(
        "profile", "Profile",
        "User info, settings, device details, alarms, supported activity types",
        "export_profile", "profile",
    ),
    SectionDef(
        "daily_health", "Daily Health",
        "Per-day: steps, heart rate, sleep, stress, body battery, SpO2, HRV, "
        "respiration, intensity minutes, all-day events",
        "export_daily_health", None,
    ),
    SectionDef(
        "activities", "Activities",
        "Per-activity: summary, splits, HR/power zones, exercise sets, "
        "weather, time-series data",
        "export_activities", None,
    ),
    SectionDef(
        "body_composition", "Body Composition",
        "Weight, BMI, body fat, muscle/bone mass, body water, weigh-ins "
        "(yearly chunks)",
        "export_body_composition", "body_comp",
    ),
    SectionDef(
        "training", "Training Metrics",
        "VO2 max, fitness age, training readiness/status, lactate threshold, "
        "cycling FTP, hill/endurance scores, race predictions",
        "export_training", "training",
    ),
    SectionDef(
        "goals", "Goals and Records",
        "Personal records, earned badges, active and past goals",
        "export_goals", "goals",
    ),
    SectionDef(
        "trends", "Trends",
        "Weekly aggregates (steps, stress, intensity minutes), daily steps, "
        "floors, progress summaries",
        "export_trends", "trends",
    ),
    SectionDef(
        "golf", "Golf",
        "Round summaries, scorecards, shot data",
        "export_golf", "golf",
    ),
    SectionDef(
        "gear", "Gear",
        "Equipment list, per-item stats, activity type defaults",
        "export_gear", "gear",
    ),
    SectionDef(
        "training_plans", "Training Plans",
        "Active and past training plans with full details",
        "export_training_plans", "training_plans",
    ),
    SectionDef(
        "workouts", "Workouts",
        "Saved workout definitions with full structure",
        "export_workouts", "workouts",
    ),
    SectionDef(
        "hydration", "Hydration",
        "Per-day fluid intake",
        "export_hydration", None,
    ),
    SectionDef(
        "nutrition", "Nutrition",
        "Per-day food logs, meals, nutrition settings",
        "export_nutrition", None,
    ),
    SectionDef(
        "womens_health", "Women's Health",
        "Menstrual calendar, pregnancy summary",
        "export_womens_health", "womens_health",
    ),
)


def _format_age(age: Optional[timedelta]) -> str:
    """Render a cache age as a short human-readable string (e.g. "3d", "12h")."""
    if age is None:
        return "?"
    total = int(age.total_seconds())
    if total < 0:
        return "0s"
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


class GarminExporter:
    def __init__(self, api: Garmin, out_dir: Path, days: int, max_activities: int,
                 fetch_all: bool = False, cache: Optional[ExportCache] = None,
                 update_mode: bool = False,
                 sections: Optional[Set[str]] = None):
        self.api = api
        self.out = out_dir
        self.max_activities = max_activities
        self.fetch_all = fetch_all
        self.update_mode = update_mode
        self.cache = cache or ExportCache(out_dir, enabled=False)
        self.today = date.today()
        self.errors: list[str] = []
        self.md: list[str] = []
        self.update_base_date: Optional[str] = None  # end date of base export
        # None means "all sections"; a set means "only these section ids".
        self.sections_filter: Optional[Set[str]] = sections

        if update_mode:
            base_end = self._find_latest_export_end_date()
            if base_end:
                self.update_base_date = base_end.isoformat()
                # Overlap by 1 day to catch late-arriving data
                self.start_date = base_end - timedelta(days=1)
                log.info(f"Update mode: last export ends {base_end}, "
                         f"fetching from {self.start_date}")
            else:
                log.warning("No previous export found -- falling back to --days")
                self.start_date = self.today - timedelta(days=days)
        elif fetch_all:
            self.start_date = self._detect_start_date()
        else:
            self.start_date = self.today - timedelta(days=days)
        self.days = (self.today - self.start_date).days

    def _find_latest_export_end_date(self) -> Optional[date]:
        """Scan output directory for the most recent export and parse its end date."""
        candidates = sorted(self.out.glob("garmin_export_*.txt"), reverse=True)
        if not candidates:
            return None

        # Group by base timestamp (ignore _partNofM suffix)
        # e.g. garmin_export_2026-03-22_150348_compact_part1of6.txt -> 2026-03-22_150348
        ts_pattern = re.compile(r'garmin_export_(\d{4}-\d{2}-\d{2}_\d{6})')
        timestamps: dict[str, Path] = {}
        for p in candidates:
            m = ts_pattern.search(p.name)
            if m:
                ts = m.group(1)
                if ts not in timestamps:
                    timestamps[ts] = p

        if not timestamps:
            return None

        # Pick the newest export (first key after reverse-sorted glob)
        newest_ts = sorted(timestamps.keys(), reverse=True)[0]
        newest_file = timestamps[newest_ts]

        # Read header and parse "Date range: X to Y"
        try:
            with open(newest_file, "r", encoding="utf-8") as f:
                header = f.read(2000)
            m = re.search(r'Date range:\s*(\S+)\s+to\s+(\S+)', header)
            if m:
                end_str = m.group(2)
                end_date = date.fromisoformat(end_str)
                log.info(f"Found latest export: {newest_file.name} "
                         f"(ends {end_date})")
                return end_date
        except (OSError, ValueError) as e:
            log.warning(f"Could not parse export header: {e}")

        # Fallback: parse date from filename timestamp
        try:
            d = datetime.strptime(newest_ts, "%Y-%m-%d_%H%M%S").date()
            log.info(f"Using filename date as fallback: {d}")
            return d
        except ValueError:
            return None

    def _detect_start_date(self) -> date:
        """Figure out how far back the user's data goes.

        Tries to find the oldest activity, then pads a week earlier
        to catch any health data before the first tracked activity.
        Falls back to 5 years if we can't determine it.
        """
        log.info("Detecting account history range...")

        # Try getting the oldest activity (sort ascending, grab first)
        oldest = safe_call(
            self.api.get_activities_by_date,
            "2000-01-01", self.today.isoformat(), None, "asc",
            label="oldest_activity",
        )
        if oldest and isinstance(oldest, list) and len(oldest) > 0:
            first_act = oldest[0]
            start_str = first_act.get("startTimeLocal", "")[:10]
            if start_str:
                try:
                    d = date.fromisoformat(start_str)
                    # Pad a week earlier to catch pre-activity health data
                    d = d - timedelta(days=7)
                    log.info(f"Oldest activity found: {start_str}")
                    log.info(f"Will export from: {d}")
                    return d
                except ValueError:
                    pass

        # Fallback: 5 years
        fallback = self.today - timedelta(days=365 * 5)
        log.info(f"Could not detect oldest data, defaulting to {fallback}")
        return fallback

    def _selected_sections(self) -> tuple[SectionDef, ...]:
        """Apply the section filter (if any) and return the chosen SECTION_REGISTRY entries."""
        if self.sections_filter is None:
            return SECTION_REGISTRY
        return tuple(s for s in SECTION_REGISTRY if s.id in self.sections_filter)

    def _get_fresh_cached_section(
        self, cache_key: Optional[str], display_name: str
    ) -> Optional[dict]:
        """Return cached section data if the cache is fresh, else None.

        Logs a single line at INFO when the cache is used, e.g.:
            "Profile: using cache (3d old)"

        Returns None when:
        - the section is per-day/per-activity (cache_key is None),
        - the cache is disabled,
        - the export is in update mode (always re-fetch),
        - the section file is missing, or
        - the section file is older than its max age.
        """
        if cache_key is None or not self.cache.enabled or self.update_mode:
            return None
        if not self.cache.is_section_fresh(cache_key):
            return None
        data = self.cache.get_section(cache_key)
        if data is None:
            return None
        age_str = _format_age(self.cache.section_age(cache_key))
        log.info(f"  {display_name}: using cache ({age_str} old)")
        return data

    def run(self):
        now = datetime.now()
        suffix = ""
        if self.update_mode:
            suffix = "_update"
        filename = f"garmin_export_{now.strftime('%Y-%m-%d_%H%M%S')}{suffix}.txt"

        log.info(f"Date range: {self.start_date} to {self.today} ({self.days} days)")
        if self.update_mode:
            log.info(f"Mode: update (new data since {self.update_base_date})")
        if settings.compact:
            log.info("Mode: compact (smaller output for LLM upload)")
        if settings.split:
            log.info("Mode: split (multiple files, <500K words each)")
        if self.fetch_all and not self.update_mode:
            log.info(f"Mode: --all (fetching complete history)")
            log.info(f"Max activities: unlimited")
        else:
            log.info(f"Max activities: {self.max_activities}")
        if self.sections_filter is not None:
            log.info(f"Sections: {', '.join(sorted(self.sections_filter))}")
        print()

        if self.update_mode:
            self.md.append("Garmin Connect Data Export -- Update\n")
            self.md.append(f"Exported: {now.isoformat()}")
            self.md.append(f"Update for data since: {self.update_base_date}")
            self.md.append(f"Date range: {self.start_date} to {self.today} ({self.days} days)")
            self.md.append(f"This file contains only new data. Upload alongside your base export files.\n")
        else:
            self.md.append("Garmin Connect Data Export\n")
            self.md.append(f"Exported: {now.isoformat()}")
            self.md.append(f"Date range: {self.start_date} to {self.today} ({self.days} days)")
            self.md.append(f"Max activities: {self.max_activities}")
        if self.sections_filter is not None:
            self.md.append(
                f"Sections: {', '.join(sorted(self.sections_filter))}"
            )
        if settings.compact:
            self.md.append(f"Format: compact (nulls stripped, single-line JSON, "
                           f"activity time-series omitted, daily data downsampled to hourly)\n")
        else:
            self.md.append(f"Format: full (complete JSON, all fields)\n")

        # Resolve which sections to actually run (and which to list in the TOC)
        selected = self._selected_sections()
        if not selected:
            log.warning("Section filter matched no sections -- writing empty export")

        # Table of contents for AI parsing
        self.md.append("Table of Contents\n")
        if self.update_mode:
            self.md.append(f"This file contains NEW data since {self.update_base_date}.")
            self.md.append("Upload alongside your base export files for complete coverage.")
        else:
            self.md.append("This file contains a complete export of Garmin Connect health and fitness data.")
        if settings.compact:
            self.md.append("Each section contains one JSON block with a schema description.")
            self.md.append("All data is raw JSON from the Garmin Connect API. In compact mode, each section is a single JSON block.")
        else:
            self.md.append("Each section has subsections with titled JSON blocks.")
            self.md.append("All data is raw JSON from the Garmin Connect API.")
        self.md.append("Sections with no data contain a note: No data available.\n")
        for i, sec in enumerate(selected, 1):
            self.md.append(f"  {i}. {sec.display} -- {sec.description}")
        self.md.append("")

        for sec in selected:
            name = sec.display
            fn = getattr(self, sec.method_name)
            log.info(f"Exporting {name}...")
            try:
                fn()
                log.info(f"  Done: {name}")
            except KeyboardInterrupt:
                log.info(f"\n  Interrupted during {name} -- saving partial export")
                self.errors.append(f"{name}: interrupted by user (partial data)")
                break
            except Exception as e:
                self.errors.append(f"{name}: {e}")
                log.error(f"  Failed: {name}: {e}")
                log.debug(traceback.format_exc())

        if self.errors:
            self.md.append("\nErrors During Export\n")
            for err in self.errors:
                self.md.append(f"- {err}")
            self.md.append("")

        # Footer omitted -- stats logged to console only

        full_text = "\n".join(self.md)

        if settings.split:
            written = self._write_split(full_text, filename)
            print()
            log.info(f"Export complete: {len(written)} files in {self.out}")
            total_kb = sum(p.stat().st_size for p in written) / 1024
            log.info(f"Total size: {total_kb:.0f} KB across {len(written)} files")
        else:
            out_path = self.out / filename
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(full_text, encoding="utf-8")
            size_kb = out_path.stat().st_size / 1024
            print()
            log.info(f"Export complete: {out_path}")
            log.info(f"File size: {size_kb:.0f} KB")

        log.info(f"API calls: {get_limiter().call_count}")
        log.info(self.cache.summary())
        if self.errors:
            log.warning(f"{len(self.errors)} section(s) had errors")

    def _write_split(self, full_text: str, base_filename: str) -> list:
        """Split the export into multiple files, each under the word limit.

        Splits at section boundaries (known section names on their own line).
        Oversized sections (like Daily Health) get their JSON content broken
        into date-range or item-count chunks. Sections are packed greedily
        into files. Output uses .txt for best compatibility with RAG tools
        like NotebookLM.
        """
        # Known section names that appear on their own line
        section_names_list = [
            "Profile", "Daily Health", "Activities", "Body Composition",
            "Training Metrics", "Goals and Records", "Trends", "Golf",
            "Gear", "Training Plans", "Workouts", "Hydration", "Nutrition",
            "Women's Health", "Errors During Export",
        ]
        # Build regex that splits at lines matching any known section name
        escaped = [re.escape(n) for n in section_names_list]
        split_pattern = r'(?=\n(?:' + '|'.join(escaped) + r')\n)'
        parts = re.split(split_pattern, full_text)

        # First part is the file header (title, date, format, TOC)
        file_header = parts[0] if parts else ""
        header_words = word_count(file_header)
        raw_sections = parts[1:] if len(parts) > 1 else []

        # Break oversized sections, keep small ones as-is
        section_chunks = []  # list of (display_name, text)
        for sec_text in raw_sections:
            # Extract section name from first non-empty line
            sec_name = sec_text.strip().split('\n')[0].strip()
            wc = word_count(sec_text)

            if wc <= settings.split_word_limit * 0.85:
                section_chunks.append((sec_name, sec_text))
            else:
                sub = self._split_oversizedsection(sec_text, sec_name)
                section_chunks.extend(sub)

        # Greedy bin-packing into files
        files = []  # list of list of (name, text)
        current_file = []
        current_words = header_words

        for name, text in section_chunks:
            chunk_words = word_count(text)
            if current_words + chunk_words > settings.split_word_limit and current_file:
                files.append(current_file)
                current_file = []
                current_words = header_words
            current_file.append((name, text))
            current_words += chunk_words

        if current_file:
            files.append(current_file)

        # Write each file
        total = len(files)
        written = []
        for i, file_sections in enumerate(files, 1):
            section_names = [n for n, _ in file_sections]

            header = f"Garmin Connect Data Export -- Part {i} of {total}\n\n"
            header += f"Sections in this file: {', '.join(section_names)}\n"
            header += f"Upload all {total} parts to the same notebook for complete data.\n\n"

            content = header + "\n".join(text for _, text in file_sections)

            suffix = f"_split_part{i}of{total}"
            fname = base_filename.replace(".txt", f"{suffix}.txt")
            path = self.out / fname
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

            wc = word_count(content)
            size_kb = path.stat().st_size / 1024
            written.append(path)
            log.info(f"  Part {i}/{total}: {fname} ({wc:,} words, {size_kb:.0f} KB)")

        return written

    def _split_oversizedsection(self, sec_text: str, sec_name: str) -> list:
        """Break a single oversized section by splitting its JSON content.

        For dict-keyed JSON (daily health, hydration, nutrition): splits by
        date-range groups. For array JSON (activities): splits by item count.
        """
        # Find the schema line and JSON code fence
        match = re.search(
            r'(Schema:[^\n]*\n)\s*(\{.*\}|\[.*\])',
            sec_text, re.DOTALL,
        )
        if not match:
            return [(sec_name, sec_text)]

        schema_line = match.group(1)
        json_str = match.group(2)

        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, TypeError):
            return [(sec_name, sec_text)]

        target_words = int(settings.split_word_limit * 0.8)

        if isinstance(data, dict) and data:
            # Build chunks by actual word count per key
            keys = list(data.keys())
            groups = []
            cur_keys = []
            cur_data = {}
            cur_words = 0

            for k in keys:
                item_str = json.dumps({k: data[k]}, default=str, ensure_ascii=False)
                item_words = word_count(item_str)

                if cur_words + item_words > target_words and cur_keys:
                    groups.append((cur_keys[0], cur_keys[-1], cur_data))
                    cur_keys = []
                    cur_data = {}
                    cur_words = 0

                cur_keys.append(k)
                cur_data[k] = data[k]
                cur_words += item_words

            if cur_keys:
                groups.append((cur_keys[0], cur_keys[-1], cur_data))

            results = []
            for idx, (first, last, chunk_data) in enumerate(groups, 1):
                part_name = f"{sec_name} (Part {idx} of {len(groups)}: {first} to {last})"
                text = f"{part_name}\n\n{schema_line}\n{to_json(chunk_data)}\n"
                results.append((part_name, text))
            return results

        elif isinstance(data, list) and data:
            # Build chunks by actual word count per item
            groups = []
            cur_items = []
            cur_start = 1
            cur_words = 0

            for i, item in enumerate(data):
                item_str = json.dumps(item, default=str, ensure_ascii=False)
                item_words = word_count(item_str)

                if cur_words + item_words > target_words and cur_items:
                    groups.append((cur_start, cur_start + len(cur_items) - 1, cur_items))
                    cur_items = []
                    cur_start = i + 1
                    cur_words = 0

                cur_items.append(item)
                cur_words += item_words

            if cur_items:
                groups.append((cur_start, cur_start + len(cur_items) - 1, cur_items))

            results = []
            for idx, (start, end, chunk_data) in enumerate(groups, 1):
                part_name = f"{sec_name} (Part {idx} of {len(groups)}: items {start}-{end})"
                text = f"{part_name}\n\n{schema_line}\n{to_json(chunk_data)}\n"
                results.append((part_name, text))
            return results

        return [(sec_name, sec_text)]

    # ===================================================================
    # Profile
    # ===================================================================
    def export_profile(self):
        self.md.append("\nProfile\n")

        cached = self._get_fresh_cached_section("profile", "Profile")
        if cached is not None:
            data = cached
        else:
            data = {}
            data["full_name"] = safe_call(self.api.get_full_name, label="full_name")
            data["unit_system"] = safe_call(self.api.get_unit_system, label="unit_system")
            data["user_profile"] = safe_call(self.api.get_user_profile, label="user_profile")
            data["profile_settings"] = safe_call(self.api.get_userprofile_settings, label="profile_settings")
            data["devices"] = safe_call(self.api.get_devices, label="devices")
            data["primary_device"] = safe_call(self.api.get_primary_training_device, label="primary_device")
            data["device_alarms"] = safe_call(self.api.get_device_alarms, label="device_alarms")
            data["last_used_device"] = safe_call(self.api.get_device_last_used, label="last_used_device")
            data["activity_types"] = safe_call(self.api.get_activity_types, label="activity_types")
            self.cache.put_section("profile", data)

        if settings.compact:
            self.md.append('Schema: "User profile data: full_name, unit_system, user_profile (demographics), profile_settings, devices (paired devices), primary_device, device_alarms, last_used_device, activity_types (supported types)."\n')
            self.md.append(f"{to_json(data)}\n")
        else:
            for title, key in [("Full Name", "full_name"), ("Unit System", "unit_system"),
                               ("User Profile", "user_profile"), ("Profile Settings", "profile_settings"),
                               ("Devices", "devices"), ("Primary Training Device", "primary_device"),
                               ("Device Alarms", "device_alarms"), ("Last Used Device", "last_used_device"),
                               ("Activity Types", "activity_types")]:
                section(self.md, title, data.get(key))

    # ===================================================================
    # Daily Health -- one section per day, complete API responses
    # ===================================================================
    def export_daily_health(self):
        weeks = self.days / 7
        months = self.days / 30.44
        log.info(f"  {self.days} days to process ({months:.1f} months / {weeks:.0f} weeks)")
        log.info(f"  Date range: {self.start_date} to {self.today}")
        log.info(f"  13 API calls per day, fetching 4 at a time")

        self.md.append("\nDaily Health\n")

        # Endpoint keys in display order
        endpoint_keys = [
            "summary", "heart_rate", "rhr", "sleep", "stress", "spo2",
            "respiration", "hrv", "body_battery", "bb_events",
            "intensity_min", "events", "lifestyle",
        ]

        display_names = {
            "summary": "Daily Summary", "heart_rate": "Heart Rate",
            "rhr": "Resting Heart Rate", "sleep": "Sleep", "stress": "Stress",
            "spo2": "Blood Oxygen (SpO2)", "respiration": "Respiration",
            "hrv": "Heart Rate Variability", "body_battery": "Body Battery",
            "bb_events": "Body Battery Events", "intensity_min": "Intensity Minutes",
            "events": "All Day Events", "lifestyle": "Lifestyle Logging",
        }

        def _fetch_endpoint(key, ds):
            """Fetch a single endpoint for a given date. Runs in a thread."""
            api = self.api
            if key == "summary":
                return safe_call(api.get_user_summary, ds, label=f"summary_{ds}")
            elif key == "heart_rate":
                return safe_call(api.get_heart_rates, ds, label=f"hr_{ds}")
            elif key == "rhr":
                return safe_call(api.get_rhr_day, ds, label=f"rhr_{ds}")
            elif key == "sleep":
                return safe_call(api.get_sleep_data, ds, label=f"sleep_{ds}")
            elif key == "stress":
                return safe_call(api.get_all_day_stress, ds, label=f"stress_{ds}")
            elif key == "spo2":
                return safe_call(api.get_spo2_data, ds, label=f"spo2_{ds}")
            elif key == "respiration":
                return safe_call(api.get_respiration_data, ds, label=f"resp_{ds}")
            elif key == "hrv":
                return safe_call(api.get_hrv_data, ds, label=f"hrv_{ds}")
            elif key == "body_battery":
                return safe_call(api.get_body_battery, ds, ds, label=f"bb_{ds}")
            elif key == "bb_events":
                return safe_call(api.get_body_battery_events, ds, label=f"bbe_{ds}")
            elif key == "intensity_min":
                return safe_call(api.get_intensity_minutes_data, ds, label=f"im_{ds}")
            elif key == "events":
                return safe_call(api.get_all_day_events, ds, label=f"events_{ds}")
            elif key == "lifestyle":
                return safe_call(api.get_lifestyle_logging_data, ds, label=f"ll_{ds}")

        t_start = time.time()
        cached_days = 0
        if settings.compact:
            all_days = {}

        for i in range(self.days):
            d = self.today - timedelta(days=i)
            ds = d.isoformat()

            # Check cache first
            day_data = self.cache.get_day(ds)
            if day_data is not None:
                cached_days += 1
            else:
                # Fetch all 13 endpoints concurrently (4 threads)
                day_data = {}
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = {
                        pool.submit(_fetch_endpoint, key, ds): key
                        for key in endpoint_keys
                    }
                    for future in as_completed(futures):
                        key = futures[future]
                        try:
                            day_data[key] = future.result()
                        except Exception:
                            day_data[key] = None

                self.cache.put_day(ds, day_data)

            # Write to markdown
            if settings.compact:
                write_data = compact_daily(day_data)
                merged = {display_names.get(k, k): v for k, v in write_data.items() if v is not None}
                if merged:
                    all_days[ds] = merged
            else:
                self.md.append(f"{ds}\n")
                for key in endpoint_keys:
                    section(self.md, display_names[key], day_data.get(key), 4)

            # Progress reporting -- frequent early on, then every 25 days
            done = i + 1
            report_interval = 5 if done <= 25 else 25
            if done % report_interval == 0 or done == 1 or done == self.days:
                elapsed = time.time() - t_start
                d_display = d.isoformat()
                if done > cached_days and (done - cached_days) > 0:
                    fetched = done - cached_days
                    per_day = elapsed / fetched
                    remaining_fetch = max(0, self.days - done)
                    eta_sec = remaining_fetch * per_day
                    eta_min = eta_sec / 60
                    log.info(f"  {done}/{self.days} days ({d_display}) | "
                             f"{cached_days} cached | "
                             f"{get_limiter().call_count} calls | ~{eta_min:.0f}m remaining")
                else:
                    log.info(f"  {done}/{self.days} days ({d_display}) | "
                             f"{cached_days} cached (all from cache so far)")

        if settings.compact:
            if all_days:
                self.md.append('Schema: "Object keyed by ISO date (YYYY-MM-DD). Each day contains up to 13 endpoints: Daily Summary, Heart Rate, Resting Heart Rate, Sleep, Stress, Blood Oxygen (SpO2), Respiration, Heart Rate Variability, Body Battery, Body Battery Events, Intensity Minutes, All Day Events, Lifestyle Logging. High-frequency time-series downsampled to ~24 hourly data points."\n')
                self.md.append(f"{to_json(all_days)}\n")
            else:
                section_nodata(self.md, "Daily Health")

    # ===================================================================
    # Activities -- complete data for every activity
    # ===================================================================
    def export_activities(self):
        self.md.append("\nActivities\n")

        if self.fetch_all or self.update_mode:
            activities = safe_call(
                self.api.get_activities_by_date,
                self.start_date.isoformat(), self.today.isoformat(), None,
                label="activities_all",
            ) or []
        else:
            activities = safe_call(
                self.api.get_activities, 0, self.max_activities,
                label="activities_list",
            ) or []
            if not activities:
                activities = safe_call(
                    self.api.get_activities_by_date,
                    self.start_date.isoformat(), self.today.isoformat(), "",
                    label="activities_by_date",
                ) or []

        self.md.append(f"Total activities found: {len(activities)}\n")
        log.info(f"  {len(activities)} activities found, 10 API calls each = up to {len(activities) * 10:,} calls")

        t_start = time.time()
        cached_acts = 0
        if settings.compact:
            all_activities = []

        for i, act in enumerate(activities):
            aid = act.get("activityId", i)
            name = act.get("activityName") or "Unnamed"
            atype = (act.get("activityType", {}).get("typeKey", "?")
                     if isinstance(act.get("activityType"), dict)
                     else str(act.get("activityType", "?")))
            start = act.get("startTimeLocal", "")

            if not settings.compact:
                self.md.append(f"Activity {aid}: {name}\n")
                self.md.append(f"Type: {atype} | Date: {start}\n")

            # Check cache
            act_data = self.cache.get_activity(aid)
            if act_data is not None:
                cached_acts += 1
            else:
                # Fetch from API and cache
                act_data = {"summary": act}
                act_data["detail"] = safe_call(self.api.get_activity, aid, label=f"act_{aid}")
                act_data["splits"] = safe_call(self.api.get_activity_splits, aid, label=f"splits_{aid}")
                act_data["split_summaries"] = safe_call(self.api.get_activity_split_summaries, aid, label=f"ss_{aid}")
                act_data["typed_splits"] = safe_call(self.api.get_activity_typed_splits, aid, label=f"typed_splits_{aid}")
                act_data["weather"] = safe_call(self.api.get_activity_weather, aid, label=f"wx_{aid}")
                act_data["hr_zones"] = safe_call(self.api.get_activity_hr_in_timezones, aid, label=f"hrz_{aid}")
                act_data["power_zones"] = safe_call(self.api.get_activity_power_in_timezones, aid, label=f"pwrz_{aid}")
                act_data["exercise_sets"] = safe_call(self.api.get_activity_exercise_sets, aid, label=f"sets_{aid}")
                act_data["details"] = safe_call(self.api.get_activity_details, aid, label=f"details_{aid}")
                self.cache.put_activity(aid, act_data)

            if settings.compact:
                act_display_keys = ["summary", "detail", "splits", "split_summaries",
                                    "typed_splits", "weather", "hr_zones", "power_zones",
                                    "exercise_sets"]
                merged = {}
                for k in act_display_keys:
                    v = act_data.get(k)
                    if v is not None:
                        merged[k] = v
                if merged:
                    all_activities.append(merged)
            else:
                section(self.md, "Activity Summary", act_data.get("summary"), 4)
                section(self.md, "Full Activity Detail", act_data.get("detail"), 4)
                section(self.md, "Splits", act_data.get("splits"), 4)
                section(self.md, "Split Summaries", act_data.get("split_summaries"), 4)
                section(self.md, "Typed Splits", act_data.get("typed_splits"), 4)
                section(self.md, "Weather", act_data.get("weather"), 4)
                section(self.md, "HR Zones", act_data.get("hr_zones"), 4)
                section(self.md, "Power Zones", act_data.get("power_zones"), 4)
                section(self.md, "Exercise Sets", act_data.get("exercise_sets"), 4)
                section(self.md, "Time-Series Details", act_data.get("details"), 4)

            done = i + 1
            if done % 10 == 0 or done == len(activities):
                elapsed = time.time() - t_start
                if done > cached_acts:
                    fetched = done - cached_acts
                    per_act = elapsed / fetched
                    remaining = max(0, len(activities) - done)
                    eta_min = (remaining * per_act) / 60
                    log.info(f"  {done}/{len(activities)} activities | {cached_acts} cached | "
                             f"{get_limiter().call_count} calls | ~{eta_min:.0f}m remaining")
                else:
                    log.info(f"  {done}/{len(activities)} activities | {cached_acts} cached")

        if settings.compact:
            if all_activities:
                self.md.append('Schema: "Array of activity objects. Each contains: summary (overview, stats), detail (full activity record), splits, split_summaries, typed_splits, weather, hr_zones, power_zones, exercise_sets. Time-series details omitted for size."\n')
                self.md.append(f"{to_json(all_activities)}\n")
            else:
                section_nodata(self.md, "Activities")

    # ===================================================================
    # Body Composition
    # ===================================================================
    def export_body_composition(self):
        self.md.append("\nBody Composition\n")

        cached = self._get_fresh_cached_section("body_comp", "Body Composition")
        if cached is not None:
            data = cached
        else:
            data = {}
            data["body_comp"] = chunked_date_call(self.api.get_body_composition,
                                                   self.start_date, self.today, "body_comp")
            data["weigh_ins"] = chunked_date_call(self.api.get_weigh_ins,
                                                   self.start_date, self.today, "weigh_ins")
            self.cache.put_section("body_comp", data)

        if settings.compact:
            self.md.append('Schema: "body_comp: weight/BMI/body fat percentage history in yearly chunks. weigh_ins: individual scale readings with timestamps."\n')
            self.md.append(f"{to_json(data)}\n")
        else:
            section(self.md, "Body Composition", data.get("body_comp"))
            section(self.md, "Weigh-Ins", data.get("weigh_ins"))

    # ===================================================================
    # Training Metrics
    # ===================================================================
    def export_training(self):
        self.md.append("\nTraining Metrics\n")

        cached = self._get_fresh_cached_section("training", "Training Metrics")
        if cached is not None:
            data = cached
        else:
            today_s = self.today.isoformat()
            start_s = self.start_date.isoformat()

            items = [
                ("training_readiness", "Training Readiness",
                 safe_call(self.api.get_training_readiness, today_s, label="training_readiness")),
                ("morning_readiness", "Morning Training Readiness",
                 safe_call(self.api.get_morning_training_readiness, today_s, label="morning_readiness")),
                ("training_status", "Training Status",
                 safe_call(self.api.get_training_status, today_s, label="training_status")),
                ("max_metrics", "VO2 Max and Max Metrics",
                 safe_call(self.api.get_max_metrics, today_s, label="max_metrics")),
                ("fitness_age", "Fitness Age",
                 safe_call(self.api.get_fitnessage_data, today_s, label="fitness_age")),
                ("lactate_threshold", "Lactate Threshold",
                 safe_call(self.api.get_lactate_threshold, label="lactate_threshold")),
                ("cycling_ftp", "Cycling FTP",
                 safe_call(self.api.get_cycling_ftp, label="cycling_ftp")),
                ("intensity_min", "Intensity Minutes",
                 safe_call(self.api.get_intensity_minutes_data, today_s, label="intensity_min")),
                ("hill_score", "Hill Score",
                 chunked_date_call(self.api.get_hill_score, self.start_date, self.today, "hill_score")),
                ("endurance_score", "Endurance Score",
                 chunked_date_call(self.api.get_endurance_score, self.start_date, self.today, "endurance_score")),
                ("running_tolerance", "Running Tolerance",
                 chunked_date_call(self.api.get_running_tolerance, self.start_date, self.today, "running_tolerance")),
                ("race_predictions", "Race Predictions",
                 safe_call(self.api.get_race_predictions, label="race_predictions")),
            ]

            data = {}
            for key, title, result in items:
                data[key] = result
                data[f"_title_{key}"] = title
            self.cache.put_section("training", data)

        if settings.compact:
            compact_data = {k: v for k, v in data.items() if not k.startswith("_title_")}
            self.md.append('Schema: "Training metrics: training_readiness, morning_readiness, training_status, max_metrics (VO2 max), fitness_age, lactate_threshold, cycling_ftp, intensity_min, hill_score (history), endurance_score (history), running_tolerance (history), race_predictions."\n')
            self.md.append(f"{to_json(compact_data)}\n")
        else:
            for key in ["training_readiness", "morning_readiness", "training_status",
                        "max_metrics", "fitness_age", "lactate_threshold", "cycling_ftp",
                        "intensity_min", "hill_score", "endurance_score", "running_tolerance",
                        "race_predictions"]:
                section(self.md, data.get(f"_title_{key}", key), data.get(key))

    # ===================================================================
    # Goals and Records
    # ===================================================================
    def export_goals(self):
        self.md.append("\nGoals and Records\n")

        cached = self._get_fresh_cached_section("goals", "Goals and Records")
        if cached is not None:
            data = cached
        else:
            data = {}
            data["personal_records"] = safe_call(self.api.get_personal_record, label="personal_records")
            data["badges"] = safe_call(self.api.get_earned_badges, label="badges")
            data["active_goals"] = safe_call(self.api.get_goals, "active", 0, 100, label="active_goals")
            data["past_goals"] = safe_call(self.api.get_goals, "past", 0, 100, label="past_goals")
            self.cache.put_section("goals", data)

        if settings.compact:
            self.md.append('Schema: "personal_records: lifetime bests by activity type. badges: earned achievement badges. active_goals: current goals. past_goals: completed/expired goals."\n')
            self.md.append(f"{to_json(data)}\n")
        else:
            section(self.md, "Personal Records", data.get("personal_records"))
            section(self.md, "Earned Badges", data.get("badges"))
            section(self.md, "Active Goals", data.get("active_goals"))
            section(self.md, "Past Goals", data.get("past_goals"))

    # ===================================================================
    # Trends
    # ===================================================================
    def export_trends(self):
        self.md.append("\nTrends\n")

        cached = self._get_fresh_cached_section("trends", "Trends")
        if cached is not None:
            data = cached
        else:
            start_s = self.start_date.isoformat()
            today_s = self.today.isoformat()

            data = {}
            data["daily_steps"] = safe_call(self.api.get_daily_steps, start_s, today_s, label="daily_steps")
            data["weekly_steps"] = safe_call(self.api.get_weekly_steps, today_s, 52, label="weekly_steps")
            data["weekly_stress"] = safe_call(self.api.get_weekly_stress, today_s, 52, label="weekly_stress")
            data["weekly_im"] = chunked_date_call(self.api.get_weekly_intensity_minutes,
                                                    self.start_date, self.today, "weekly_im")
            data["floors"] = safe_call(self.api.get_floors, start_s, label="floors")

            for metric in ("distance", "duration", "elevationGain", "calories"):
                result = safe_call(
                    self.api.get_progress_summary_between_dates,
                    start_s, today_s, metric, True,
                    label=f"progress_{metric}",
                )
                data[f"progress_{metric}"] = result

            # Body battery range endpoint returns 400 for any date span;
            # per-day body battery is already captured in daily health sections.
            data["bb_range"] = None
            self.cache.put_section("trends", data)

        if settings.compact:
            compact_data = {k: v for k, v in data.items() if k != "bb_range"}
            self.md.append('Schema: "daily_steps, weekly_steps (52 weeks), weekly_stress (52 weeks), weekly_im (intensity minutes), floors, progress_distance, progress_duration, progress_elevationGain, progress_calories."\n')
            self.md.append(f"{to_json(compact_data)}\n")
        else:
            section(self.md, "Daily Steps", data.get("daily_steps"))
            section(self.md, "Weekly Steps (52 weeks)", data.get("weekly_steps"))
            section(self.md, "Weekly Stress (52 weeks)", data.get("weekly_stress"))
            section(self.md, "Weekly Intensity Minutes", data.get("weekly_im"))
            section(self.md, "Floors", data.get("floors"))
            for metric in ("distance", "duration", "elevationGain", "calories"):
                section(self.md, f"Progress: {metric}", data.get(f"progress_{metric}"))

    # ===================================================================
    # Golf
    # ===================================================================
    def export_golf(self):
        self.md.append("\nGolf\n")

        cached = self._get_fresh_cached_section("golf", "Golf")
        if cached is not None:
            data = cached
        else:
            data = {}
            summary = safe_call(self.api.get_golf_summary, label="golf_summary")
            data["summary"] = summary

            scorecards = []
            if summary and isinstance(summary, list):
                for item in summary:
                    sc_id = item.get("scorecardId") or item.get("id")
                    if not sc_id:
                        continue
                    sc = {"_id": sc_id}
                    sc["detail"] = safe_call(self.api.get_golf_scorecard, sc_id, label=f"golf_sc_{sc_id}")
                    sc["shots"] = safe_call(self.api.get_golf_shot_data, sc_id, label=f"golf_shots_{sc_id}")
                    scorecards.append(sc)

            data["scorecards"] = scorecards
            self.cache.put_section("golf", data)

        if not data.get("summary") and not data.get("scorecards"):
            section_nodata(self.md, "Golf")
        elif settings.compact:
            self.md.append('Schema: "summary: round list. scorecards: array of {_id, detail, shots} per round. Empty if no golf data."\n')
            self.md.append(f"{to_json(data)}\n")
        else:
            section(self.md, "Golf Summary", data.get("summary"))
            for sc in data.get("scorecards", []):
                section(self.md, f"Scorecard {sc.get('_id', '?')}", sc.get("detail"))
                section(self.md, f"Shot Data {sc.get('_id', '?')}", sc.get("shots"))

    # ===================================================================
    # Gear
    # ===================================================================
    def export_gear(self):
        self.md.append("\nGear\n")

        cached = self._get_fresh_cached_section("gear", "Gear")
        if cached is not None:
            data = cached
        else:
            # Need user profile number for gear endpoints
            profile = safe_call(self.api.get_user_profile, label="gear_profile")
            profile_num = None
            if profile and isinstance(profile, dict):
                profile_num = str(profile.get("profileNumber") or profile.get("userProfileNumber", ""))

            data = {}
            if profile_num:
                data["gear_list"] = safe_call(self.api.get_gear, profile_num, label="gear_list")
                data["gear_defaults"] = safe_call(self.api.get_gear_defaults, profile_num, label="gear_defaults")
            else:
                data["gear_list"] = None
                data["gear_defaults"] = None

            gear_details = []
            if data["gear_list"] and isinstance(data["gear_list"], list):
                for item in data["gear_list"]:
                    uuid = item.get("uuid") or item.get("gearUUID")
                    if not uuid:
                        continue
                    g = {"_uuid": uuid}
                    g["stats"] = safe_call(self.api.get_gear_stats, uuid, label=f"gear_stats_{uuid}")
                    gear_details.append(g)

            data["gear_details"] = gear_details
            self.cache.put_section("gear", data)

        if not data.get("gear_list"):
            section_nodata(self.md, "Gear")
        elif settings.compact:
            self.md.append('Schema: "gear_list: equipment items. gear_defaults: per-activity-type defaults. gear_details: array of {_uuid, stats} per item. Empty if no gear."\n')
            self.md.append(f"{to_json(data)}\n")
        else:
            section(self.md, "Gear List", data.get("gear_list"))
            section(self.md, "Gear Defaults", data.get("gear_defaults"))
            for g in data.get("gear_details", []):
                section(self.md, f"Gear Stats: {g.get('_uuid', '?')}", g.get("stats"))

    # ===================================================================
    # Training Plans
    # ===================================================================
    def export_training_plans(self):
        self.md.append("\nTraining Plans\n")

        cached = self._get_fresh_cached_section("training_plans", "Training Plans")
        if cached is not None:
            data = cached
        else:
            data = {}
            plans = safe_call(self.api.get_training_plans, label="training_plans")
            data["plans"] = plans

            plan_details = []
            if plans and isinstance(plans, list):
                for item in plans:
                    pid = item.get("trainingPlanId") or item.get("id")
                    if not pid:
                        continue
                    p = {"_id": pid}
                    # Try standard plan first, then adaptive
                    detail = safe_call(self.api.get_training_plan_by_id, pid, label=f"plan_{pid}")
                    if detail is None:
                        detail = safe_call(self.api.get_adaptive_training_plan_by_id, pid,
                                           label=f"adaptive_plan_{pid}")
                    p["detail"] = detail
                    plan_details.append(p)

            data["plan_details"] = plan_details
            self.cache.put_section("training_plans", data)

        if not data.get("plans"):
            section_nodata(self.md, "Training Plans")
        elif settings.compact:
            self.md.append('Schema: "plans: training plan list. plan_details: array of {_id, detail} per plan. Empty if no plans."\n')
            self.md.append(f"{to_json(data)}\n")
        else:
            section(self.md, "Training Plans", data.get("plans"))
            for p in data.get("plan_details", []):
                section(self.md, f"Plan: {p.get('_id', '?')}", p.get("detail"))

    # ===================================================================
    # Workouts
    # ===================================================================
    def export_workouts(self):
        self.md.append("\nWorkouts\n")

        cached = self._get_fresh_cached_section("workouts", "Workouts")
        if cached is not None:
            data = cached
        else:
            data = {}
            workout_list = safe_call(self.api.get_workouts, 0, 1000, label="workouts")
            data["workout_list"] = workout_list

            workout_details = []
            if workout_list and isinstance(workout_list, list):
                for item in workout_list:
                    wid = item.get("workoutId") or item.get("id")
                    if not wid:
                        continue
                    w = {"_id": wid}
                    w["detail"] = safe_call(self.api.get_workout_by_id, wid, label=f"workout_{wid}")
                    workout_details.append(w)

            data["workout_details"] = workout_details
            self.cache.put_section("workouts", data)

        if not data.get("workout_list"):
            section_nodata(self.md, "Workouts")
        elif settings.compact:
            self.md.append('Schema: "workout_list: saved workout definitions. workout_details: array of {_id, detail} per workout. Empty if no workouts."\n')
            self.md.append(f"{to_json(data)}\n")
        else:
            section(self.md, "Workout List", data.get("workout_list"))
            for w in data.get("workout_details", []):
                section(self.md, f"Workout: {w.get('_id', '?')}", w.get("detail"))

    # ===================================================================
    # Hydration -- per-day, chunked with caching
    # ===================================================================
    def export_hydration(self):
        self.md.append("\nHydration\n")
        log.info(f"  {self.days} days to check")

        # Collect all days, split cached vs uncached
        days_list = []
        cached_results = {}
        uncached_dates = []
        for i in range(self.days):
            d = self.today - timedelta(days=i)
            ds = d.isoformat()
            days_list.append(ds)
            cached = self.cache.get_day(f"hydration_{ds}")
            if cached is not None:
                cached_results[ds] = cached
            else:
                uncached_dates.append(ds)

        log.info(f"  {len(cached_results)} cached, {len(uncached_dates)} to fetch")

        # Fetch uncached days concurrently
        fetched_results = {}
        if uncached_dates:
            api = self.api
            def _fetch_hydration(ds):
                return ds, safe_call(api.get_hydration_data, ds, label=f"hydration_{ds}")

            t_start = time.time()
            done = 0
            with ThreadPoolExecutor(max_workers=4) as pool:
                for ds, result in pool.map(lambda ds: _fetch_hydration(ds), uncached_dates):
                    data = {"hydration": result}
                    self.cache.put_day(f"hydration_{ds}", data)
                    fetched_results[ds] = data
                    done += 1
                    if done % 50 == 0 or done == len(uncached_dates):
                        elapsed = time.time() - t_start
                        remaining = len(uncached_dates) - done
                        eta = (elapsed / done * remaining / 60) if done else 0
                        log.info(f"    {done}/{len(uncached_dates)} fetched | ~{eta:.0f}m remaining")

        # Write markdown in chronological order
        if settings.compact:
            all_days = {}
            for ds in days_list:
                day_data = cached_results.get(ds) or fetched_results.get(ds)
                if day_data and day_data.get("hydration"):
                    all_days[ds] = day_data["hydration"]
            if all_days:
                self.md.append('Schema: "Object keyed by ISO date (YYYY-MM-DD). Each day contains fluid intake data: cups consumed, goal, intake records with timestamps."\n')
                self.md.append(f"{to_json(all_days)}\n")
            else:
                section_nodata(self.md, "Hydration")
        else:
            has_data = False
            for ds in days_list:
                day_data = cached_results.get(ds) or fetched_results.get(ds)
                if day_data and day_data.get("hydration"):
                    has_data = True
                    self.md.append(f"{ds}\n")
                    section(self.md, "Hydration", day_data["hydration"], 4)
            if not has_data:
                section_nodata(self.md, "Hydration")

    # ===================================================================
    # Nutrition -- per-day, concurrent with caching
    # ===================================================================
    def export_nutrition(self):
        self.md.append("\nNutrition\n")
        log.info(f"  {self.days} days to check")

        # Collect all days, split cached vs uncached
        days_list = []
        cached_results = {}
        uncached_dates = []
        for i in range(self.days):
            d = self.today - timedelta(days=i)
            ds = d.isoformat()
            days_list.append(ds)
            cached = self.cache.get_day(f"nutrition_{ds}")
            if cached is not None:
                cached_results[ds] = cached
            else:
                uncached_dates.append(ds)

        log.info(f"  {len(cached_results)} cached, {len(uncached_dates)} to fetch")

        # Fetch uncached days concurrently (3 API calls per day)
        fetched_results = {}
        if uncached_dates:
            api = self.api
            def _fetch_nutrition(ds):
                fl = safe_call(api.get_nutrition_daily_food_log, ds, label=f"food_{ds}")
                ml = safe_call(api.get_nutrition_daily_meals, ds, label=f"meals_{ds}")
                st = safe_call(api.get_nutrition_daily_settings, ds, label=f"nutr_set_{ds}")
                return ds, {"food_log": fl, "meals": ml, "settings": st}

            t_start = time.time()
            done = 0
            with ThreadPoolExecutor(max_workers=4) as pool:
                for ds, data in pool.map(lambda ds: _fetch_nutrition(ds), uncached_dates):
                    self.cache.put_day(f"nutrition_{ds}", data)
                    fetched_results[ds] = data
                    done += 1
                    if done % 50 == 0 or done == len(uncached_dates):
                        elapsed = time.time() - t_start
                        remaining = len(uncached_dates) - done
                        eta = (elapsed / done * remaining / 60) if done else 0
                        log.info(f"    {done}/{len(uncached_dates)} fetched | ~{eta:.0f}m remaining")

        # Write markdown in chronological order
        if settings.compact:
            all_days = {}
            for ds in days_list:
                day_data = cached_results.get(ds) or fetched_results.get(ds)
                if day_data and any(day_data.get(k) for k in ("food_log", "meals", "settings")):
                    merged = {k: v for k, v in day_data.items() if v is not None}
                    all_days[ds] = merged
            if all_days:
                self.md.append('Schema: "Object keyed by ISO date (YYYY-MM-DD). Each day may contain: food_log (daily intake totals), meals (individual meal entries), settings (nutrition goals/targets)."\n')
                self.md.append(f"{to_json(all_days)}\n")
            else:
                section_nodata(self.md, "Nutrition")
        else:
            has_data = False
            for ds in days_list:
                day_data = cached_results.get(ds) or fetched_results.get(ds)
                if day_data and any(day_data.get(k) for k in ("food_log", "meals", "settings")):
                    has_data = True
                    self.md.append(f"{ds}\n")
                    section(self.md, "Food Log", day_data.get("food_log"), 4)
                    section(self.md, "Meals", day_data.get("meals"), 4)
                    section(self.md, "Nutrition Settings", day_data.get("settings"), 4)
            if not has_data:
                section_nodata(self.md, "Nutrition")

    # ===================================================================
    # Women's Health
    # ===================================================================
    def export_womens_health(self):
        self.md.append("\nWomen's Health\n")

        cached = self._get_fresh_cached_section("womens_health", "Women's Health")
        if cached is not None:
            data = cached
        else:
            data = {}
            data["pregnancy"] = safe_call(self.api.get_pregnancy_summary, label="pregnancy")

            # Menstrual calendar endpoint returns 400 on accounts without the
            # feature enabled. Try a single recent-range call first; only chunk
            # the full history if it succeeds.
            probe = safe_call(self.api.get_menstrual_calendar_data,
                              self.today.isoformat(),
                              self.today.isoformat(),
                              label="menstrual_probe")
            if probe is not None:
                data["menstrual_calendar"] = chunked_date_call(
                    self.api.get_menstrual_calendar_data,
                    self.start_date, self.today, "menstrual_cal")
            else:
                data["menstrual_calendar"] = None
            self.cache.put_section("womens_health", data)

        if not any(data.get(k) for k in ("pregnancy", "menstrual_calendar")):
            section_nodata(self.md, "Women's Health")
        elif settings.compact:
            self.md.append('Schema: "pregnancy: pregnancy tracking summary. menstrual_calendar: cycle history. Features require opt-in on Garmin device. Empty if not enabled."\n')
            self.md.append(f"{to_json(data)}\n")
        else:
            section(self.md, "Pregnancy Summary", data.get("pregnancy"))
            section(self.md, "Menstrual Calendar", data.get("menstrual_calendar"))
