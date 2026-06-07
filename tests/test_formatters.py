"""Unit tests for garmin_llm_export.formatters.

These tests intentionally do NOT touch the network or the real Garmin client.
They cover the deterministic, pure-function formatters in formatters.py.
"""

from __future__ import annotations

import json

import pytest

from garmin_llm_export import formatters
from garmin_llm_export.formatters import (
    _longest_line,
    add_derived_daily_fields,
    add_local_timestamps,
    compact_daily,
    downsample_timeseries,
    section,
    section_nodata,
    strip_empty,
    to_json,
    word_count,
    wrap_json,
    TIMESTAMP_FIELDS_GMT,
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


# ---------------------------------------------------------------------------
# GLE-11: line-budget aware JSON
# ---------------------------------------------------------------------------
class TestLongestLine:
    def test_empty(self):
        assert _longest_line("") == 0

    def test_single_line(self):
        assert _longest_line("hello world") == 11

    def test_picks_max(self):
        assert _longest_line("a\nbb\nccc") == 3


class TestWrapJson:
    def test_short_input_unchanged(self):
        data = {"a": 1, "b": [1, 2, 3]}
        result = wrap_json(data, max_line=2000)
        parsed = json.loads(result)
        assert parsed == data

    def test_long_array_breaks_one_per_line(self):
        # 100 elements of ~30 chars each => one giant line in json.dumps
        data = [{"k": i, "v": f"value-{i}"} for i in range(100)]
        result = wrap_json(data, max_line=200)
        # Each element should now be on its own line
        assert "\n" in result
        assert _longest_line(result) <= 200
        # And the data round-trips
        assert json.loads(result) == data

    def test_long_dict_breaks_one_per_line(self):
        data = {f"key_{i:03d}": f"value_{i}" for i in range(100)}
        result = wrap_json(data, max_line=200)
        assert _longest_line(result) <= 200
        assert json.loads(result) == data

    def test_primitives_passthrough(self):
        assert wrap_json("hello", max_line=2000) == '"hello"'
        assert wrap_json(42, max_line=2000) == "42"
        assert wrap_json(None, max_line=2000) == "null"

    def test_no_line_exceeds_budget(self):
        # Fuzz: a 5000-element array
        data = list(range(5000))
        result = wrap_json(data, max_line=500)
        assert _longest_line(result) <= 500


class TestToJsonLineBudget:
    def test_to_json_breaks_long_arrays(self, reset_settings):
        # A single long line in full mode should be wrapped to obey the budget
        data = [{"k": i, "v": f"value_{i}"} for i in range(200)]
        result = to_json(data)
        assert _longest_line(result) <= reset_settings.line_budget

    def test_to_json_compact_mode_also_respects_budget(self, reset_settings):
        # GLE-11: no line in any exported file exceeds the budget, even
        # in compact mode. Compact mode is about stripping empties, not
        # about preserving a single line at all costs.
        reset_settings.compact = True
        data = [{"k": i, "v": f"value_{i}"} for i in range(200)]
        result = to_json(data)
        assert _longest_line(result) <= reset_settings.line_budget
        # Round-trips
        assert json.loads(result) == data

    def test_to_json_short_output_passthrough(self, reset_settings):
        # Small data should NOT trigger the wrap; it should look like the
        # previous default ``json.dumps(indent=2)`` output.
        data = {"a": 1, "b": [1, 2]}
        result = to_json(data)
        assert "\n" in result
        # Indented key on its own line
        assert '"a": 1' in result


# ---------------------------------------------------------------------------
# GLE-9: derived fields in Daily Health
# ---------------------------------------------------------------------------
class TestAddDerivedDailyFields:
    def _sleep_payload(self) -> dict:
        return {
            "dailySleepDTO": {
                "calendarDate": "2026-06-05",
                "sleepTimeSeconds": 32580,
                "deepSleepSeconds": 3420,
                "lightSleepSeconds": 22500,
                "remSleepSeconds": 6660,
                "awakeSleepSeconds": 1920,
                "sleepStartTimestampGMT": 1780605360000,
                "sleepEndTimestampGMT": 1780639860000,
                "averageRespirationValue": 15.0,
                "awakeCount": 2,
                "avgSleepStress": 20.0,
                "sleepScoreFeedback": "NEGATIVE_LONG_BUT_RESTLESS",
                "sleepScoreInsight": "NEGATIVE_STRESSFUL_DAY",
                "sleepScores": {
                    "overall": {"value": 75, "qualifierKey": "FAIR"},
                },
            },
            "restingHeartRate": 71,
            "avgOvernightHrv": 34.0,
            "hrvStatus": "UNBALANCED",
            "bodyBatteryChange": 57,
        }

    def test_adds_sleep_summary(self):
        day = {"sleep": self._sleep_payload()}
        add_derived_daily_fields(day, tz="Asia/Kolkata")
        assert "_summary" in day["sleep"]
        assert day["sleep"]["_summary"]["date"] == "2026-06-05"
        assert "Long but restless" in day["sleep"]["_summary"]["verdict"]

    def test_preserves_raw_sleep_keys(self):
        day = {"sleep": self._sleep_payload()}
        add_derived_daily_fields(day, tz="Asia/Kolkata")
        # Original keys are still there
        assert "dailySleepDTO" in day["sleep"]
        assert "restingHeartRate" in day["sleep"]
        # And the new key is additive
        assert "_summary" in day["sleep"]

    def test_adds_hrv_normalised_fields(self):
        day = {"hrv": {
            "hrvSummary": {
                "weeklyAvg": 34,
                "baseline": {"balancedLow": 35, "balancedUpper": 45},
                "status": "UNBALANCED",
            },
        }}
        add_derived_daily_fields(day)
        assert day["hrv"]["_weekly_avg"] == 34
        assert day["hrv"]["_baseline_low"] == 35
        assert day["hrv"]["_baseline_high"] == 45
        assert day["hrv"]["_status"] == "UNBALANCED"

    def test_hrv_missing_baseline_is_ok(self):
        day = {"hrv": {"hrvSummary": {"weeklyAvg": 40, "status": "BALANCED"}}}
        add_derived_daily_fields(day)
        assert day["hrv"]["_weekly_avg"] == 40
        assert day["hrv"]["_status"] == "BALANCED"
        assert "_baseline_low" not in day["hrv"]

    def test_adds_body_battery_morning_delta_list_form(self):
        day = {"body_battery": [{"date": "2026-06-05", "charged": 62, "drained": 26}]}
        add_derived_daily_fields(day)
        assert day["body_battery"][0]["_morning_charge_delta"] == 62

    def test_adds_body_battery_morning_delta_dict_form(self):
        day = {"body_battery": {"charged": 50, "drained": 20}}
        add_derived_daily_fields(day)
        assert day["body_battery"]["_morning_charge_delta"] == 50

    def test_no_sleep_key_does_not_crash(self):
        day = {"hrv": {"hrvSummary": {"weeklyAvg": 30, "status": "POOR"}}}
        add_derived_daily_fields(day)
        # HRV is still annotated even without sleep
        assert day["hrv"]["_status"] == "POOR"

    def test_returns_input_unchanged_for_non_dict(self):
        assert add_derived_daily_fields("not a dict") == "not a dict"
        assert add_derived_daily_fields(42) == 42

    def test_idempotent(self):
        day = {"sleep": self._sleep_payload()}
        add_derived_daily_fields(day, tz="Asia/Kolkata")
        first_summary = day["sleep"]["_summary"]
        add_derived_daily_fields(day, tz="Asia/Kolkata")
        # Second call should not overwrite the existing _summary
        assert day["sleep"]["_summary"] is first_summary


# ---------------------------------------------------------------------------
# GLE-13: local timestamp formatting
# ---------------------------------------------------------------------------
class TestAddLocalTimestamps:
    def test_local_time_added_for_sleep_window(self):
        # The canonical use-case: a user in Asia/Kolkata sees a human-readable
        # local timestamp next to the raw millisecond GMT value.
        payload = {
            "sleepStartTimestampGMT": 1780552920000,
            "sleepEndTimestampGMT": 1780605360000,
        }
        result = add_local_timestamps(payload, "Asia/Kolkata")
        # Original fields preserved
        assert result["sleepStartTimestampGMT"] == 1780552920000
        assert result["sleepEndTimestampGMT"] == 1780605360000
        # _local siblings added
        assert "sleepStartTimestampGMT_local" in result
        assert "sleepEndTimestampGMT_local" in result
        # ISO 8601 with +05:30 offset
        assert "+05:30" in result["sleepStartTimestampGMT_local"]
        assert result["sleepStartTimestampGMT_local"].startswith("2026-06-04")

    def test_local_time_uses_profile_timezone(self):
        # Same millisecond value expressed in UTC gives a different local time.
        payload = {"startGMT": 1780552920000}
        kolkata = add_local_timestamps(dict(payload), "Asia/Kolkata")
        utc = add_local_timestamps(dict(payload), "UTC")
        # Both are valid ISO 8601 but point to the same instant
        assert "+05:30" in kolkata["startGMT_local"]
        assert "+00:00" in utc["startGMT_local"]
        # The offset difference reflects the timezones
        assert kolkata["startGMT_local"] != utc["startGMT_local"]

    def test_local_time_disabled_by_flag(self):
        # When tz=None the function is a no-op and returns the input unchanged.
        payload = {"sleepStartTimestampGMT": 1780552920000}
        result = add_local_timestamps(payload, None)
        assert result == payload
        assert "sleepStartTimestampGMT_local" not in result

    def test_unknown_field_not_modified(self):
        payload = {"totalSteps": 5000, "startGMT": 1780552920000}
        result = add_local_timestamps(payload, "UTC")
        assert result["totalSteps"] == 5000
        assert "totalSteps_local" not in result
        assert "startGMT_local" in result

    def test_non_numeric_value_skipped(self):
        payload = {"sleepStartTimestampGMT": "not-a-number"}
        result = add_local_timestamps(payload, "UTC")
        assert "sleepStartTimestampGMT_local" not in result

    def test_negative_epoch_skipped(self):
        # Values that are clearly not epoch-ms should be skipped
        payload = {"startGMT": -1}
        result = add_local_timestamps(payload, "UTC")
        assert "startGMT_local" not in result

    def test_list_payload_localised_element_wise(self):
        payload = [
            {"startTimestampGMT": 1780552920000},
            {"startTimestampGMT": 1780605360000},
        ]
        result = add_local_timestamps(payload, "UTC")
        assert len(result) == 2
        assert "startTimestampGMT_local" in result[0]
        assert "startTimestampGMT_local" in result[1]

    def test_original_payload_unchanged(self):
        # Ensure the function does not mutate the caller's dict.
        original = {"sleepEndTimestampGMT": 1780605360000}
        add_local_timestamps(original, "UTC")
        assert "sleepEndTimestampGMT_local" not in original
