# Garmin LLM Export

Export your [Garmin Connect](https://connect.garmin.com) health and fitness data as plain text files with raw JSON — built for LLM tools like [NotebookLM](https://notebooklm.google.com), ChatGPT, and Claude.

No Garmin developer API key required. Authenticates through Garmin SSO using [python-garminconnect](https://github.com/cyberjunky/python-garminconnect).

## Why plain text?

LLM tools index plain `.txt` files reliably. Markdown code fences and `.md` parsing quirks (especially in NotebookLM) can cause JSON blocks to be skipped. This exporter writes section headers plus raw JSON — no markdown, no code fences — so RAG systems can retrieve your actual numbers.

## Features

- **Complete data export** — profile, daily health, activities, body composition, training metrics, goals, trends, golf, gear, workouts, hydration, nutrition, and more
- **LLM-ready format** — plain `.txt` with raw JSON blocks, table of contents with line ranges for AI navigation
- **Compact mode** — strip nulls, downsample time-series, significantly smaller files
- **Split mode** — auto-split into files under NotebookLM's 500K word limit
- **Resumable cache** — interrupted `--all` exports pick up where they left off
- **Incremental updates** — `--update` fetches only new data since your last export
- **Adaptive rate limiting** — paces API calls and backs off on 429 responses
- **Focus presets** — `--focus sleep|recovery|training|body` for targeted exports
- **Sleep summary** — `--last-sleep` for plain-prose last-night sleep summary
- **Section filter** — `--sections daily_health,training` to export only what you need
- **Local timestamps** — `_local` ISO-8601 siblings added to GMT millisecond fields
- **Index file** — sibling `.index.json` with line/byte ranges for fast navigation

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

```bash
git clone <your-repo-url>
cd garmin-llm-export
uv sync
```

Or install with pip after cloning:

```bash
pip install -e .
```

## Setup

Create a `.env` file in the project root:

```env
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=your-password
```

Log in once — tokens are cached at `~/.garminconnect/` for about a year:

```bash
uv run garmin-export --login
```

## Quick Queries

Targeted exports for specific health questions — no full export needed:

| Command | What it exports |
|---------|-----------------|
| `uv run garmin-export --focus sleep --days 7` | Last week's sleep data |
| `uv run garmin-export --focus recovery --days 7` | Recovery, HRV, readiness |
| `uv run garmin-export --focus training --days 14` | Training load, VO₂ max, race preds |
| `uv run garmin-export --focus body --days 30` | Weight, body composition |
| `uv run garmin-export --last-sleep` | Plain-prose last night's sleep summary |

Available presets: `sleep`, `recovery`, `training`, `body`, `all`. Use `--list-presets` to see all.

`uv run garmin-sleep` is a shorthand for `--last-sleep` (outputs plain-prose to stdout).

## Usage

```bash
# Last 30 days, 100 activities (defaults)
uv run garmin-export

# Last 7 days, compact for LLM upload
uv run garmin-export --days 7 --compact

# Full history
uv run garmin-export --all

# Full history, split for NotebookLM
uv run garmin-export --all --split

# Incremental update since last export
uv run garmin-export --update

# Export only specific sections
uv run garmin-export --sections daily_health,training --days 7
```

### All CLI Options

| Flag | Description |
|------|-------------|
| `--login` | Authenticate and cache tokens, then exit |
| `--days N` | Days of daily health data (default: 30) |
| `--activities N` | Max activities to export (default: 100) |
| `--all` | Export complete history (ignores `--days` / `--activities` limits) |
| `--sections ID[,ID...]` | Export only named sections (e.g. `daily_health,training`) |
| `--focus PRESET` | Export only sections matching preset (sleep / recovery / training / body / all) |
| `--compact` | Smaller output: strip nulls, downsample time-series |
| `--split` | Split into <500K word files (implies `--compact`) |
| `--update` | Export only new data since last export (implies `--compact`) |
| `--output DIR` | Output directory (default: `export`) |
| `--no-cache` | Force full re-fetch, ignore cache |
| `--no-local-time` | Skip local timestamp conversion |
| `--no-sleep-summary` | Omit the Sleep Summaries section from full export |
| `--delay SEC` | Base delay between API calls (default: 0.15) |
| `-v, --verbose` | Debug logging |
| `--list-presets` | List all available focus presets and exit |
| `--list-sections` | List all available sections and exit |
| `--last-sleep` | Write plain-prose last-night sleep summary to stdout and exit |

Also runnable as a module:

```bash
uv run python -m src.garmin_llm_export --days 7 --compact
```

Output files: `export/garmin_export_YYYY-MM-DD_HHMMSS.txt` (or `_compact`, `_split_partNofM`, `_update` suffixes as applicable).

A sibling `.index.json` file is written alongside each export for machine-readable navigation.

## What gets exported

| Section | Contents |
|---------|----------|
| Profile | User info, devices, settings, activity types |
| Daily Health | Steps, HR, sleep, stress, body battery, SpO₂, HRV, respiration, intensity |
| Activities | Summaries, splits, zones, weather, time-series |
| Body Composition | Weight, BMI, body fat, weigh-ins |
| Training Metrics | VO₂ max, readiness, FTP, hill/endurance scores |
| Goals& Records | Personal records, badges, goals |
| Trends | Weekly/daily aggregates, progress |
| Hydration / Nutrition | Per-day logs |
| Golf, Gear, Workouts, Training Plans, Women's Health | When available on your account |

Empty sections are marked `No data available.` rather than omitted.

In full-mode exports, a **Sleep Summaries** section provides per-night prose summaries after the raw JSON.

## Recommended LLM workflow

1. **Base export:** `uv run garmin-export --all --split`
2. Upload all `*_part*.txt` files to one NotebookLM notebook
3. **Keep fresh:** run `uv run garmin-export --update` periodically and upload the `_update.txt` alongside your base files

## Project structure

```
garmin-llm-export/
├── src/garmin_llm_export/
│   ├── __init__.py     # Package init, exports public API
│   ├── __main__.py     # python -m entry point
│   ├── auth.py         # .env credentials + OAuth token cache
│   ├── cache.py        # Resumable JSON cache (per-day, per-activity, per-section)
│   ├── cli.py          # garmin-export CLI entry point
│   ├── config.py       # Export runtime settings
│   ├── exporter.py     # Data fetching and file writing
│   ├── formatters.py   # JSON compaction, line sizing, local timestamps
│   ├── last_sleep.py   # --last-sleep plain-prose writer
│   ├── presets.py      # --focus preset definitions
│   ├── rate_limit.py   # Thread-safe adaptive API pacing
│   ├── sleep_cli.py    # garmin-sleep CLI entry point
│   └── summaries.py   # Sleep summary engine + get_latest_sleep_summary
├── tests/              # 210 tests, pytest-based
├── SKILL.md            # Claude skill definition (in repo root)
├── pyproject.toml
├── README.md
└── CHANGELOG.md
```

## Python API

```python
from pathlib import Path
from garmin_llm_export import ExportCache, GarminExporter, load_env, login
from garmin_llm_export.config import settings

load_env()
api = login(Path("~/.garminconnect"))

# Full export (last 30 days)
settings.compact = True
cache = ExportCache(Path("export"), enabled=True)
GarminExporter(api, Path("export"), days=7, max_activities=50).run()

# Quick: get last night's sleep as a dict
from garmin_llm_export import get_latest_sleep_summary
summary = get_latest_sleep_summary(api)
print(summary)
```

## Security

Never commit:

- `.env` (credentials)
- `export/` (your health data)
- `.garminconnect/` (auth tokens)

All three are listed in `.gitignore`.

## License

Apache 2.0 — see [LICENSE](LICENSE).
