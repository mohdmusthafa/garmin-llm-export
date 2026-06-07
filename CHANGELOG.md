# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0-alpha] - 2026-06-07

### Added

- **Sleep as a first-class citizen** (Phase 2):
  - `get_latest_sleep_summary()` Python helper for quick sleep data access
  - `--last-sleep` flag: plain-prose last-night sleep summary to stdout
  - `garmin-sleep` subcommand: `uv run garmin-sleep` shorthand for `--last-sleep`
  - `Sleep Summaries` export section with nightly sleep quality, stages, HRV, SpO₂
  - Derived daily fields: sleep efficiency, HRV deviation, body battery charge/discharge
  - Line-sizing logic: wraps JSON at 999 tokens for 128K context windows

- **Focus presets** (`--focus`): `sleep`, `recovery`, `training`, `body`, `all`
  - `--list-presets` and `--list-sections` for discovery
  - Per-focus cache key isolation so focused exports don't pollute full-export cache

- **Section filter** (`--sections`): export only named sections by ID
- **Cache freshness** (`SECTION_MAX_AGE_DAYS`): per-section TTL policy, auto-refresh stale data
- **CLI revamp**: grouped help, all three entry points (`garmin-export`, `garmin-sleep`, `python -m garmin_llm_export`)

### Changed

- Default export: 30 days (up from 14), 100 activities (up from 50)
- `--compact` implied by `--split` and `--update` (no need to double-flag)
- `--no-sleep-summary` flag to omit Sleep Summaries from full export when desired

### Fixed

- Null fields stripped in compact mode (was only partially working)
- Activity time-series omitted in compact mode (was still included)
- Resumable exports correctly resume from last cached day (was restarting from day 0)