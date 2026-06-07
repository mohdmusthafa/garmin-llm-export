# Garmin LLM Export — Improvement Plan

**Document status:** Draft v1 — pending review
**Author:** Pi (automated assistant)
**Date:** 2026-06-05
**Target codebase:** `garmin-llm-export` (v0.1.0)

---

## 1. Background & Motivation

The current exporter is a well-engineered "dump everything to text" tool that works excellently for full-history NotebookLM uploads. However, when a user (or an LLM agent on their behalf) wants a fast, targeted query — "analyze my last night's sleep" — the workflow is friction-heavy:

- 282 API calls for one night
- 667 KB file for a 2-day window
- JSON embedded on single lines ≥ 50 KB that defeat `grep` and the agent's `read` tool
- Schema/key naming inconsistencies
- No pre-computed derived fields (stage %, efficiency, sleep score, etc.)
- No way to say "just give me sleep"

This plan addresses the 17 friction points identified in the prior session and reorganises them into a delivery roadmap with 5 epics, 16 tasks, explicit acceptance criteria, and measurable success conditions.

---

## 2. Goals & Non-Goals

### Goals
1. Reduce API calls for targeted queries by **≥ 80 %**.
2. Reduce file size for targeted queries by **≥ 95 %** (sleep-only should be < 10 KB).
3. Eliminate the need for ad-hoc parsing scripts when answering common questions.
4. Make the output directly LLM-readable (parseable with one `read` call, no manual extraction).
5. Preserve the current "full export for NotebookLM" use case without regression.

### Non-Goals (this iteration)
- Building a query DSL or SQL-like layer on top of the data
- Changing the on-disk cache layout in a breaking way
- Adding new data sources (Apple Health, Oura, etc.)
- Re-architecting the Garmin API client itself

---

## 3. Success Metrics (Measurable)

| Metric | Baseline (today) | Target | Measurement |
|---|---|---|---|
| API calls for "last night sleep" | ~282 (full 2-day run) | ≤ 30 | Count from `rate_limit.get_limiter().call_count` |
| Output file size for sleep query | 667 KB | ≤ 10 KB | `wc -c` on output file |
| Steps to answer a sleep question | 5+ (login → export → grep → parse → answer) | 1 (one CLI invocation) | Manual task count |
| Lines per `read` tool call to view sleep | >50,000 (single line) | <500 | `wc -l` on a section |
| Number of distinct issues closed (from Friction List) | 0 | ≥ 14 of 17 | Issue tracking |
| Backward compatibility | n/a | All existing flags produce identical output | Regression diff test |
| Test coverage (formatters, summaries, sleep) | 0 % | ≥ 80 % | `pytest --cov` |

---

## 4. Delivery Plan — 3 Phases, 5 Epics, 16 Tasks

### Phase overview

```
Phase 1 (Foundation)        Phase 2 (Sleep First-Class)        Phase 3 (Polish)
─────────────────────      ──────────────────────────         ──────────────────
GLE-1  Test scaffolding    GLE-6  Sleep summary engine        GLE-13 Local timestamp formatting
GLE-2  Section filter      GLE-7  --last-sleep flag           GLE-14 Schema/key consistency
GLE-3  Profile cache reuse GLE-8  garmin-sleep subcommand      GLE-15 Output index file
GLE-4  --focus presets     GLE-9  Derived fields in Daily H.   GLE-16 README + SKILL.md update
GLE-5  CLI help revamp     GLE-10 Python API helper
                           GLE-11 LLM-readable line sizing
                           GLE-12 Sleep section restructure
```

**Suggested sequencing:** 1 → 2 → 3. Each phase ends with a runnable, releasable artifact.

---

## 5. Task Tracker — Implementation Status

**Last updated:** 2026-06-07 (all tasks verified complete)
**Overall progress:** 16 / 16 tasks complete (100 %)

This section is the single source of truth for what has been built. Update it whenever a task moves state.

### 5.1 Status legend

| Marker | State | Meaning |
|---|---|---|
| 📋 | To Do | Triaged, not yet started |
| 🚧 | In Progress | Active work in a branch |
| ✅ | Done | Merged, acceptance criteria met, tests pass |
| ⏸ | Blocked | Cannot proceed; see notes for blocker |
| ❌ | Cancelled | Will not be done (with reason) |

### 5.2 Progress by phase

