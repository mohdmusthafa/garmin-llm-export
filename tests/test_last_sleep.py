"""Unit tests for garmin_llm_export.last_sleep (GLE-7)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from garmin_llm_export.last_sleep import (
    _format_duration,
    _format_score,
    write_last_sleep_file,
)
from garmin_llm_export.summaries import build_sleep_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _canned_payload(date_str: str) -> dict:
    """Same shape as the canned data in conftest._canned_sleep_for_day()."""
    return {
        "dailySleepDTO": {
            "calendarDate": date_str,
            "sleepTimeSeconds": 32580,
            "napTimeSeconds": 0,
            "sleepStartTimestampGMT": 1780605360000,
            "sleepEndTimestampGMT": 1780639860000,
            "deepSleepSeconds": 3420,
            "lightSleepSeconds": 22500,
            "remSleepSeconds": 6660,
            "awakeSleepSeconds": 1920,
            "averageRespirationValue": 15.0,
            "lowestRespirationValue": 11.0,
            "highestRespirationValue": 21.0,
            "awakeCount": 2,
            "avgSleepStress": 20.0,
            "sleepScoreFeedback": "NEGATIVE_LONG_BUT_RESTLESS",
            "sleepScoreInsight": "NEGATIVE_STRESSFUL_DAY",
            "sleepScores": {
                "totalDuration": {"qualifierKey": "EXCELLENT"},
                "stress": {"qualifierKey": "FAIR"},
                "awakeCount": {"qualifierKey": "FAIR"},
                "overall": {"value": 75, "qualifierKey": "FAIR"},
                "remPercentage": {"value": 20, "qualifierKey": "FAIR"},
                "restlessness": {"qualifierKey": "POOR"},
                "lightPercentage": {"value": 69, "qualifierKey": "FAIR"},
                "deepPercentage": {"value": 10, "qualifierKey": "FAIR"},
            },
        },
        "restingHeartRate": 71,
        "avgOvernightHrv": 34.0,
        "hrvStatus": "UNBALANCED",
        "bodyBatteryChange": 57,
        "restlessMomentsCount": 20,
    }


class _StubCache:
    """Minimal cache stub with a get_day() method.

    Mirrors the real cache's shape: a per-day dict with multiple
    endpoint keys (summary, sleep, hr, ...). Tests build a day by
    wrapping the sleep payload in ``{"sleep": <payload>}``.
    """

    def __init__(self, days: dict[str, dict]):
        self._days = days

    def get_day(self, ds: str):
        return self._days.get(ds)


def _day_with_sleep(date_str: str) -> dict:
    """A per-day cache entry that holds the canned sleep payload under ``sleep``."""
    return {"sleep": _canned_payload(date_str)}


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------
class TestFormatDuration:
    def test_zero(self):
        assert _format_duration(0) == "0m"

    def test_minutes(self):
        assert _format_duration(60) == "1m"
        assert _format_duration(45 * 60) == "45m"

    def test_hours_only(self):
        assert _format_duration(3600) == "1h"

    def test_hours_and_minutes(self):
        assert _format_duration(32580) == "9h 3m"  # canonical example
        assert _format_duration(3661) == "1h 1m"

    def test_none_or_negative(self):
        assert _format_duration(None) == "?"
        assert _format_duration(-5) == "?"


# ---------------------------------------------------------------------------
# _format_score
# ---------------------------------------------------------------------------
class TestFormatScore:
    def test_value_only(self):
        assert _format_score(75, None) == "75"

    def test_value_with_qualifier(self):
        assert _format_score(75, "fair") == "75 (fair)"

    def test_none_value(self):
        assert _format_score(None, "fair") == "?"


# ---------------------------------------------------------------------------
# write_last_sleep_file
# ---------------------------------------------------------------------------
class TestWriteLastSleepFile:
    def test_writes_file_under_10_kb(self, tmp_path: Path):
        today = datetime.now().date().isoformat()
        cache = _StubCache({today: _day_with_sleep(today)})
        path = write_last_sleep_file(
            cache, tmp_path, now=datetime(2026, 6, 7, 19, 50, 0)
        )
        assert path is not None
        size_kb = path.stat().st_size / 1024
        assert size_kb < 10, f"File is {size_kb:.1f} KB, expected < 10"

    def test_returns_none_when_no_data(self, tmp_path: Path):
        cache = _StubCache({})
        path = write_last_sleep_file(cache, tmp_path)
        assert path is None

    def test_falls_back_to_earlier_day(self, tmp_path: Path):
        # Cache has data for "today" but the summary can't be built (no DTO)
        # -- should fall back to the next day with a buildable summary.
        from datetime import date, timedelta
        today = date.today()
        d1 = today.isoformat()
        d2 = (today - timedelta(days=1)).isoformat()
        cache = _StubCache({
            d1: {"sleep": {}},  # empty -> no summary
            d2: _day_with_sleep(d2),  # good
        })
        path = write_last_sleep_file(cache, tmp_path)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        assert f"# Last Night's Sleep -- {d2}" in content

    def test_filename_uses_now(self, tmp_path: Path):
        today = datetime.now().date().isoformat()
        cache = _StubCache({today: _day_with_sleep(today)})
        path = write_last_sleep_file(
            cache, tmp_path, now=datetime(2026, 6, 7, 19, 50, 0)
        )
        assert path is not None
        assert "garmin_last_sleep_2026-06-07_195000.txt" == path.name

    def test_contains_verdict_and_subscores(self, tmp_path: Path):
        today = datetime.now().date().isoformat()
        cache = _StubCache({today: _day_with_sleep(today)})
        path = write_last_sleep_file(
            cache, tmp_path, now=datetime(2026, 6, 7, 19, 50, 0)
        )
        content = path.read_text(encoding="utf-8")
        assert "Long but restless" in content
        assert "Stressful day" in content
        assert "Sub-scores" in content
        assert "Raw data (dailySleepDTO)" in content
        # The overall score from the canned payload is 75, qualifier "fair"
        assert "75" in content
        assert "fair" in content

    def test_localised_bedtime_in_ist(self, tmp_path: Path):
        today = datetime.now().date().isoformat()
        cache = _StubCache({today: _day_with_sleep(today)})
        path = write_last_sleep_file(
            cache, tmp_path, tz="Asia/Kolkata",
            now=datetime(2026, 6, 7, 19, 50, 0),
        )
        content = path.read_text(encoding="utf-8")
        assert "+05:30" in content
