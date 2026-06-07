"""Adaptive API rate limiting."""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable, Optional

from garminconnect import GarminConnectConnectionError, GarminConnectTooManyRequestsError

log = logging.getLogger(__name__)

_limiter: "RateLimiter | None" = None


class RateLimiter:
    """Thread-safe adaptive pacer for Garmin API calls."""

    def __init__(self, base_delay: float = 0.15):
        self.base_delay = base_delay
        self.current_delay = base_delay
        self.call_count = 0
        self.last_call = 0.0
        self.consecutive_ok = 0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.time() - self.last_call
            if elapsed < self.current_delay:
                time.sleep(self.current_delay - elapsed)
            self.last_call = time.time()
            self.call_count += 1
            if self.call_count % 250 == 0:
                log.info("  Pacing break after %s API calls...", self.call_count)
                time.sleep(2)

    def on_success(self) -> None:
        with self._lock:
            self.consecutive_ok += 1
            if self.consecutive_ok > 10 and self.current_delay > self.base_delay:
                self.current_delay = max(self.base_delay, self.current_delay * 0.9)

    def on_rate_limit(self) -> None:
        with self._lock:
            self.consecutive_ok = 0
            self.current_delay = min(self.current_delay * 2, 10.0)
            log.warning(
                "  Rate limited -- delay now %.1fs, waiting 60s...", self.current_delay
            )
        time.sleep(60)

    def on_error(self) -> None:
        with self._lock:
            self.consecutive_ok = 0
            self.current_delay = min(self.current_delay * 1.2, 5.0)


def configure_limiter(base_delay: float = 0.15) -> RateLimiter:
    global _limiter
    _limiter = RateLimiter(base_delay)
    return _limiter


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def safe_call(fn: Callable, *args, label: str = "", **kwargs) -> Optional[Any]:
    """Call a Garmin API method with rate limiting and error handling."""
    limiter = get_limiter()
    limiter.wait()
    try:
        result = fn(*args, **kwargs)
        limiter.on_success()
        return result
    except GarminConnectTooManyRequestsError:
        limiter.on_rate_limit()
        limiter.wait()
        try:
            result = fn(*args, **kwargs)
            limiter.on_success()
            return result
        except Exception as exc:
            log.warning("  Retry failed (%s): %s", label, exc)
            limiter.on_error()
            return None
    except GarminConnectConnectionError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is None:
            match = re.search(r"API Error (\d{3})", str(exc))
            status = int(match.group(1)) if match else None
        if status == 429:
            limiter.on_rate_limit()
            return None
        if status in (400, 404):
            log.debug("  Not available (%s) [%s]", status, label)
        else:
            log.warning("  HTTP %s [%s]: %s", status, label, exc)
            limiter.on_error()
        return None
    except Exception as exc:
        log.warning("  API error [%s]: %s", label, exc)
        limiter.on_error()
        return None