| Phase | Tasks | ✅ Done | 🚧 In Progress | ⏸ Blocked | 📋 To Do | % Complete |
|---|---|---|---|---|---|---|
| Phase 1 — Foundation | 5 | 5 | 0 | 0 | 0 | **100 %** |
| Phase 2 — Sleep First-Class | 7 | 7 | 0 | 0 | 0 | **100 %** |
| Phase 3 — Polish | 4 | 4 | 0 | 0 | 0 | **100 %** |
| **Total** | **16** | **16** | **0** | **0** | **0** | **100 %** |

### 5.3 Progress by epic

| Epic | Tasks | Done | % Complete |
|---|---|---|---|
| A — Foundations | 5 | 5 | **100 %** |
| B — Sleep First-Class | 7 | 7 | **100 %** |
| C — Polish | 4 | 4 | **100 %** |

### 5.4 Full task status

| ID | Title | Phase | Epic | Priority | Effort | Status | Dependencies | Branch / Notes |
|---|---|---|---|---|---|---|---|---|
| GLE-1  | Test scaffolding & fixtures       | 1 | A | P0 | S  | ✅ Done   | —                          | Tests, fixtures, mock API, golden snapshot. #gle-1 |
| GLE-2  | Section selection (`--sections`)  | 1 | A | P0 | M  | ✅ Done   | GLE-1                       | `SECTION_REGISTRY` + `sections=` filter; TOC reflects choice. |
| GLE-3  | Reuse cached static sections      | 1 | A | P1 | S  | ✅ Done   | GLE-1                       | `cache.section_age()` + `is_section_fresh()`; per-section max-age policy. |
| GLE-4  | Focus presets (`--focus`)         | 1 | A | P0 | S  | ✅ Done   | GLE-2                       | New `presets.py`; sleep/recovery/training/body/all. |
| GLE-5  | CLI help & discovery revamp       | 1 | A | P2 | XS | ✅ Done   | GLE-2, GLE-4                | Argument groups, `--list-presets`, `--list-sections`, quick-start epilog. |
| GLE-6  | Sleep summary engine              | 2 | B | P0 | M  | ✅ Done   | GLE-1                       | Pure function in `summaries.py`; verdict phrases, stage pcts, vitals. 32 unit tests. |
| GLE-7  | `--last-sleep` flag               | 2 | B | P0 | S  | ✅ Done   | GLE-2, GLE-3, GLE-6         | `last_sleep.py` writer; ~10 KB output, plain-prose. |
| GLE-8  | `garmin-sleep` subcommand         | 2 | B | P1 | S  | ✅ Done   | GLE-7                       | `sleep_cli.py` + pyproject entry; `--days N` for multi-night. |
| GLE-9  | Derived fields in Daily Health    | 2 | B | P1 | M  | ✅ Done   | GLE-6                       | `add_derived_daily_fields` adds `_summary`, `_weekly_avg`, `_morning_charge_delta`. Compact-only. |
| GLE-10 | Python API helper                 | 2 | B | P1 | XS | ✅ Done   | GLE-6                       | `get_latest_sleep_summary(api, *, tz=None)` in `__init__.py`. |
| GLE-11 | LLM-readable line sizing          | 2 | B | P0 | S  | ✅ Done   | GLE-1                       | `line_budget=2000`, `wrap_json`, TOC line ranges. |
| GLE-12 | Restructure Sleep section         | 2 | B | P1 | S  | ✅ Done   | GLE-6                       | Full-mode "Sleep Summaries" prose block; `--no-sleep-summary` to skip. |
| GLE-13 | Local timestamp formatting        | 3 | C | P1 | S  | ✅ Done   | GLE-11                      | `add_local_timestamps` in formatters.py; `TIMESTAMP_FIELDS_GMT` constant. |
| GLE-14 | Schema/key consistency            | 3 | C | P2 | XS | ✅ Done   | GLE-9                       | `_extract_key_map` in exporter.py; key map embedded in output. |
| GLE-15 | Output index file                 | 3 | C | P2 | S  | ✅ Done   | GLE-11, GLE-14              | `_write_index_file` in exporter.py; `skip_index` param. |
| GLE-16 | README& SKILL.md update          | 3 | C | P1 | S  | ✅ Done   | GLE-4, GLE-7, GLE-8, GLE-10 | README, SKILL.md, and CHANGELOG all updated for 0.2.0-alpha. |

### 5.5 Ready-to-pick queue

**All 16 tasks complete.** No tasks remaining.

### 5.6 Critical path

The longest dependency chain that blocks the headline feature (`--last-sleep`):

```
GLE-1 → GLE-2 → GLE-4
GLE-1 → GLE-6 → GLE-7  ← headline feature
                       ↘ GLE-8
GLE-1 → GLE-3 ────────↗
```

