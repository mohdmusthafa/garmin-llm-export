"""Shared pytest fixtures for the garmin-llm-export test suite.

Goals:
- Deterministic canned data for at least 2 days of sleep + 1 activity.
- Mocked Garmin API that records every call (so tests can assert call counts).
- A fresh tmp export/cache directory per test (no cross-test contamination).
- An exporter fixture with sensible defaults.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from garmin_llm_export.cache import ExportCache
from garmin_llm_export.config import settings as global_settings


# ---------------------------------------------------------------------------
# Logging: silence noisy loggers during tests, but keep errors visible
# ---------------------------------------------------------------------------
logging.getLogger("garmin_llm_export").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TZ = "Asia/Kolkata"

DAY0 = date(2026, 6, 4)   # wake date for sleep that started evening of June 3
DAY1 = date(2026, 6, 5)   # wake date for sleep that started evening of June 4

PROFILE_NUMBER = 144263752
DEVICE_ID = 3617627775
USER_NAME = "Test User"


# ---------------------------------------------------------------------------
# Canned sleep data (the 2026-06-05 record from a real Garmin account)
# ---------------------------------------------------------------------------
def _realistic_daily_sleep_dto(calendar_date: str) -> Dict[str, Any]:
    """Return a realistic dailySleepDTO payload keyed by wake date."""
    if calendar_date == "2026-06-05":
        return {
            "id": 1780605360000,
            "userProfilePK": PROFILE_NUMBER,
            "calendarDate": calendar_date,
            "sleepTimeSeconds": 32580,
            "napTimeSeconds": 0,
            "sleepWindowConfirmed": True,
            "sleepWindowConfirmationType": "enhanced_confirmed_final",
            "sleepStartTimestampGMT": 1780605360000,
            "sleepEndTimestampGMT": 1780639860000,
            "sleepStartTimestampLocal": 1780625160000,
            "sleepEndTimestampLocal": 1780659660000,
            "unmeasurableSleepSeconds": 0,
            "deepSleepSeconds": 3420,
            "lightSleepSeconds": 22500,
            "remSleepSeconds": 6660,
            "awakeSleepSeconds": 1920,
            "deviceRemCapable": True,
            "retro": False,
            "sleepFromDevice": True,
            "averageRespirationValue": 15.0,
            "lowestRespirationValue": 11.0,
            "highestRespirationValue": 21.0,
            "awakeCount": 2,
            "avgSleepStress": 20.0,
            "ageGroup": "ADULT",
            "sleepScoreFeedback": "NEGATIVE_LONG_BUT_RESTLESS",
            "sleepScoreInsight": "NEGATIVE_STRESSFUL_DAY",
            "sleepScorePersonalizedInsight": "NOT_AVAILABLE",
            "sleepScores": {
                "totalDuration": {
                    "qualifierKey": "EXCELLENT",
                    "optimalStart": 28800.0,
                    "optimalEnd": 28800.0,
                },
                "stress": {"qualifierKey": "FAIR", "optimalStart": 0.0, "optimalEnd": 15.0},
                "awakeCount": {"qualifierKey": "FAIR", "optimalStart": 0.0, "optimalEnd": 1.0},
                "overall": {"value": 75, "qualifierKey": "FAIR"},
                "remPercentage": {
                    "value": 20,
                    "qualifierKey": "FAIR",
                    "optimalStart": 21.0,
                    "optimalEnd": 31.0,
                },
                "restlessness": {
                    "qualifierKey": "POOR",
                    "optimalStart": 0.0,
                    "optimalEnd": 5.0,
                },
                "lightPercentage": {
                    "value": 69,
                    "qualifierKey": "FAIR",
                    "optimalStart": 30.0,
                    "optimalEnd": 64.0,
                },
                "deepPercentage": {
                    "value": 10,
                    "qualifierKey": "FAIR",
                    "optimalStart": 16.0,
                    "optimalEnd": 33.0,
                },
            },
            "sleepVersion": 2,
        }
    if calendar_date == "2026-06-04":
        return {
            "id": 1780520160000,
            "userProfilePK": PROFILE_NUMBER,
            "calendarDate": calendar_date,
            "sleepTimeSeconds": 28380,
            "napTimeSeconds": 0,
            "sleepWindowConfirmed": True,
            "sleepWindowConfirmationType": "enhanced_confirmed_final",
            "sleepStartTimestampGMT": 1780520160000,
            "sleepEndTimestampGMT": 1780552920000,
            "sleepStartTimestampLocal": 1780539960000,
            "sleepEndTimestampLocal": 1780572720000,
            "unmeasurableSleepSeconds": 0,
            "deepSleepSeconds": 3000,
            "lightSleepSeconds": 23760,
            "remSleepSeconds": 1620,
            "awakeSleepSeconds": 4380,
            "deviceRemCapable": True,
            "retro": False,
            "sleepFromDevice": True,
            "averageRespirationValue": 16.0,
            "lowestRespirationValue": 9.0,
            "highestRespirationValue": 20.0,
            "awakeCount": 4,
            "avgSleepStress": 26.0,
            "ageGroup": "ADULT",
            "sleepScoreFeedback": "NEGATIVE_LONG_BUT_POOR_QUALITY",
            "sleepScoreInsight": "NEGATIVE_STRESSFUL_DAY",
            "sleepScorePersonalizedInsight": "NOT_AVAILABLE",
            "sleepScores": {
                "totalDuration": {
                    "qualifierKey": "EXCELLENT",
                    "optimalStart": 28800.0,
                    "optimalEnd": 28800.0,
                },
                "stress": {"qualifierKey": "POOR", "optimalStart": 0.0, "optimalEnd": 15.0},
                "awakeCount": {"qualifierKey": "POOR", "optimalStart": 0.0, "optimalEnd": 1.0},
                "overall": {"value": 50, "qualifierKey": "POOR"},
                "remPercentage": {
                    "value": 6,
                    "qualifierKey": "POOR",
                    "optimalStart": 21.0,
                    "optimalEnd": 31.0,
                },
                "restlessness": {
                    "qualifierKey": "POOR",
                    "optimalStart": 0.0,
                    "optimalEnd": 5.0,
                },
                "lightPercentage": {
                    "value": 84,
                    "qualifierKey": "POOR",
                    "optimalStart": 30.0,
                    "optimalEnd": 64.0,
                },
                "deepPercentage": {
                    "value": 11,
                    "qualifierKey": "FAIR",
                    "optimalStart": 16.0,
                    "optimalEnd": 33.0,
                },
            },
            "sleepVersion": 2,
        }
    raise ValueError(f"No canned data for {calendar_date}")


def _canned_sleep_for_day(calendar_date: str) -> Dict[str, Any]:
    """Sleep payload for a given wake date (matches Garmin's get_sleep_data shape)."""
    dto = _realistic_daily_sleep_dto(calendar_date)
    return {
        "dailySleepDTO": dto,
        "sleepMovement": [],
        "remSleepData": True,
        "sleepLevels": [],
        "sleepRestlessMoments": [],
        "restlessMomentsCount": dto.get("awakeCount", 0) * 10,
        "wellnessEpochRespirationDataDTOList": [],
        "wellnessEpochRespirationAveragesList": [],
        "respirationVersion": 100,
        "sleepHeartRate": [],
        "sleepStress": [],
        "sleepBodyBattery": [],
        "skinTempDataExists": False,
        "hrvData": [],
        "avgOvernightHrv": 34.0,
        "hrvStatus": "UNBALANCED",
        "bodyBatteryChange": 57,
        "restingHeartRate": 71,
    }


def _canned_user_profile() -> Dict[str, Any]:
    return {
        "id": PROFILE_NUMBER,
        "userData": {
            "gender": "MALE",
            "weight": 70500.0,
            "height": 175.0,
            "timeFormat": "time_twelve_hr",
            "birthDate": "2002-09-10",
            "measurementSystem": "metric",
        },
        "connectDate": "2022-01-01",
        "sourceType": "Garmin Connect Website",
    }


def _canned_profile_settings() -> Dict[str, Any]:
    return {
        "displayName": USER_NAME,
        "preferredLocale": "en_IN",
        "measurementSystem": "metric",
        "firstDayOfWeek": {"dayId": 2, "dayName": "sunday", "sortOrder": 2},
        "numberFormat": {"decimalSeparator": ".", "thousandsSeparator": ","},
        "timeFormat": "time_twelve_hr",
        "dateFormat": "date_format_dmy",
        "powerFormat": {"formatId": 30, "formatKey": "watt"},
        "heartRateFormat": {"formatId": 21, "formatKey": "bpm"},
        "timeZone": TZ,
    }


def _canned_daily_summary(calendar_date: str) -> Dict[str, Any]:
    return {
        "userProfileId": PROFILE_NUMBER,
        "calendarDate": calendar_date,
        "totalKilocalories": 2229.0,
        "activeKilocalories": 379.0,
        "bmrKilocalories": 1850.0,
        "totalSteps": 3324,
        "totalDistanceMeters": 2594,
        "dailyStepGoal": 6620,
        "restingHeartRate": 76,
        "stressQualifier": "STRESSFUL",
        "bodyBatteryAtWakeTime": 55,
        "bodyBatteryHighestValue": 55,
        "bodyBatteryLowestValue": 5,
    }


def _canned_hrv(calendar_date: str) -> Dict[str, Any]:
    return {
        "userProfilePk": PROFILE_NUMBER,
        "hrvSummary": {
            "calendarDate": calendar_date,
            "weeklyAvg": 34,
            "lastNightAvg": 34,
            "lastNight5MinHigh": 48,
            "baseline": {"lowUpper": 31, "balancedLow": 35, "balancedUpper": 45},
            "status": "UNBALANCED",
            "feedbackPhrase": "HRV_UNBALANCED_12",
        },
        "hrvReadings": [],
    }


# ---------------------------------------------------------------------------
# Mock API
# ---------------------------------------------------------------------------
class MockGarminApi:
    """A recording mock of the Garmin API used by the exporter.

    - Every method records its call (label, args, kwargs).
    - Returns deterministic canned data for 2 days.
    - Exposes `call_count` and `calls_by_label` for assertions.
    """

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self._dates_with_data = {DAY0.isoformat(), DAY1.isoformat()}

    # ---- introspection helpers ----
    @property
    def call_count(self) -> int:
        return len(self.calls)

    def calls_by_label(self, label: str) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["label"] == label]

    def call_count_by_label(self, label: str) -> int:
        return len(self.calls_by_label(label))

    def _record(self, label: str, args, kwargs) -> None:
        self.calls.append(
            {"label": label, "args": list(args), "kwargs": dict(kwargs)}
        )

    # ---- profile section ----
    def get_full_name(self):
        self._record("full_name", (), {})
        return USER_NAME

    def get_unit_system(self):
        self._record("unit_system", (), {})
        return {"measurementSystem": "metric"}

    def get_user_profile(self):
        self._record("user_profile", (), {})
        return _canned_user_profile()

    def get_userprofile_settings(self):
        self._record("profile_settings", (), {})
        return _canned_profile_settings()

    def get_devices(self):
        self._record("devices", (), {})
        return [
            {
                "deviceId": DEVICE_ID,
                "deviceName": "Test Watch",
                "deviceType": "fitness_tracker",
            }
        ]

    def get_primary_training_device(self):
        self._record("primary_device", (), {})
        return {
            "deviceId": DEVICE_ID,
            "deviceName": "Test Watch",
        }

    def get_device_alarms(self):
        self._record("device_alarms", (), {})
        return []

    def get_device_last_used(self):
        self._record("last_used_device", (), {})
        return {"deviceId": DEVICE_ID, "deviceName": "Test Watch"}

    def get_activity_types(self):
        self._record("activity_types", (), {})
        return [{"typeKey": "running", "typeId": 1}]

    # ---- daily-health endpoints ----
    def get_user_summary(self, date_str):
        self._record("summary", (date_str,), {})
        if date_str in self._dates_with_data:
            return _canned_daily_summary(date_str)
        return None

    def get_heart_rates(self, date_str):
        self._record("hr", (date_str,), {})
        return {
            "calendarDate": date_str,
            "heartRateValues": [[1780511400000, 88.2], [1780519080000, 86.8]],
        }

    def get_rhr_day(self, date_str):
        self._record("rhr", (date_str,), {})
        return {
            "statisticsStartDate": date_str,
            "statisticsEndDate": date_str,
            "allMetrics": {
                "metricsMap": {
                    "WELLNESS_RESTING_HEART_RATE": [{"value": 76.0, "calendarDate": date_str}]
                }
            },
        }

    def get_sleep_data(self, date_str):
        self._record("sleep", (date_str,), {})
        if date_str in self._dates_with_data:
            return _canned_sleep_for_day(date_str)
        return None

    def get_all_day_stress(self, date_str):
        self._record("stress", (date_str,), {})
        return {"calendarDate": date_str, "avgStressLevel": 43, "maxStressLevel": 96}

    def get_spo2_data(self, date_str):
        self._record("spo2", (date_str,), {})
        return {
            "calendarDate": date_str,
            "sleepStartTimestampLocal": "2026-06-05T02:06:00.0",
            "sleepEndTimestampLocal": "2026-06-05T11:41:00.0",
        }

    def get_respiration_data(self, date_str):
        self._record("resp", (date_str,), {})
        return {
            "calendarDate": date_str,
            "lowestRespirationValue": 9.0,
            "highestRespirationValue": 21.0,
            "avgWakingRespirationValue": 13.0,
            "avgSleepRespirationValue": 16.0,
        }

    def get_hrv_data(self, date_str):
        self._record("hrv", (date_str,), {})
        if date_str in self._dates_with_data:
            return _canned_hrv(date_str)
        return None

    def get_body_battery(self, start, end):
        self._record("bb", (start, end), {})
        return [
            {
                "date": start,
                "charged": 62,
                "drained": 26,
                "bodyBatteryValuesArray": [],
            }
        ]

    def get_body_battery_events(self, date_str):
        self._record("bbe", (date_str,), {})
        return []

    def get_intensity_minutes_data(self, date_str):
        self._record("intensity_min", (date_str,), {})
        return {
            "calendarDate": date_str,
            "weeklyModerate": 70,
            "weeklyVigorous": 7,
            "weeklyTotal": 84,
            "weekGoal": 150,
        }

    def get_all_day_events(self, date_str):
        self._record("events", (date_str,), {})
        return []

    def get_lifestyle_logging_data(self, date_str):
        self._record("lifestyle", (date_str,), {})
        return {"completionStats": []}

    # ---- activities ----
    def get_activities(self, start, limit):
        self._record("activities_list", (start, limit), {})
        return [
            {
                "activityId": 22465797734,
                "activityName": "Test Run",
                "activityType": {"typeKey": "running", "typeId": 1},
                "startTimeLocal": "2026-06-03T07:00:00",
                "distance": 5000.0,
                "duration": 1800.0,
            }
        ]

    def get_activities_by_date(self, start_str, end_str, _unused, sort="asc"):
        self._record(
            "activities_by_date", (start_str, end_str, _unused, sort), {}
        )
        return []

    def get_activity(self, activity_id):
        self._record("activity", (activity_id,), {})
        return {
            "activityId": activity_id,
            "activityName": "Test Run",
            "distance": 5000.0,
            "duration": 1800.0,
        }

    def get_activity_splits(self, activity_id):
        self._record("activity_splits", (activity_id,), {})
        return []

    def get_activity_split_summaries(self, activity_id):
        self._record("activity_split_summaries", (activity_id,), {})
        return []

    def get_activity_typed_splits(self, activity_id):
        self._record("activity_typed_splits", (activity_id,), {})
        return []

    def get_activity_weather(self, activity_id):
        self._record("activity_weather", (activity_id,), {})
        return None

    def get_activity_hr_in_timezones(self, activity_id):
        self._record("activity_hr_zones", (activity_id,), {})
        return []

    def get_activity_power_in_timezones(self, activity_id):
        self._record("activity_power_zones", (activity_id,), {})
        return []

    def get_activity_exercise_sets(self, activity_id):
        self._record("activity_exercise_sets", (activity_id,), {})
        return []

    def get_activity_details(self, activity_id):
        self._record("activity_details", (activity_id,), {})
        return None

    # ---- body composition ----
    def get_body_composition(self, start_str, end_str):
        self._record("body_comp", (start_str, end_str), {})
        return []

    def get_weigh_ins(self, start_str, end_str):
        self._record("weigh_ins", (start_str, end_str), {})
        return []

    # ---- training metrics ----
    def get_training_readiness(self, date_str):
        self._record("training_readiness", (date_str,), {})
        return []

    def get_morning_training_readiness(self, date_str):
        self._record("morning_training_readiness", (date_str,), {})
        return []

    def get_training_status(self, date_str):
        self._record("training_status", (date_str,), {})
        return []

    def get_max_metrics(self, date_str):
        self._record("max_metrics", (date_str,), {})
        return []

    def get_fitnessage_data(self, date_str):
        self._record("fitness_age", (date_str,), {})
        return []

    def get_lactate_threshold(self):
        self._record("lactate_threshold", (), {})
        return None

    def get_cycling_ftp(self):
        self._record("cycling_ftp", (), {})
        return None

    def get_hill_score(self, start_str, end_str):
        self._record("hill_score", (start_str, end_str), {})
        return []

    def get_endurance_score(self, start_str, end_str):
        self._record("endurance_score", (start_str, end_str), {})
        return []

    def get_running_tolerance(self, start_str, end_str):
        self._record("running_tolerance", (start_str, end_str), {})
        return []

    def get_race_predictions(self):
        self._record("race_predictions", (), {})
        return None

    # ---- goals and records ----
    def get_personal_record(self):
        self._record("personal_records", (), {})
        return []

    def get_earned_badges(self):
        self._record("badges", (), {})
        return []

    def get_goals(self, status, _offset, _limit):
        self._record(f"goals_{status}", (status, _offset, _limit), {})
        return []

    # ---- trends ----
    def get_daily_steps(self, start_str, end_str):
        self._record("daily_steps", (start_str, end_str), {})
        return []

    def get_weekly_steps(self, end_date, weeks):
        self._record("weekly_steps", (end_date, weeks), {})
        return []

    def get_weekly_stress(self, end_date, weeks):
        self._record("weekly_stress", (end_date, weeks), {})
        return []

    def get_weekly_intensity_minutes(self, start_str, end_str):
        self._record("weekly_im", (start_str, end_str), {})
        return []

    def get_floors(self, start_str):
        self._record("floors", (start_str,), {})
        return None

    def get_progress_summary_between_dates(self, start_str, end_str, metric, _):
        self._record(f"progress_{metric}", (start_str, end_str, metric, _), {})
        return []

    # ---- golf ----
    def get_golf_summary(self):
        self._record("golf_summary", (), {})
        return []

    def get_golf_scorecard(self, scorecard_id):
        self._record("golf_scorecard", (scorecard_id,), {})
        return None

    def get_golf_shot_data(self, scorecard_id):
        self._record("golf_shot_data", (scorecard_id,), {})
        return None

    # ---- gear ----
    def get_gear(self, profile_num):
        self._record("gear_list", (profile_num,), {})
        return []

    def get_gear_defaults(self, profile_num):
        self._record("gear_defaults", (profile_num,), {})
        return []

    def get_gear_stats(self, uuid):
        self._record("gear_stats", (uuid,), {})
        return None

    # ---- training plans ----
    def get_training_plans(self):
        self._record("training_plans", (), {})
        return []

    def get_training_plan_by_id(self, plan_id):
        self._record("training_plan", (plan_id,), {})
        return None

    def get_adaptive_training_plan_by_id(self, plan_id):
        self._record("adaptive_training_plan", (plan_id,), {})
        return None

    # ---- workouts ----
    def get_workouts(self, _offset, _limit):
        self._record("workouts", (_offset, _limit), {})
        return []

    def get_workout_by_id(self, workout_id):
        self._record("workout", (workout_id,), {})
        return None

    # ---- hydration ----
    def get_hydration_data(self, date_str):
        self._record("hydration", (date_str,), {})
        return None

    # ---- nutrition ----
    def get_nutrition_daily_food_log(self, date_str):
        self._record("food_log", (date_str,), {})
        return None

    def get_nutrition_daily_meals(self, date_str):
        self._record("meals", (date_str,), {})
        return None

    def get_nutrition_daily_settings(self, date_str):
        self._record("nutrition_settings", (date_str,), {})
        return None

    # ---- women's health ----
    def get_pregnancy_summary(self):
        self._record("pregnancy", (), {})
        return None

    def get_menstrual_calendar_data(self, start_str, end_str):
        self._record("menstrual", (start_str, end_str), {})
        return None


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_garmin_api() -> MockGarminApi:
    """A deterministic mock of the Garmin API for 2 days of data."""
    return MockGarminApi()


