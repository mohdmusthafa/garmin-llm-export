---
name: garmin-export
description: Export Garmin Connect health and fitness data as plain text for LLM analysis. Use when users want to export their Garmin health data (steps, heart rate, sleep, activities, training metrics, body composition, goals, etc.) for analysis in LLM tools like NotebookLM, Claude, or ChatGPT. Handles authentication via Garmin SSO, resumable exports, incremental updates, and split files for NotebookLM's 500K word limit. Relevant when users mention Garmin, health tracking, fitness data export, wearable data, NotebookLM, or personal health analytics.
---

# Garmin LLM Export

Export Garmin Connect health and fitness data as plain text files optimized for LLM tools. No API key required — uses Garmin's website authentication.

## Quick Start

```bash
# 1. Setup: create .env with credentials
cat > garmin-llm-export/.env << 'EOF'
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=your-password
EOF

# 2. Login once to cache tokens (~1 year validity)
uv run garmin-export --login

# 3. Basic export (last 30 days, 100 activities)
uv run garmin-export

# 4. For NotebookLM: full history split into manageable files
uv run garmin-export --all --split
```

## Core Commands

| Command | When to Use |
|---------|-------------|
| `uv run garmin-export --login` | First-time setup only |
| `uv run garmin-export` | Last 30 days, 100 activities |
| `uv run garmin-export --days 7 --compact` | Last week, smaller output |
| `uv run garmin-export --all` | Complete history |
| `uv run garmin-export --all --split` | Full history for NotebookLM |
| `uv run garmin-export --update` | Incremental update since last export |

## All CLI Options

```bash
uv run garmin-export [OPTIONS]

Options:
  --login                   Authenticate only, cache tokens, exit
  --days N                  Days of daily health data (default: 30)
  --activities N            Max activities to export (default: 100)
  --all                     Export complete history (ignores --days/--activities)
  --compact                 Smaller output: strip nulls, downsample time-series
  --split                   Split into <500K word files (implies --compact)
  --update                  Export only new data since last export (implies --compact)
  --output DIR              Output directory (default: export/)
  --no-cache                Force full re-fetch, ignore resume cache
  --delay SEC               Base delay between API calls (default: 0.15)
  -v, --verbose             Debug logging
```

## Output Format

The exporter writes `.txt` files with:
- **Table of Contents** at the top for AI navigation
- **Plain text headers** + **raw JSON** (no markdown code fences — optimal for RAG)
- **LLM-ready structure**: section headers are parseable, JSON is clean

### Compact Mode Differences
- Null fields stripped
- Activity time-series omitted
- Daily health downsampled to hourly
- Single-line JSON blocks per section
- Significantly smaller file sizes

### Split Mode
- Auto-splits into files under NotebookLM's 500K word limit
- Each file has a header listing its sections
- Upload all parts to the same notebook

## Data Sections Exported

| Section | Contents |
|---------|----------|
| **Profile** | User info, devices, settings, activity types |
| **Daily Health** | Steps, HR, sleep, stress, body battery, SpO₂, HRV, respiration, intensity |
| **Activities** | Summaries, splits, HR/power zones, weather, time-series |
| **Body Composition** | Weight, BMI, body fat, muscle/bone mass |
| **Training Metrics** | VO₂ max, readiness, FTP, hill/endurance scores, race predictions |
| **Goals and Records** | Personal records, badges, goals |
| **Trends** | Weekly/daily aggregates, progress |
| **Golf** | Round summaries, scorecards, shot data |
| **Gear** | Equipment list, per-item stats |
| **Workouts** | Saved workout definitions |
| **Training Plans** | Active and past plans |
| **Hydration** | Per-day fluid intake |
| **Nutrition** | Per-day food logs, meals |
| **Women's Health** | Menstrual calendar, pregnancy |

## Efficient Usage Patterns

### First Export (Full History)
```bash
# Recommended for NotebookLM upload
uv run garmin-export --all --split

# Or for smaller outputs with an LLM
uv run garmin-export --all --compact
```

### Regular Updates
```bash
# After initial --all export, use --update to get only new data
uv run garmin-export --update

# The update file goes alongside your base export files
# Upload both to NotebookLM for complete coverage
```

### Resumable Exports
The tool automatically caches fetched data. If interrupted:
```bash
# Just re-run — it picks up where it left off
uv run garmin-export --all --split
```

### Rate Limiting
Garmin's API may return 429 errors. To slow down:
```bash
uv run garmin-export --all --delay 0.5  # Increase from default 0.15s
```

## Troubleshooting

### "Rate limited (429)"
Wait 10-15 minutes, then retry with increased delay:
```bash
uv run garmin-export --update --delay 0.5
```

### "Authentication failed"
Re-run login:
```bash
uv run garmin-export --login
```

### Token refresh
Tokens cache at `~/.garminconnect/` for ~1 year. To force re-auth:
```bash
rm -rf ~/.garminconnect/
uv run garmin-export --login
```

### Large exports taking too long
Use `--days` to limit scope:
```bash
uv run garmin-export --days 90 --activities 200 --compact
```

## Recommended LLM Workflow

1. **Initial export**: `uv run garmin-export --all --split`
2. Upload all `*_part*.txt` files to one NotebookLM notebook
3. **Periodic updates**: `uv run garmin-export --update`
4. Upload the `_update.txt` file alongside your base files

## Security Notes

- Never commit `.env`, `export/`, or `~/.garminconnect/` (all in .gitignore)
- Tokens are stored locally and typically valid for ~1 year
- Health data files contain sensitive personal information

## Python API

```python
from garmin_llm_export import load_env, login, ExportCache, GarminExporter
from pathlib import Path

load_env()
api = login(Path("~/.garminconnect"))
cache = ExportCache(Path("export"), enabled=True)
exporter = GarminExporter(api, Path("export"), days=30, max_activities=100)
exporter.run()
```