Wall-clock estimate: ~3.5 working days from GLE-1 to a usable `--last-sleep`.

### 5.7 How to update this section

1. When you **start** a task: change its row from `📋 To Do` to `🚧 In Progress` and add a `branch: <name>` link in the "Branch / Notes" column.
2. When you **merge**: change to `✅ Done` and link the PR (e.g. `#123`).
3. When you **get blocked**: change to `⏸ Blocked` and describe the blocker in "Branch / Notes".
4. When you **cancel** a task: change to `❌ Cancelled` and add a one-line reason.
5. **Always** update the "Last updated" date and the per-phase / per-epic counters in §5.2 and §5.3.
6. A task is `✅ Done` only when every item in §10 ("Definition of Done") is satisfied.

---

## 6. Epic & Task Breakdown

### Epic A — Foundations (Phase 1)
*Goal: build the test harness and the section-selection primitive that everything else depends on.*

---

#### **GLE-1 — Test scaffolding & fixtures**
- **Priority:** P0
- **Owner:** AI agent
- **Effort:** S (0.5 day)
- **Dependencies:** none
- **Status:** To Do

**Problem addressed:** None directly, but the project has zero test coverage. Every other change needs verification.

**Scope:**
- Add `tests/` directory with `pytest`-based layout.
- Add `pyproject.toml` `[project.optional-dependencies] dev = ["pytest", "pytest-cov", "freezegun"]` and a `[tool.pytest.ini_options]` block.
- Provide a `conftest.py` with:
  - A `mock_garmin_api` fixture exposing the methods used in `exporter.py` (deterministic canned responses for at least 2 days of sleep + 1 activity).
  - A `sample_sleep_data` fixture with realistic `dailySleepDTO`, `sleepLevels`, `sleepHeartRate`, etc.
  - A `tmp_export_dir` fixture for cache/output isolation.
- Snapshot test for one full export run against the mock API to lock in output shape.

**Acceptance criteria:**
- `uv run pytest` runs and passes.
- `uv run pytest --cov=garmin_llm_export --cov-report=term-missing` reports coverage.
- CI-equivalent run completes in < 5 s (no network calls).
- The snapshot test produces a stable hash for a given input fixture.

**Test plan:**
- Unit: formatters (strip_empty, downsample_timeseries, compact_daily, to_json).
- Integration: end-to-end run of `GarminExporter.run()` with the mock API and assert file exists, has the expected sections, and is < N bytes.
- Snapshot: full `garmin_export_*.txt` content hash matches the checked-in golden file.

---

#### **GLE-2 — Section selection (`--sections`)**
- **Priority:** P0
- **Owner:** AI agent
- **Effort:** M (1 day)
- **Dependencies:** GLE-1
- **Status:** To Do

**Problems addressed:** #1 (no sleep-specific mode), #3 (always pulls all static sections), #4 (no way to skip cached data).

**Scope:**
- Add CLI flag `--sections sleep,training,activities` (comma-separated, validated against known section names).
- Refactor the `sections` list in `exporter.py:run()` to be derived from the parsed flag.
- Default to all sections if flag is absent (backward compatible).
- Update the table-of-contents and file header to reflect which sections are included.
- Update the activity / daily-health / training fetchers to early-return empty dicts when their section is not requested (avoid API calls entirely).

**Acceptance criteria:**
- `uv run garmin-export --sections sleep --days 2` produces a file containing **only** the Daily Health section (sleep is part of it).
- API call count for the above command is ≤ 30 (down from 282).
- File size is ≤ 50 KB (down from 667 KB).
- All previously-passing tests still pass.
- `--sections foo` returns a friendly error listing valid section names.

**Test plan:**
- `test_sections_filter_skips_other_fetches` — assert `api.get_body_composition` is never called when `--sections sleep`.
- `test_sections_filter_writes_correct_toc` — assert the file TOC lists only the chosen sections.
- `test_sections_invalid_value_raises` — assert clean error.
- `test_sections_default_is_all_sections` — backward-compat regression test.

---

#### **GLE-3 — Reuse cached static sections**
- **Priority:** P1
- **Owner:** AI agent
- **Effort:** S (0.5 day)
- **Dependencies:** GLE-1
- **Status:** To Do

**Problems addressed:** #3 (always re-fetches profile, body comp, gear, etc.).

