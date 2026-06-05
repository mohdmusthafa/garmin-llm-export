"""Unit tests for garmin_llm_export.cache."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from garmin_llm_export.cache import ExportCache, chunked_date_call


# ---------------------------------------------------------------------------
# ExportCache - basic CRUD
# ---------------------------------------------------------------------------
class TestExportCache:
    def test_disabled_cache_returns_none(self, tmp_export_dir: Path):
        c = ExportCache(tmp_export_dir, enabled=False)
        c.put_day("2026-06-05", {"a": 1})
        assert c.get_day("2026-06-05") is None

    def test_day_round_trip(self, cache: ExportCache):
        cache.put_day("2026-06-05", {"sleep": {"x": 1}})
        result = cache.get_day("2026-06-05")
        assert result == {"sleep": {"x": 1}}

    def test_day_miss_returns_none(self, cache: ExportCache):
        assert cache.get_day("9999-01-01") is None

    def test_activity_round_trip(self, cache: ExportCache):
        cache.put_activity(12345, {"summary": {"a": 1}})
        result = cache.get_activity(12345)
        assert result == {"summary": {"a": 1}}

    def test_section_round_trip(self, cache: ExportCache):
        cache.put_section("profile", {"full_name": "Test"})
        result = cache.get_section("profile")
        assert result == {"full_name": "Test"}

    def test_section_age_returns_timedelta(self, cache: ExportCache):
        # Implemented in GLE-3; skipped until then.
        pytest.skip("section_age() is added in GLE-3")

    def test_section_age_missing_returns_none(self, cache: ExportCache):
        # Implemented in GLE-3; skipped until then.
        pytest.skip("section_age() is added in GLE-3")

    def test_overwrite_replaces(self, cache: ExportCache):
        cache.put_section("profile", {"v": 1})
        cache.put_section("profile", {"v": 2})
        assert cache.get_section("profile") == {"v": 2}

    def test_corrupt_json_treated_as_miss(self, cache: ExportCache, tmp_export_dir: Path):
        cache.put_section("profile", {"v": 1})
        # Manually corrupt the file
        path = tmp_export_dir / ".cache" / "sections" / "profile.json"
        path.write_text("not valid json {{{", encoding="utf-8")
        assert cache.get_section("profile") is None

    def test_summary_zero_lookups(self, tmp_export_dir: Path):
        c = ExportCache(tmp_export_dir, enabled=True)
        assert "no lookups" in c.summary()

    def test_summary_after_lookups(self, cache: ExportCache):
        cache.get_day("9999-01-01")  # miss
        cache.put_day("2026-06-05", {"x": 1})
        cache.get_day("2026-06-05")  # hit
        s = cache.summary()
        assert "1 hits" in s
        assert "1 misses" in s
        assert "50%" in s

    def test_disabled_does_not_create_dirs(self, tmp_export_dir: Path):
        ExportCache(tmp_export_dir, enabled=False)
        assert not (tmp_export_dir / ".cache").exists()


# ---------------------------------------------------------------------------
# chunked_date_call
# ---------------------------------------------------------------------------
class TestChunkedDateCall:
    def test_empty_range_returns_none(self):
        # Function that always returns None
        assert chunked_date_call(lambda s, e: None, date(2026, 1, 1), date(2026, 1, 2), "label") is None

    def test_single_chunk(self):
        seen_calls = []

        def fn(s, e):
            seen_calls.append((s, e))
            return [{"x": 1}]

        result = chunked_date_call(fn, date(2026, 1, 1), date(2026, 1, 5), "test", chunk_days=365)
        assert result == [{"x": 1}]
        assert len(seen_calls) == 1

    def test_multiple_chunks_concatenate_lists(self):
        seen_calls = []

        def fn(s, e):
            seen_calls.append((s, e))
            return [{"chunk": s}]

        result = chunked_date_call(
            fn,
            date(2026, 1, 1),
            date(2026, 12, 31),
            "test",
            chunk_days=90,
        )
        # 4 chunks of ~90 days
        assert len(seen_calls) >= 3
        assert len(result) >= 3
        # All items concatenated
        assert all("chunk" in item for item in result)

    def test_none_results_skipped(self):
        def fn(s, e):
            if s == "2026-01-01":
                return [{"x": 1}]
            return None

        result = chunked_date_call(
            fn, date(2026, 1, 1), date(2026, 6, 1), "test", chunk_days=30
        )
        # Only first chunk returned data
        assert result == [{"x": 1}]
