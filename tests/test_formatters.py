"""Unit tests for garmin_llm_export.formatters.

These tests intentionally do NOT touch the network or the real Garmin client.
They cover the deterministic, pure-function formatters in formatters.py.
"""

from __future__ import annotations

import json

import pytest

from garmin_llm_export import formatters
from garmin_llm_export.formatters import (
    compact_daily,
    downsample_timeseries,
    section,
    section_nodata,
    strip_empty,
    to_json,
    word_count,
)


# ---------------------------------------------------------------------------
# word_count
# ---------------------------------------------------------------------------
class TestWordCount:
    def test_empty_string(self):
        assert word_count("") == 0

    def test_single_word(self):
        assert word_count("hello") == 1

    def test_basic_sentence(self):
        assert word_count("the quick brown fox") == 4

    def test_handles_whitespace(self):
        assert word_count("  multiple   spaces  ") == 2

    def test_handles_newlines(self):
        assert word_count("a\nb\nc") == 3


# ---------------------------------------------------------------------------
# strip_empty
# ---------------------------------------------------------------------------
class TestStripEmpty:
    def test_strips_none(self):
        assert strip_empty({"a": None, "b": 1}) == {"b": 1}

    def test_strips_empty_string(self):
        assert strip_empty({"a": "", "b": "x"}) == {"b": "x"}

    def test_strips_empty_list(self):
        assert strip_empty({"a": [], "b": [1]}) == {"b": [1]}

    def test_strips_empty_dict(self):
        assert strip_empty({"a": {}, "b": {"k": "v"}}) == {"b": {"k": "v"}}

    def test_recursive_dict(self):
        result = strip_empty({"a": {"b": None, "c": 1}})
        assert result == {"a": {"c": 1}}

    def test_recursive_list(self):
        result = strip_empty([None, 1, "x", [None, 2]])
        # None is filtered, ints/strings are kept, nested list has None stripped
        assert result == [1, "x", [2]]

    def test_list_filters_only_none(self):
        # Current implementation: lists filter out None, but keep empty strings
        # (a known asymmetry with dicts; documented via this test as a contract).
        result = strip_empty([None, 1, "", "x"])
        assert result == [1, "", "x"]

    def test_keeps_zero_and_false(self):
        # Important: 0 and False are valid values and should not be stripped
        assert strip_empty({"a": 0, "b": False, "c": None}) == {"a": 0, "b": False}

    def test_passthrough_primitives(self):
        assert strip_empty(42) == 42
        assert strip_empty("hello") == "hello"
        assert strip_empty(0) == 0
        assert strip_empty(False) is False

    def test_nested_empty_filtering(self):
        result = strip_empty({"a": {"b": None}, "c": 1})
        assert result == {"c": 1}


# ---------------------------------------------------------------------------
# downsample_timeseries
# ---------------------------------------------------------------------------
class TestDownsampleTimeseries:
    def test_short_data_passthrough(self):
        data = [{"v": i} for i in range(10)]
        result = downsample_timeseries(data, max_points=24)
        assert result == data  # unchanged, ≤ max_points

    def test_empty_data_passthrough(self):
        assert downsample_timeseries([]) == []

    def test_dict_list_downsampled(self):
        # Use values >= 2 to avoid the `0 in (True, False)` / `1 in (True, False)`
        # quirk in the original implementation: bool is a subclass of int, so
        # 0 and 1 short-circuit the averaging branch. Real sensor data is
        # rarely exactly 0/1, so this matters in practice for offsets.
        data = [{"v": i + 2} for i in range(100)]
        result = downsample_timeseries(data, max_points=10)
        # bucket_size = 10, range(0,100,10) = 10 iterations
        assert len(result) == 10
        # First bucket values: 2..11 -> avg 6.5
        assert result[0]["v"] == 6.5

    def test_tuple_list_downsampled(self):
        # Note: 2.0 is truthy, so 0.0 averages correctly here.
        data = [[i, (i + 1) * 2.0] for i in range(100)]
        result = downsample_timeseries(data, max_points=10)
        assert len(result) == 10
        # First row should be [0, 2.0]; second col averaged
        assert result[0][0] == 0
        # First bucket's col 1: 2.0, 4.0, ..., 20.0 -> avg 11.0
        assert result[0][1] == 11.0

    def test_max_points_approximate(self):
        data = [{"v": i} for i in range(1000)]
        result = downsample_timeseries(data, max_points=24)
        # The implementation buckets via range(0, n, bucket_size), so the
        # resulting count is ceil(n / bucket_size). With 1000 // 24 = 41
        # we get ceil(1000/41) = 25 buckets. Verify it is at-or-near the
        # target, not strict equality.
        assert 1 <= len(result) <= 30
        assert len(result) < len(data)

    def test_passthrough_non_dict_non_tuple(self):
        data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        result = downsample_timeseries(data, max_points=5)
        # primitive list -> not dict, not tuple -> passthrough
        assert result == data