**Scope:**
- Inspect `ExportCache.get_section` semantics; ensure profile, devices, body composition, gear, training plans, workouts, activity types are all cached as full sections (most already are, but verify).
- Add a "max-age" policy: a section cache older than N days is considered stale. N=7 for profile/gear, N=1 for body composition, N=30 for training plans, ∞ for activity types.
- Add `ExportCache.section_age(name) -> Optional[timedelta]` helper.
- Modify section exports to skip the API call and use cache when fresh.
- Log a single line per skipped section: `"Profile: using cache (3d old)"`.

**Acceptance criteria:**
- Second invocation of `garmin-export --sections profile --days 1` makes 0 API calls (after the first).
- A `profile.json` older than 7 days forces a re-fetch.
- A `body_comp.json` older than 1 day forces a re-fetch.
- Cache age is visible in the log output.

**Test plan:**
- `test_static_section_cache_hit_skips_api` — mock the API call counter, assert 0 calls on second run within TTL.
- `test_static_section_cache_expired_refetches` — advance freezegun past TTL, assert call count > 0.
- `test_section_cache_age_helper` — unit test on `section_age`.

---

#### **GLE-4 — Focus presets (`--focus sleep|recovery|training|all`)**
- **Priority:** P0
- **Owner:** AI agent
- **Effort:** S (0.5 day)
- **Dependencies:** GLE-2
- **Status:** To Do

**Problems addressed:** #1, #4, #15 (still need to know which sections to combine for a question).

**Scope:**
- Add a `--focus {name}` CLI flag as a *shortcut* that expands to a default `--sections` set.
- Built-in presets in a new `presets.py` module:
  - `sleep` → `["daily_health", "training"]` (the training section is needed for `training_readiness.sleepScore`)
  - `recovery` → `["daily_health", "training", "body_composition"]`
  - `training` → `["daily_health", "training", "activities"]`
  - `body` → `["profile", "body_composition", "trends"]`
  - `all` → every section (default)
- Presets are user-overridable: `--focus sleep --sections daily_health` → custom wins.
- Update `--help` and the SKILL.md command table.

**Acceptance criteria:**
- `uv run garmin-export --focus sleep --days 2` runs in < 10 s and < 30 API calls.
- The output file is ≤ 30 KB.
- The TOC in the file declares the preset used: `Focus preset: sleep`.
- `--focus` and explicit `--sections` are mutually exclusive (clear error if both given).

**Test plan:**
- `test_focus_sleep_resolves_to_sections` — assert the right sections are fetched.
- `test_focus_with_explicit_sections_errors` — assert mutual exclusion.
- `test_focus_unknown_value_errors` — assert friendly message.

---

#### **GLE-5 — CLI help & discovery revamp**
- **Priority:** P2
- **Owner:** AI agent
- **Effort:** XS (0.25 day)
- **Dependencies:** GLE-2, GLE-4
- **Status:** To Do

**Problem addressed:** DX — the existing `--help` is a wall of options, no examples by use case.

**Scope:**
- Reorganise `--help` output into groups: "Common queries", "Data selection", "Output control", "Troubleshooting".
- Add a `--list-presets` flag that prints available `--focus` presets with descriptions.
- Add a "Quick start" block to the epilog.

**Acceptance criteria:**
- `garmin-export --help` is < 60 lines and groups flags.
- `garmin-export --list-presets` lists sleep, recovery, training, body, all with one-line descriptions.
- Help text includes one example per preset.

**Test plan:**
- `test_help_lists_all_flags` — assert all flag strings appear in `--help` output.
- `test_list_presets_output` — assert preset names appear.

---

### Epic B — Sleep as a first-class citizen (Phase 2)
*Goal: "analyze my last night's sleep" is one command, < 10 KB output, no manual parsing.*

---

#### **GLE-6 — Sleep summary engine**
- **Priority:** P0
- **Owner:** AI agent
- **Effort:** M (1.5 days)
- **Dependencies:** GLE-1
- **Status:** ✅ Done

**Problems addressed:** #12, #13, #14, #17 (no summary, no derived fields, no API helper).

**Scope:**
- New module `summaries.py` with `build_sleep_summary(daily_health_doc, *, latest_only=True) -> dict`.
- The function:
  - Picks the most recent sleep record (wake-date = today or yesterday).
  - Computes and returns:
    - `date` (wake date), `bedtime_local`, `wake_local` (timezone-aware, ISO 8601 with offset from the user profile `timeZone`).
    - `time_in_bed_seconds`, `asleep_seconds`, `sleep_efficiency` (% asleep of time-in-bed).
    - `stages`: `{deep_seconds, light_seconds, rem_seconds, awake_seconds}` plus percentages and "optimal" flag per stage.
    - `score`: `{overall, total_duration, stress, awake_count, rem_percentage, restlessness, light_percentage, deep_percentage}` from `sleepScores`.
    - `vitals`: `{resting_hr, sleep_hr {min, avg, max}, respiration {avg, min, max}, avg_sleep_stress, hrv_avg, hrv_status, restless_moments_count, awake_count, body_battery_change}`.
    - `subscores_qualifiers`: each sleepScore field's `qualifierKey` (POOR/FAIR/GOOD/EXCELLENT) normalised to a list `[{name, value, qualifier, optimal_range}, ...]`.
    - `verdict`: short natural-language string from `sleepScoreFeedback` and `sleepScoreInsight`.
