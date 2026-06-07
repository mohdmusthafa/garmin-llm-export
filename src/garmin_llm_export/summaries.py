"""Pre-computed derived summaries for Garmin data.

Right now this module only handles sleep -- the GLE-6 summary engine.
The functions in this file are pure: they take a dict, return a dict,
and never touch the network or the filesystem. That makes them trivial
to unit-test with the canned fixtures in ``tests/conftest.py``.

The downstream consumers (GLE-7 ``--last-sleep``, GLE-9 derived fields in
Daily Health, GLE-10 ``get_latest_sleep_summary``, GLE-12 Sleep Summaries
section) all use the same builder. Adding a new derived field is a
one-place change: extend :func:`build_sleep_summary`.

Key map (nested structure in the sleep payload)::
    Sleep.dailySleepDTO.sleepScores.{totalDuration,stress,awakeCount,remPercentage,restlessness,lightPercentage,deepPercentage,overall}
    Sleep.dailySleepDTO.{sleepScoreFeedback,sleepScoreInsight,sleepScorePersonalizedInsight}
    Sleep.{dailySleepDTO,sleepMovement,remSleepData,sleepLevels,sleepRestlessMoments,hrvData,avgOvernightHrv,hrvStatus,bodyBatteryChange,restingHeartRate}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# ---------------------------------------------------------------------------
# Verdict phrase map
#
# Garmin exposes a fixed vocabulary of two enums on the dailySleepDTO:
#   sleepScoreFeedback        ("NEGATIVE_*", "POSITIVE_*", "MEH", etc.)
#   sleepScoreInsight         ("NEGATIVE_STRESSFUL_DAY", etc.)
# We translate those into short, human-readable phrases for the file
# header. Unknown keys fall back to the raw enum string so we never
# silently drop information.
# ---------------------------------------------------------------------------
_FEEDBACK_PHRASES: dict[str, str] = {
    "NEGATIVE_LONG_BUT_RESTLESS": "Long but restless",
    "NEGATIVE_LONG_BUT_POOR_QUALITY": "Long but poor quality",
    "NEGATIVE_SHORT": "Too short",
    "NEGATIVE_RESTLESS": "Restless",
    "NEGATIVE_STRESSFUL_DAY": "Stressful day",
    "NEGATIVE_BAD_SCORE": "Poor score",
    "POSITIVE_CONSISTENT": "Consistent",
    "POSITIVE_LONG": "Long",
    "POSITIVE_QUALITY": "Good quality",
    "POSITIVE_PERFECT": "Perfect",
    "POSITIVE_RECOVERY": "Restorative",
    "MEH_BALANCED": "Balanced",
    "MEH_UNBALANCED": "Unbalanced",
    "MEH_TYPICAL": "Typical",
}

_INSIGHT_PHRASES: dict[str, str] = {
    "NEGATIVE_STRESSFUL_DAY": "Stressful day",
    "NEGATIVE_LATE_NIGHT": "Late to bed",
    "NEGATIVE_WOKE_UP_OFTEN": "Woke up often",
    "NEGATIVE_BAD_SCORE": "Bad score",
    "NEGATIVE_INSUFFICIENT_DATA": "Insufficient data",
    "POSITIVE_PERFECT_SCORE": "Perfect score",
    "POSITIVE_GOOD_SCORE": "Good score",
    "POSITIVE_NORMAL_SCORE": "Normal score",
    "MEH_NORMAL_SCORE": "Normal score",
}


def _humanize_qualifier(qualifier: Optional[str]) -> Optional[str]:
    """Map a subscore qualifier (POOR/FAIR/GOOD/EXCELLENT) to lowercase."""
    if not qualifier:
        return None
    return qualifier.lower()


def _pct(part: Optional[int | float], whole: Optional[int | float]) -> Optional[float]:
    """Safe percentage with one decimal. Returns None when inputs are missing."""
    if part is None or whole is None or whole <= 0:
        return None
    return round((part / whole) * 100, 1)


def _ms_to_local(ms: Optional[int | float], tz: Optional[str]) -> Optional[str]:
    """Convert a Garmin millisecond timestamp to an ISO 8601 string in `tz`.

    Returns None when the input is missing or the timezone is unknown.
    Localisation happens in the user-profile timezone; the API also exposes
    ``sleepStartTimestampLocal`` already-localised but that is in the
    device timezone (rarely set to anything but the watch's stored zone).
    """
    if ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None
    if tz:
        try:
            dt = dt.astimezone(ZoneInfo(tz))
        except ZoneInfoNotFoundError:
            pass
    return dt.isoformat()


def _safe_int(value: Any) -> Optional[int]:
    """Coerce to int, returning None on None/empty/invalid."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    """Coerce to float, returning None on None/empty/invalid."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_subscores(
    sleep_scores: dict[str, Any],
) -> list[dict[str, Any]]:
    """Normalise sleepScores.* into [{name, value, qualifier, optimal_range}, ...].

    The wire format is a mix of value-bearing fields (overall, remPercentage,
    lightPercentage, deepPercentage) and qualifier-only fields (stress,
    awakeCount, restlessness, totalDuration). We surface both with the same
    shape so downstream code can render them as a uniform table.
    """
    subscores: list[dict[str, Any]] = []
    for name, payload in sleep_scores.items():
        if not isinstance(payload, dict):
            continue
        entry: dict[str, Any] = {"name": name}
        if "value" in payload:
            entry["value"] = payload.get("value")
        entry["qualifier"] = _humanize_qualifier(payload.get("qualifierKey"))
        if "optimalStart" in payload and "optimalEnd" in payload:
            entry["optimal_range"] = [
                payload.get("optimalStart"),
                payload.get("optimalEnd"),
            ]
        subscores.append(entry)
    return subscores