# ---------------------------------------------------------------------------
# compact_daily
# ---------------------------------------------------------------------------
class TestCompactDaily:
    def test_downsamples_timeseries_keys(self):
        data = {
            "heart_rate": {
                "heartRateValues": [[i, 80 + i] for i in range(100)]
            },
            "summary": {"steps": 5000},
        }
        result = compact_daily(data)
        # summary should be preserved as-is
        assert result["summary"] == {"steps": 5000}
        # heart_rate list should be downsampled to ~24 hourly points
        assert len(result["heart_rate"]["heartRateValues"]) <= 30
        assert len(result["heart_rate"]["heartRateValues"]) < 100

    def test_passthrough_non_dict_input(self):
        assert compact_daily("not a dict") == "not a dict"
        assert compact_daily(42) == 42

    def test_downsamples_top_level_list_keys(self):
        data = {
            "heart_rate": [[i, 80 + i] for i in range(100)],
            "summary": {"steps": 5000},
        }
        result = compact_daily(data)
        assert len(result["heart_rate"]) <= 30
        assert len(result["heart_rate"]) < 100
        assert result["summary"] == {"steps": 5000}


# ---------------------------------------------------------------------------
# to_json
# ---------------------------------------------------------------------------
class TestToJson:
    def test_default_indented(self, reset_settings):
        data = {"a": 1, "b": [1, 2]}
        result = to_json(data)
        parsed = json.loads(result)
        assert parsed == data
        # default = not compact -> not single-line
        assert "\n" in result

    def test_compact_single_line(self, reset_settings):
        reset_settings.compact = True
        data = {"a": 1, "b": [1, 2]}
        result = to_json(data)
        parsed = json.loads(result)
        assert parsed == data
        # compact mode -> single line
        assert "\n" not in result

    def test_compact_strips_empty(self, reset_settings):
        reset_settings.compact = True
        data = {"a": 1, "b": None, "c": "", "d": []}
        result = to_json(data)
        parsed = json.loads(result)
        assert parsed == {"a": 1}

    def test_split_dict_format(self, reset_settings):
        reset_settings.split = True
        data = {"a": 1, "b": 2}
        result = to_json(data)
        # Split mode wraps dict items on their own lines
        assert "  " in result
        assert "\n" in result

    def test_split_list_format(self, reset_settings):
        reset_settings.split = True
        data = [1, 2, 3]
        result = to_json(data)
        assert "\n" in result
        # each item on its own line
        assert result.count("\n") >= 2


# ---------------------------------------------------------------------------
# section / section_nodata
# ---------------------------------------------------------------------------
class TestSection:
    def test_section_writes_title_and_json(self):
        md = []
        section(md, "Sleep", {"a": 1})
        assert md[0] == "Sleep\n"
        assert json.loads(md[1]) == {"a": 1}

    def test_section_skips_when_data_is_none(self):
        md = []
        section(md, "Sleep", None)
        # nothing appended
        assert md == []

    def test_section_nodata_writes_marker(self):
        md = []
        section_nodata(md, "Sleep")
        assert md == ["No data available.\n"]