- The function is pure — no I/O, no API calls — takes the dict and returns a dict. This makes it trivially testable.

**Acceptance criteria:**
- A 2-day-old fixture with the 2026-06-05 record returns a summary with all fields populated.
- `verdict` correctly maps `NEGATIVE_LONG_BUT_RESTLESS` → `"Long but restless"` and `NEGATIVE_STRESSFUL_DAY` → `"Stressful day"`.
- Stage percentages match manual calculation.
- Returns `None` (or raises a clear `NoSleepDataError`) when no sleep record exists.
- Time conversion correctly uses the user profile's `timeZone` (default to UTC if missing).

**Test plan:**
- `test_summary_computes_efficiency` — given 9h 3m asleep / 9h 35m in bed → ~94.6 %.
- `test_summary_extracts_verdict_phrases` — parametrised over known feedback keys.
- `test_summary_handles_no_sleep_data` — returns None.
- `test_summary_converts_to_local_time` — fixture with `timeZone: Asia/Kolkata`, GMT timestamps → correct IST strings.
- `test_summary_hrv_status_mapping` — UNBALANCED, BALANCED, POOR all pass through.

---

#### **GLE-7 — `--last-sleep` flag**
- **Priority:** P0
- **Owner:** AI agent
- **Effort:** S (0.5 day)
- **Dependencies:** GLE-2, GLE-3, GLE-6
- **Status:** ✅ Done

**Problems addressed:** #1, #2, #15, #16, #17 (the headline feature).

**Scope:**
- New CLI flag `--last-sleep` that:
  - Sets `days=2` (yesterday + today, enough to capture last night).
  - Sets `sections=daily_health` only.
  - After fetching, calls `build_sleep_summary` and writes a small, dedicated file.
  - Output file is **< 10 KB** and contains: a human-readable summary block + the raw `dailySleepDTO` JSON for completeness.
- Output filename: `garmin_last_sleep_YYYY-MM-DD_HHMMSS.txt`.
- Default output format is **plain prose, not JSON**, with:
  - A header line: `# Last Night's Sleep — 2026-06-05`
  - Bullet list of headline metrics.
  - A "Sub-scores" table.
  - A "Verdict" paragraph.
  - A "Raw data" appendix containing the original `dailySleepDTO` JSON.

**Acceptance criteria:**
- `uv run garmin-export --last-sleep` produces a file < 10 KB.
- API call count ≤ 13 (one day of daily health endpoints, of which sleep is the relevant one; everything else is cache-hit after first run).
- The file is fully viewable in a single `read` call (≤ 200 lines, ≤ 10 KB).
- The first 40 lines alone are sufficient to answer "how was my sleep" without any LLM-side parsing.

**Test plan:**
- `test_last_sleep_writes_small_file` — assert file < 10 KB.
- `test_last_sleep_uses_sleep_summary_engine` — assert the file contains the verdict phrase.
- `test_last_sleep_api_calls_bounded` — assert mocked call count ≤ 30.

---

#### **GLE-8 — `garmin-sleep` subcommand**
- **Priority:** P1
- **Owner:** AI agent
- **Effort:** S (0.5 day)
- **Dependencies:** GLE-7
- **Status:** ✅ Done

**Problem addressed:** Discoverability & ergonomics — many users will type the word "sleep" first.

**Scope:**
- Register a second console script in `pyproject.toml`: `garmin-sleep = "garmin_llm_export.sleep_cli:main"`.
- New thin module `sleep_cli.py` that wraps `main(["--last-sleep"])`.
- Adds a `--days N` flag (default 1, meaning "last night"; >1 means "last N nights") that triggers the summary for each of the last N days as a multi-section file.

**Acceptance criteria:**
- `uv run garmin-sleep` works and is equivalent to `garmin-export --last-sleep`.
- `uv run garmin-sleep --days 7` produces a 7-night file with per-night summaries and a 7-night trend block.
- The new script is documented in README.