def _verdict(
    feedback: Optional[str], insight: Optional[str]
) -> str:
    """Compose a short verdict line from feedback + insight enums.

    The format is: "<feedback phrase>; <insight phrase>." -- but if either
    is missing, the other stands alone. Unknown enums are passed through
    in humanised form (e.g. "Meh_typical" -> "Meh typical").
    """
    parts: list[str] = []
    if feedback:
        phrase = _FEEDBACK_PHRASES.get(feedback)
        if phrase is None:
            phrase = feedback.replace("_", " ").lower()
        parts.append(phrase)
    if insight:
        phrase = _INSIGHT_PHRASES.get(insight)
        if phrase is None:
            phrase = insight.replace("_", " ").lower()
        parts.append(phrase)
    if not parts:
        return "No verdict available."
    if len(parts) == 1:
        return f"{parts[0]}."
    return f"{parts[0]}; {parts[1]}."


def build_sleep_summary(
    daily_sleep_payload: Optional[dict[str, Any]],
    *,
    tz: Optional[str] = None,
    latest_only: bool = True,
) -> Optional[dict[str, Any]]:
    """Return a derived summary of the most recent sleep record.

    Args:
        daily_sleep_payload: The full payload returned by
            ``Garmin.get_sleep_data(calendar_date)``. The function only
            needs ``dailySleepDTO`` and a few sibling fields (HRV, resting
            HR, body battery delta), so a partial payload is acceptable;
            missing fields surface as ``None`` in the summary rather than
            raising.
        tz: IANA timezone name (e.g. ``"Asia/Kolkata"``) used to localise
            bedtime / wake. Defaults to UTC.
        latest_only: Reserved for future multi-night summaries. Currently
            a no-op (the payload always represents a single night).

    Returns:
        A dict with the headline fields described in the GLE-6 spec, or
        ``None`` when the payload is missing or has no ``dailySleepDTO``.
    """
    if latest_only is False:
        # Hook for a future "weekly summary" without breaking the signature.
        pass

    if not daily_sleep_payload or not isinstance(daily_sleep_payload, dict):
        return None

    dto = daily_sleep_payload.get("dailySleepDTO")
    if not dto or not isinstance(dto, dict):
        return None

    total_seconds = _safe_int(dto.get("sleepTimeSeconds")) or 0
    deep = _safe_int(dto.get("deepSleepSeconds"))
    light = _safe_int(dto.get("lightSleepSeconds"))
    rem = _safe_int(dto.get("remSleepSeconds"))
    awake = _safe_int(dto.get("awakeSleepSeconds"))

    # Time in bed = sleep window = end - start (in seconds)
    start_ms = _safe_int(dto.get("sleepStartTimestampGMT"))
    end_ms = _safe_int(dto.get("sleepEndTimestampGMT"))
    time_in_bed_seconds: Optional[int] = None
    if start_ms is not None and end_ms is not None and end_ms > start_ms:
        time_in_bed_seconds = int((end_ms - start_ms) / 1000)

    stages: dict[str, Any] = {
        "deep_seconds": deep,
        "light_seconds": light,
        "rem_seconds": rem,
        "awake_seconds": awake,
    }
    if total_seconds > 0:
        stages["deep_pct"] = _pct(deep, total_seconds)
        stages["light_pct"] = _pct(light, total_seconds)
        stages["rem_pct"] = _pct(rem, total_seconds)
        stages["awake_pct"] = _pct(awake, total_seconds)

    efficiency: Optional[float] = None
    if time_in_bed_seconds and total_seconds:
        efficiency = round((total_seconds / time_in_bed_seconds) * 100, 1)

    sleep_scores = dto.get("sleepScores") or {}
    if not isinstance(sleep_scores, dict):
        sleep_scores = {}

    score_block: dict[str, Any] = {
        "overall": _safe_int(sleep_scores.get("overall", {}).get("value"))
        if isinstance(sleep_scores.get("overall"), dict)
        else None,
        "qualifier": _humanize_qualifier(
            sleep_scores.get("overall", {}).get("qualifierKey")
            if isinstance(sleep_scores.get("overall"), dict)
            else None
        ),
        "subscores": _extract_subscores(sleep_scores),
    }

    vitals: dict[str, Any] = {
        "resting_hr": _safe_int(daily_sleep_payload.get("restingHeartRate")),
        "avg_sleep_stress": _safe_float(dto.get("avgSleepStress")),
        "respiration": {
            "avg": _safe_float(dto.get("averageRespirationValue")),
            "min": _safe_float(dto.get("lowestRespirationValue")),
            "max": _safe_float(dto.get("highestRespirationValue")),
        },
        "hrv_avg": _safe_float(daily_sleep_payload.get("avgOvernightHrv")),
        "hrv_status": daily_sleep_payload.get("hrvStatus"),
        "restless_moments_count": _safe_int(
            daily_sleep_payload.get("restlessMomentsCount")
        ),
        "awake_count": _safe_int(dto.get("awakeCount")),
        "body_battery_change": _safe_int(
            daily_sleep_payload.get("bodyBatteryChange")
        ),
    }

    return {
        "date": dto.get("calendarDate"),
        "bedtime_local": _ms_to_local(start_ms, tz),
        "wake_local": _ms_to_local(end_ms, tz),
        "time_in_bed_seconds": time_in_bed_seconds,
        "asleep_seconds": total_seconds or None,
        "sleep_efficiency": efficiency,
        "stages": stages,
        "score": score_block,
        "vitals": vitals,
        "verdict": _verdict(
            dto.get("sleepScoreFeedback"),
            dto.get("sleepScoreInsight"),
        ),
    }


