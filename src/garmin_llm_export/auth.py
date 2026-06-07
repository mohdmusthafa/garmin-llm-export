"""Garmin Connect authentication."""

from __future__ import annotations

import logging
import os
import sys
from getpass import getpass
from pathlib import Path

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
)

from .config import DEFAULT_TOKENSTORE

log = logging.getLogger(__name__)


def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs from .env (does not override existing env vars)."""
    candidates = [path] if path else [Path(".env"), Path(__file__).resolve().parent.parent / ".env"]
    for env_path in candidates:
        if env_path is None or not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip("\"'")
            if key and val:
                os.environ.setdefault(key, val)
        return


def _credentials() -> tuple[str, str]:
    email = os.getenv("GARMIN_EMAIL", "").strip()
    password = os.getenv("GARMIN_PASSWORD", "").strip()
    if email and password:
        return email, password

    print("\n  Garmin Connect Login")
    print("  Add GARMIN_EMAIL and GARMIN_PASSWORD to .env to skip this prompt.\n")
    if not email:
        email = input("  Email: ").strip()
    if not password:
        password = getpass("  Password: ")
    if not email or not password:
        log.error("GARMIN_EMAIL and GARMIN_PASSWORD are required")
        sys.exit(1)
    return email, password


def login(tokenstore: Path | str | None = None) -> Garmin:
    """Authenticate via cached tokens or fresh SSO login."""
    store = Path(tokenstore or os.getenv("GARMINTOKENS", DEFAULT_TOKENSTORE)).expanduser()
    email, password = _credentials()
    api = Garmin(email=email, password=password)

    if store.exists():
        try:
            api.login(str(store))
            log.info("Authenticated with cached tokens")
            return api
        except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
            log.info("Cached tokens invalid (%s), logging in fresh", type(exc).__name__)

    store.mkdir(parents=True, exist_ok=True)
    log.info("Logging in to Garmin Connect...")
    mfa_status, mfa_state = api.login(str(store))

    if mfa_status == "needs_mfa":
        code = input("\n  Enter MFA code: ").strip()
        api.resume_login(mfa_state, code)
        api.client.dump(str(store))

    log.info("Tokens saved to %s", store)
    return api