**Test plan:**
- `test_sleep_cli_default_equals_last_sleep` — output equality.
- `test_sleep_cli_days_n_writes_n_summaries` — assert N summary headers.

---

#### **GLE-9 — Derived fields in Daily Health export**
- **Priority:** P1
- **Owner:** AI agent
- **Effort:** M (1 day)
- **Dependencies:** GLE-6
- **Status:** ✅ Done

**Problem addressed:** #13 (no derived fields anywhere in the export).

**Scope:**
- In compact mode, when the Daily Health payload is written, augment each day's sleep record with a `_summary` sub-object containing the GLE-6 summary output.
- Same for HRV (add `weekly_avg`, `baseline_low`, `baseline_high`, `status`).
- Same for Body Battery (add `morning_charge_delta`).
- Keep raw data intact — derived fields are additive, prefixed with `_` to mark them as computed.

**Acceptance criteria:**
- For any day with sleep data, the JSON contains a `_sleep_summary` key.
- The summary object matches what `build_sleep_summary` returns for that day.
- The full-mode export is unchanged (no `_summary` keys).

**Test plan:**
- `test_daily_health_includes_sleep_summary_in_compact` — assert key present in compact mode.
- `test_daily_health_full_mode_unchanged` — regression test.

---

#### **GLE-10 — Python API: `get_latest_sleep_summary()`**
- **Priority:** P1
- **Owner:** AI agent
- **Effort:** XS (0.25 day)
- **Dependencies:** GLE-6
- **Status:** ✅ Done

**Problem addressed:** #16 (no Python shortcut).

**Scope:**
- In `__init__.py`, expose `from .summaries import build_sleep_summary`.
- Add a convenience wrapper `get_latest_sleep_summary(api, *, tz: str | None = None) -> dict | None` that:
  - Detects the user's timezone (from profile or `tz` arg).
  - Fetches yesterday and today's `dailySleepDTO` (≤ 2 calls).
  - Returns the summary, or `None` if no sleep.

**Acceptance criteria:**
- A 5-line Python snippet can produce a sleep summary.
- The function is documented in README under "Python API".

**Test plan:**
- `test_get_latest_sleep_summary_returns_dict` — happy path with mock.
- `test_get_latest_sleep_summary_no_data` — returns None.

---

#### **GLE-11 — LLM-readable line sizing**
- **Priority:** P0
- **Owner:** AI agent
- **Effort:** S (0.5 day)
- **Dependencies:** GLE-1
- **Status:** ✅ Done

**Problems addressed:** #5, #6, #10 (giant single lines defeat grep + read tool).

**Scope:**
- New formatter config: `settings.line_budget = 2000` (max chars per output line in JSON).
- New `formatters.wrap_json(data, *, max_line=2000, indent=1)` that pretty-prints but breaks long arrays of objects into multiple lines.
- The big offenders (Daily Health, Activities) get line-wrapped automatically when the rendered line exceeds the budget.
- Tables of contents gain a `line_range` for each section (e.g., `1. Daily Health — lines 56–1432`) so a downstream agent can `read --offset=56 --limit=200`.
- Each `###` section header is followed by an explicit `lines: 56-120` comment line.

**Acceptance criteria:**
- No line in any exported file exceeds `settings.line_budget` (default 2000 chars).
- Each section's TOC entry includes its byte range.
- A agent (or human) can locate any section in < 2 steps: read TOC, then `read --offset=... --limit=...`.

**Test plan:**
- `test_no_line_exceeds_budget` — assert `max(len(line) for line in output) ≤ 2000`.
- `test_toc_includes_line_ranges` — assert each entry has a range.
- `test_wrap_json_handles_nested_arrays` — formatter unit test.

---

#### **GLE-12 — Restructure Sleep section in full export**
- **Priority:** P1
- **Owner:** AI agent
- **Effort:** S (0.5 day)
- **Dependencies:** GLE-6
- **Status:** ✅ Done

**Problem addressed:** #11, #14 (no "Sleep Summary" section, no derived headline numbers).

**Scope:**
- In non-compact mode, after the per-day Sleep sub-sections, add a new "Sleep Summary" section that calls `build_sleep_summary` for each day that has sleep data and prints a 3-5 line prose block per day.
- In compact mode, the GLE-9 derived fields cover this; no separate section needed.

**Acceptance criteria:**
- Full-mode export contains a "Sleep Summaries" section after Daily Health.
- Each summary is ≤ 5 lines.
- The section can be skipped via `--no-sleep-summary` (default: on).