def _detect_user_tz(api: Any) -> Optional[str]:
    """Try to read the user's timezone from the Garmin profile.

    The `profile_settings` endpoint exposes ``timeZone`` (an IANA name) for
    the logged-in user. We fall back to ``None`` (which means UTC in the
    downstream summary) when the profile is missing or the field is absent.
    """
    try:
        settings = api.get_userprofile_settings()  # type: ignore[attr-defined]
    except Exception:
        return None
    if not isinstance(settings, dict):
        return None
    return settings.get("timeZone")


def get_latest_sleep_summary(
    api: Any,
    *,
    tz: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return a sleep summary for the most recent night, or None.

    Args:
        api: A ``garminconnect.Garmin`` (or compatible) client. The function
            calls two endpoints: ``get_userprofile_settings`` (when ``tz`` is
            not given) and ``get_sleep_data`` for yesterday and today.
        tz: Optional IANA timezone (e.g. ``"Asia/Kolkata"``). When omitted,
            the function tries to read the timezone from the user profile.

    Returns:
        A summary dict (see :func:`build_sleep_summary`) for the most
        recent night that has data, or ``None`` if no sleep record is
        found in the last two days.

    Example:
        >>> from garmin_llm_export import get_latest_sleep_summary, login
        >>> from garmin_llm_export.config import DEFAULT_TOKENSTORE
        >>> from pathlib import Path
        >>> api = login(Path(DEFAULT_TOKENSTORE))
        >>> summary = get_latest_sleep_summary(api)
        >>> print(summary["verdict"])
    """
    if tz is None:
        tz = _detect_user_tz(api)
        if tz is None:
            tz = "UTC"

    from datetime import date, timedelta

    today = date.today()
    yesterday = today - timedelta(days=1)
    # Most recent first: today, then yesterday. The function returns the
    # first non-None summary it finds.
    for d in (today, yesterday):
        try:
            payload = api.get_sleep_data(d.isoformat())  # type: ignore[attr-defined]
        except Exception:
            payload = None
        if not payload:
            continue
        summary = build_sleep_summary(payload, tz=tz)
        if summary is not None:
            return summary
    return None