@pytest.fixture(autouse=True)
def _fast_rate_limiter():
    """Use a zero-delay limiter so tests do not sleep for 13s+.

    The 60s sleep inside RateLimiter.on_rate_limit() is patched by this
    fixture so a 429 raised by a mock does not block the test process.
    """
    from unittest.mock import patch
    import time
    from garmin_llm_export import rate_limit

    rate_limit._limiter = None
    rate_limit.configure_limiter(0.0)
    with patch.object(rate_limit.time, "sleep", lambda _s: None):
        yield
    rate_limit._limiter = None


@pytest.fixture
def sample_sleep_data():
    """Realistic sleep data dict keyed by wake date."""
    return {
        DAY0.isoformat(): _canned_sleep_for_day(DAY0.isoformat()),
        DAY1.isoformat(): _canned_sleep_for_day(DAY1.isoformat()),
    }


@pytest.fixture
def sample_daily_sleep_dto():
    """A single realistic dailySleepDTO (the 2026-06-05 record)."""
    return _realistic_daily_sleep_dto(DAY1.isoformat())


@pytest.fixture
def tmp_export_dir(tmp_path: Path) -> Path:
    """A fresh export directory for each test (no cross-test contamination)."""
    out = tmp_path / "export"
    out.mkdir()
    return out


@pytest.fixture
def cache(tmp_export_dir: Path) -> ExportCache:
    """An enabled cache bound to the temp export dir."""
    return ExportCache(tmp_export_dir, enabled=True)


@pytest.fixture
def reset_settings():
    """Reset the global ExportSettings before AND after a test."""
    global_settings.compact = False
    global_settings.split = False
    global_settings.update = False
    yield global_settings
    global_settings.compact = False
    global_settings.split = False
    global_settings.update = False