**Test plan:**
- `test_full_mode_has_sleep_summary_section` — assert section present.
- `test_skip_sleep_summary_flag` — assert section absent when off.

---

### Epic C — Polish (Phase 3)
*Goal: close the remaining quality-of-life gaps and ship the docs.*

---

#### **GLE-13 — Local timestamp formatting**
- **Priority:** P1
- **Owner:** AI agent
- **Effort:** S (0.5 day)
- **Dependencies:** GLE-11
- **Status:** To Do

**Problem addressed:** #9 (timestamps in millisecond GMT only).

**Scope:**
- New helper `formatters.add_local_timestamps(payload, tz: str) -> payload` that:
  - For each known timestamp field (`sleepStartTimestampGMT`, `sleepEndTimestampGMT`, `startGMT`, `endGMT`, `startTimestampGMT`, etc.), adds a sibling `_local` field with ISO 8601 in the user's tz.
  - Original fields are preserved.
- Called once per section in compact mode, on the post-stripped payload.
- Document the list of recognised timestamp field names in a module constant `TIMESTAMP_FIELDS_GMT` for future maintenance.

**Acceptance criteria:**
- A user in `Asia/Kolkata` sees `2026-06-05T11:41:00+05:30` next to `1780552920000`.
- Original fields are not removed or renamed.
- A configurable flag `--no-local-time` opts out.

**Test plan:**
- `test_local_time_added_for_sleep_window` — assert `_local` key present.
- `test_local_time_uses_profile_timezone` — assert offset matches `Asia/Kolkata` (+05:30).
- `test_local_time_disabled_by_flag` — assert absent.

---

#### **GLE-14 — Schema/key consistency**
- **Priority:** P2
- **Owner:** AI agent
- **Effort:** XS (0.25 day)
- **Dependencies:** GLE-9
- **Status:** To Do

**Problem addressed:** #7, #8 (schema says `sleep` lowercase, actual key is `Sleep`; `sleepScores` substructure undocumented).

**Scope:**
- Update the schema description in `export_daily_health` to list keys as they actually appear in the JSON: `"Daily Summary, Heart Rate, Resting Heart Rate, Sleep (note: capital S), ..."`.
- Add a per-section "Key map" appendix to the file header that documents nested structures, e.g.:
  - `Sleep.dailySleepDTO.sleepScores.{totalDuration,stress,awakeCount,remPercentage,restlessness,lightPercentage,deepPercentage,overall}`.
- Add the same key-map inline in the GLE-6 summary docstring.

**Acceptance criteria:**
- The schema string in the output file matches the actual key names.
- A new "Key map" block is at the top of each file (or a `_index.json` sibling — see GLE-15).
- No more "key not found" surprises for downstream agents.

**Test plan:**
- `test_schema_string_lists_capital_sleep` — assert the literal string `"Sleep"` appears.

---

#### **GLE-15 — Output index file**
- **Priority:** P2
- **Owner:** AI agent
- **Effort:** S (0.5 day)
- **Dependencies:** GLE-11, GLE-14
- **Status:** To Do

**Problem addressed:** #11, #14 (no machine-readable way to navigate the file).

**Scope:**
- For every export, write a sibling `garmin_export_*.index.json` containing:
  - `file`: relative path
  - `version`: exporter version
  - `exported_at`: ISO timestamp
  - `date_range`: {start, end, days}
  - `sections`: list of `{name, line_start, line_end, byte_start, byte_end, schema, key_map}`.
  - `pre_computed`: optional, list of derived summaries (e.g., latest sleep summary).
- When `--split` is used, the index references all parts.
- The `garmin-sleep` command skips the index (output is small enough).

**Acceptance criteria:**
- An index file is always written alongside the main export.
- A LLM agent can `read` only the index (small) to know where to look in the large file.
- Index validates as JSON in 100 % of cases.

**Test plan:**
- `test_index_written_alongside_export` — assert sibling file exists.
- `test_index_line_ranges_match_actual_file` — assert byte offsets correspond to the actual section boundaries.

---

#### **GLE-16 — README & SKILL.md update**
- **Priority:** P1
- **Owner:** AI agent
- **Effort:** S (0.5 day)
- **Dependencies:** GLE-4, GLE-7, GLE-8, GLE-10
- **Status:** To Do

**Problem addressed:** Discoverability of new features.

**Scope:**
- Update README:
  - Add "Quick queries" section near the top with `--focus sleep`, `--focus recovery`, etc.
  - Add Python API example for `get_latest_sleep_summary`.
  - Update flag table.
