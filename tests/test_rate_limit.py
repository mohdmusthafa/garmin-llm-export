"""Unit tests for garmin_llm_export.rate_limit.

These tests exercise the RateLimiter state machine and the safe_call wrapper
without making any real network calls.

NOTE: RateLimiter.on_rate_limit() always calls time.sleep(60). We patch
time.sleep in every test that triggers a rate limit so the suite stays fast.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import patch

import pytest

from garmin_llm_export import rate_limit
from garmin_llm_export.rate_limit import RateLimiter, configure_limiter, safe_call


@pytest.fixture(autouse=True)
def _reset_limiter():
    """Reset the global limiter between tests.

    Overrides the autouse fast-rate-limiter from conftest.py so the rate
    limit module's own state is the thing under test.
    """
    rate_limit._limiter = None
    yield
    rate_limit._limiter = None


@pytest.fixture
def no_sleep():
    """Patch time.sleep so 60s rate-limit sleeps do not block the test."""
    with patch("garmin_llm_export.rate_limit.time.sleep") as mock:
        yield mock


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------
class TestRateLimiter:
    def test_initial_state(self):
        r = RateLimiter(base_delay=0.1)
        assert r.current_delay == 0.1
        assert r.call_count == 0
        assert r.consecutive_ok == 0

    def test_on_success_increments_counter(self):
        r = RateLimiter(base_delay=0.1)
        for _ in range(5):
            r.on_success()
        assert r.consecutive_ok == 5
        assert r.current_delay == 0.1  # not reduced below base

    def test_on_rate_limit_doubles_delay(self, no_sleep):
        r = RateLimiter(base_delay=0.1)
        r.on_rate_limit()
        # 0.1 * 2 = 0.2
        assert r.current_delay == 0.2
        assert r.consecutive_ok == 0
        # 60s sleep was skipped
        no_sleep.assert_called_once_with(60)

    def test_on_rate_limit_capped(self, no_sleep):
        r = RateLimiter(base_delay=0.1)
        for _ in range(20):
            r.on_rate_limit()
        # Capped at 10.0
        assert r.current_delay <= 10.0

    def test_on_error_increases_delay(self):
        r = RateLimiter(base_delay=0.1)
        r.on_error()
        # 0.1 * 1.2 = 0.12
        assert r.current_delay == pytest.approx(0.12, abs=1e-6)
        assert r.consecutive_ok == 0

    def test_on_success_decreases_after_throttling(self, no_sleep):
        r = RateLimiter(base_delay=0.1)
        r.on_rate_limit()
        r.on_rate_limit()
        # 0.4
        assert r.current_delay == pytest.approx(0.4, abs=1e-6)
        for _ in range(15):
            r.on_success()
        # The success branch decays by 0.9 per call once consecutive_ok > 10,
        # so over 5 decay steps: 0.4 * 0.9^5 ~= 0.236. The important contract
        # is that the delay is *decreasing*, not that it has reached base.
        assert r.current_delay < 0.4
        assert r.consecutive_ok == 15

    def test_wait_increments_call_count(self):
        r = RateLimiter(base_delay=0.0)  # no sleep
        r.wait()
        r.wait()
        assert r.call_count == 2


# ---------------------------------------------------------------------------
# safe_call
# ---------------------------------------------------------------------------
class TestSafeCall:
    def test_returns_value_on_success(self):
        configure_limiter(base_delay=0.0)
        result = safe_call(lambda x: x * 2, 5, label="doubler")
        assert result == 10

    def test_returns_none_on_generic_exception(self):
        def boom():
            raise ValueError("kaboom")

        result = safe_call(boom, label="boom")
        assert result is None

    def test_returns_none_on_garmin_connection_error_400(self, caplog):
        from garminconnect import GarminConnectConnectionError

        def fn():
            raise GarminConnectConnectionError("API Error 400 - bad request")

        # The package logger is set to WARNING in conftest to keep test output
        # clean. To inspect the DEBUG "Not available" message we must raise the
        # level for the duration of the call.
        logger = logging.getLogger("garmin_llm_export.rate_limit")
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            result = safe_call(fn, label="bad_request")
        finally:
            logger.setLevel(old_level)
        assert result is None
        # 400 should be debug-level, not warning
        assert any("Not available" in r.getMessage() for r in caplog.records)

    def test_returns_none_on_garmin_connection_error_404(self, caplog):
        from garminconnect import GarminConnectConnectionError

        def fn():
            raise GarminConnectConnectionError("API Error 404 - not found")

        logger = logging.getLogger("garmin_llm_export.rate_limit")
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            result = safe_call(fn, label="missing")
        finally:
            logger.setLevel(old_level)
        assert result is None
        assert any("Not available" in r.getMessage() for r in caplog.records)

    def test_returns_none_on_garmin_too_many_requests(self, no_sleep):
        from garminconnect import GarminConnectTooManyRequestsError

        def fn():
            raise GarminConnectTooManyRequestsError("rate limited")

        configure_limiter(base_delay=0.0)
        # safe_call retries once after 60s sleep; we patched sleep above.
        result = safe_call(fn, label="rl")
        assert result is None
