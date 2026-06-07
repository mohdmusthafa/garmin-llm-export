"""Unit tests for garmin_llm_export.summaries (GLE-6)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from garmin_llm_export.summaries import build_sleep_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _payload_with(dto_overrides: dict | None = None,
                  top_overrides: dict | None = None) -> dict:
    """Build a minimal but realistic sleep payload (the 2026-06-05 record)."""
    dto = {
        "calendarDate": "2026-06-05",
        "sleepTimeSeconds": 32580,
        "napTimeSeconds": 0,
        "sleepStartTimestampGMT": 1780605360000,
        "sleepEndTimestampGMT": 1780639860000,
        "sleepStartTimestampLocal": 1780625160000,
        "sleepEndTimestampLocal": 1780659660000,
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
    }
    if dto_overrides:
        dto.update(dto_overrides)
    return {
        "dailySleepDTO": dto,
        "restingHeartRate": 71,
        "avgOvernightHrv": 34.0,
        "hrvStatus": "UNBALANCED",
        "bodyBatteryChange": 57,
        "restlessMomentsCount": 20,
        **(top_overrides or {}),
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
class TestBuildSleepSummary:
    def test_returns_dict_for_canned_payload(self):
        summary = build_sleep_summary(_payload_with())
        assert isinstance(summary, dict)

    def test_summary_includes_date(self):
        summary = build_sleep_summary(_payload_with())
        assert summary["date"] == "2026-06-05"

    def test_summary_includes_verdict(self):
        summary = build_sleep_summary(_payload_with())
        assert "Long but restless" in summary["verdict"]
        assert "Stressful day" in summary["verdict"]

    def test_summary_includes_local_bedtime_and_wake(self):
        summary = build_sleep_summary(_payload_with(), tz="Asia/Kolkata")
        assert summary["bedtime_local"] is not None
        assert summary["wake_local"] is not None
        # IST is UTC+5:30
        assert summary["bedtime_local"].endswith("+05:30")
        assert summary["wake_local"].endswith("+05:30")

    def test_summary_includes_utc_when_no_tz(self):
        summary = build_sleep_summary(_payload_with(), tz=None)
        assert summary["bedtime_local"].endswith("+00:00")

    def test_summary_includes_vitals(self):
        summary = build_sleep_summary(_payload_with())
        v = summary["vitals"]
        assert v["resting_hr"] == 71
        assert v["avg_sleep_stress"] == 20.0
        assert v["hrv_avg"] == 34.0
        assert v["hrv_status"] == "UNBALANCED"
        assert v["awake_count"] == 2
        assert v["body_battery_change"] == 57
        assert v["respiration"]["avg"] == 15.0
        assert v["respiration"]["min"] == 11.0
        assert v["respiration"]["max"] == 21.0

    def test_summary_score_block_contains_overall(self):
        summary = build_sleep_summary(_payload_with())
        assert summary["score"]["overall"] == 75
        assert summary["score"]["qualifier"] == "fair"
        names = {s["name"] for s in summary["score"]["subscores"]}
        assert "overall" in names
        assert "remPercentage" in names
        assert "restlessness" in names


# ---------------------------------------------------------------------------
# Stage percentages and efficiency
# ---------------------------------------------------------------------------
class TestStagesAndEfficiency:
    def test_efficiency_uses_seconds_math(self):
        # 9h 3m asleep / 9h 35m in bed = 32580 / 34500 = 94.4%
        summary = build_sleep_summary(_payload_with())
        assert summary["time_in_bed_seconds"] == 34500
        assert summary["asleep_seconds"] == 32580
        assert summary["sleep_efficiency"] == 94.4

    def test_stage_percentages(self):
        summary = build_sleep_summary(_payload_with())
        # 3420 + 22500 + 6660 + 1920 = 34500 (time-in-bed)
        # deep = 3420/32580 = 10.5%
        # light = 22500/32580 = 69.1%
        # rem = 6660/32580 = 20.4%
        # awake = 1920/32580 = 5.9%
        s = summary["stages"]
        assert s["deep_pct"] == 10.5
        assert s["light_pct"] == 69.1
        assert s["rem_pct"] == 20.4
        assert s["awake_pct"] == 5.9

    def test_efficiency_none_when_window_missing(self):
        summary = build_sleep_summary(
            _payload_with(dto_overrides={
                "sleepStartTimestampGMT": None,
                "sleepEndTimestampGMT": None,
            })
        )
        assert summary["time_in_bed_seconds"] is None
        assert summary["sleep_efficiency"] is None

    def test_efficiency_unchanged_for_twenty_two_minutes(self):
        # 1320s / 1440s = 91.7%
        summary = build_sleep_summary(
            _payload_with(dto_overrides={
                "sleepTimeSeconds": 1320,
                "sleepStartTimestampGMT": 1_700_000_000_000,
                "sleepEndTimestampGMT": 1_700_000_000_000 + 1_440_000,
            })
        )
        assert summary["sleep_efficiency"] == 91.7


# ---------------------------------------------------------------------------
# Verdict phrase mapping
# ---------------------------------------------------------------------------
class TestVerdictPhrases:
    @pytest.mark.parametrize("feedback,insight,expected_substrings", [
        ("NEGATIVE_LONG_BUT_RESTLESS", "NEGATIVE_STRESSFUL_DAY",
         ["Long but restless", "Stressful day"]),
        ("NEGATIVE_LONG_BUT_POOR_QUALITY", "NEGATIVE_WOKE_UP_OFTEN",
         ["Long but poor quality", "Woke up often"]),
        ("POSITIVE_RECOVERY", None, ["Restorative"]),
        (None, "POSITIVE_PERFECT_SCORE", ["Perfect score"]),
        ("MEH_BALANCED", None, ["Balanced"]),
        ("SOMETHING_UNKNOWN", "ALSO_UNKNOWN",
         ["something unknown", "also unknown"]),
    ])
    def test_known_and_unknown_keys(self, feedback, insight, expected_substrings):
        # Use the private helper directly so the test isn't tied to the DTO shape
        from garmin_llm_export.summaries import _verdict
        verdict = _verdict(feedback, insight)
        for sub in expected_substrings:
            assert sub in verdict

    def test_no_keys_returns_sentinel(self):
        from garmin_llm_export.summaries import _verdict
        assert _verdict(None, None) == "No verdict available."


# ---------------------------------------------------------------------------
# HRV status mapping (and other passthroughs)
# ---------------------------------------------------------------------------
class TestPassthroughs:
    def test_hrv_status_passes_through(self):
        for status in ("UNBALANCED", "BALANCED", "POOR"):
            summary = build_sleep_summary(
                _payload_with(top_overrides={"hrvStatus": status})
            )
            assert summary["vitals"]["hrv_status"] == status

    def test_qualifiers_lowercased(self):
        summary = build_sleep_summary(_payload_with())
        for sub in summary["score"]["subscores"]:
            q = sub.get("qualifier")
            if q is not None:
                assert q == q.lower()


# ---------------------------------------------------------------------------
# Edge cases: missing data
# ---------------------------------------------------------------------------
class TestNoData:
    def test_none_payload_returns_none(self):
        assert build_sleep_summary(None) is None

    def test_empty_dict_returns_none(self):
        assert build_sleep_summary({}) is None

    def test_missing_dto_returns_none(self):
        assert build_sleep_summary({"restingHeartRate": 71}) is None

    def test_dto_with_only_minimum_fields(self):
        # Only the fields required for the headline numbers
        summary = build_sleep_summary({
            "dailySleepDTO": {
                "calendarDate": "2026-01-01",
                "sleepTimeSeconds": 28800,
                "deepSleepSeconds": 3600,
                "lightSleepSeconds": 18000,
                "remSleepSeconds": 7200,
                "awakeSleepSeconds": 0,
                "sleepScoreFeedback": None,
                "sleepScoreInsight": None,
                "sleepScores": {},
            }
        })
        assert summary["date"] == "2026-01-01"
        assert summary["asleep_seconds"] == 28800
        assert summary["stages"]["deep_pct"] == 12.5
        assert summary["vitals"]["resting_hr"] is None
        assert summary["verdict"] == "No verdict available."

    def test_invalid_timestamp_returns_none_local(self):
        summary = build_sleep_summary(
            _payload_with(dto_overrides={"sleepStartTimestampGMT": "not-a-number"})
        )
        assert summary["bedtime_local"] is None

    def test_zero_total_seconds_yields_no_percentages(self):
        summary = build_sleep_summary(
            _payload_with(dto_overrides={"sleepTimeSeconds": 0})
        )
        # stages have raw seconds, but no percentages when total is 0
        assert "deep_pct" not in summary["stages"]
        assert summary["sleep_efficiency"] is None


# ---------------------------------------------------------------------------
# GLE-10: get_latest_sleep_summary
# ---------------------------------------------------------------------------
class TestGetLatestSleepSummary:
    def _api(self, sleep_for_dates, profile_tz="Asia/Kolkata"):
        """A tiny stub of the Garmin API that returns canned sleep data."""

        class _Stub:
            def get_userprofile_settings(inner_self):
                return {"timeZone": profile_tz} if profile_tz else {}

            def get_sleep_data(inner_self, ds):
                return sleep_for_dates.get(ds)

        return _Stub()

    def test_returns_today_summary(self):
        from garmin_llm_export.summaries import get_latest_sleep_summary

        api = self._api({
            "2026-06-07": _payload_with(dto_overrides={"calendarDate": "2026-06-07"}),
        })
        summary = get_latest_sleep_summary(api)
        assert summary is not None
        assert summary["date"] == "2026-06-07"

    def test_falls_back_to_yesterday(self):
        from garmin_llm_export.summaries import get_latest_sleep_summary

        api = self._api({
            "2026-06-06": _payload_with(dto_overrides={"calendarDate": "2026-06-06"}),
        })
        summary = get_latest_sleep_summary(api)
        assert summary is not None
        assert summary["date"] == "2026-06-06"

    def test_returns_none_when_no_data(self):
        from garmin_llm_export.summaries import get_latest_sleep_summary

        api = self._api({})  # no sleep data for either day
        summary = get_latest_sleep_summary(api)
        assert summary is None

    def test_uses_explicit_tz_over_profile(self):
        from garmin_llm_export.summaries import get_latest_sleep_summary

        api = self._api({
            "2026-06-07": _payload_with(dto_overrides={"calendarDate": "2026-06-07"}),
        }, profile_tz="America/New_York")
        summary = get_latest_sleep_summary(api, tz="Asia/Kolkata")
        # The bedtime should be in IST (+05:30), not EST (-05:00)
        assert summary["bedtime_local"].endswith("+05:30")

    def test_detects_tz_from_profile(self):
        from garmin_llm_export.summaries import get_latest_sleep_summary

        api = self._api({
            "2026-06-07": _payload_with(dto_overrides={"calendarDate": "2026-06-07"}),
        }, profile_tz="Asia/Kolkata")
        summary = get_latest_sleep_summary(api)
        assert summary["bedtime_local"].endswith("+05:30")

    def test_falls_back_to_utc_when_profile_missing(self):
        from garmin_llm_export.summaries import get_latest_sleep_summary

        class _NoProfile:
            def get_userprofile_settings(inner_self):
                return None

            def get_sleep_data(inner_self, ds):
                return _payload_with(dto_overrides={"calendarDate": ds})

        summary = get_latest_sleep_summary(_NoProfile())
        assert summary is not None
        assert summary["bedtime_local"].endswith("+00:00")