- Update `garmin-llm-export-skill/SKILL.md`:
  - New "Quick Start" lines: `uv run garmin-sleep`, `uv run garmin-export --focus sleep`.
  - Document the "wake-date" convention explicitly.
  - Document the new `--sections` and `--focus` flags.
  - Add a "Common queries" cookbook (4-5 recipes).
- Add a CHANGELOG entry following Keep-a-Changelog format.

**Acceptance criteria:**
- README mentions `--focus` and `--last-sleep` within the first 30 lines.
- SKILL.md's "Quick Start" has 5 commands instead of 4, including the new ones.
- CHANGELOG entry exists for the new version.

**Test plan:**
- Doc lint: assert README and SKILL.md reference the new flags.
- A `docs_check.py` smoke test that greps for the new feature names.

---

## 7. Out-of-Scope / Deferred

| Idea | Reason for deferral |
|---|---|
| `--last-recovery` / `--last-activity` siblings of `--last-sleep` | Same pattern as GLE-7/8; trivial once summary engines exist. **Tracked for v0.3.0.** |
| Sleep chart rendering (matplotlib / ASCII) | Visual; LLM consumers don't need it. |
| Multi-user support | Out of project scope. |
| Query DSL (`--query "show me all runs in May"`) | Big surface area; not in this iteration. |
| Replacing the Garmin client | Library is fine; not our problem. |

---

## 8. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Schema drift in the Garmin API breaks summary logic | Medium | Medium | Pin summary engine to fixture data; add a contract test per derived field. |
| Output structure change breaks existing NotebookLM users | Low | High | All existing flags produce byte-identical output (GLE-1 snapshot test enforces this). |
| `--sections sleep` accidentally excludes training readiness | Low | Medium | The `sleep` preset includes training for a reason; document it in `--list-presets`. |
| Line-wrapping changes byte offsets in `_index.json` | Low | Low | Index is generated from the same writer that wraps; no drift possible. |
| Larger output with `_local` fields regresses file size for `--all` | Medium | Low | `_local` is opt-in (`--no-local-time` default for `--all --split`); add a size assertion test. |

---

## 9. Rollout Plan

1. **Phase 1 (GLE-1 → GLE-5):** land on a feature branch. CI green. No user-facing changes yet.
2. **Phase 2 (GLE-6 → GLE-12):** land as a single PR, versioned as `0.2.0-alpha`. Document in CHANGELOG.
3. **Phase 3 (GLE-13 → GLE-16):** land as `0.2.0`. Bump README, tag release.
4. **Smoke test on a real account** (this repo's owner) before tagging 0.2.0.
5. **Update the SKILL.md skill** in `garmin-llm-export-skill/` to advertise the new commands.

---

## 10. Definition of Done (per task)

A task is "Done" when:
- Code is merged to `main`.
- All acceptance criteria pass.
- New tests are added and pass.
- Coverage does not decrease.
- A short note is added to CHANGELOG.
- A maintainer (you) has reviewed the diff and given a thumbs-up.

A phase is "Done" when:
- All its tasks are Done.
- `uv run pytest` is green.
- The end-to-end test (`test_e2e_real_account`) on the maintainer's account produces a sleep summary file < 10 KB.
- The CHANGELOG is bumped.

---

## 11. Appendix — Mapping from friction list to tasks

| Friction list # | Issue | Task(s) |
|---|---|---|
| 1 | No sleep-specific mode | GLE-4, GLE-7, GLE-8 |
| 2 | `--compact` still bloated | GLE-11, GLE-13 |
| 3 | Always fetches all sections | GLE-2, GLE-3 |
| 4 | No incremental section control | GLE-2 |
| 5 | Single-line JSON | GLE-11 |
| 6 | Grep useless | GLE-11, GLE-15 |
| 7 | Key naming vs schema mismatch | GLE-14 |
| 8 | sleepScores undocumented | GLE-14, GLE-6 |
| 9 | GMT-only timestamps | GLE-13 |
| 10 | No line ranges in TOC | GLE-11, GLE-15 |
| 11 | No "Sleep Summary" section | GLE-6, GLE-12 |
| 12 | No summary engine | GLE-6 |
| 13 | No derived fields | GLE-6, GLE-9 |
| 14 | "Human-readable" isn't | GLE-11, GLE-12, GLE-15 |
| 15 | 282 API calls for one night | GLE-2, GLE-3, GLE-4, GLE-7 |
| 16 | No Python API helper | GLE-10 |
| 17 | Multi-step workflow | GLE-7, GLE-8 |

**Coverage:** 17 / 17 issues addressed across 16 tasks.

---

*End of plan — awaiting review.*
