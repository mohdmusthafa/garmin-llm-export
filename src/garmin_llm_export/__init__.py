"""Export Garmin Connect data as LLM-readable plain text with raw JSON."""

__version__ = "0.2.0-alpha"

from .auth import load_env, login
from .cache import SECTION_MAX_AGE_DAYS, ExportCache
from .config import ExportSettings, settings
from .exporter import GarminExporter, SECTION_REGISTRY
from .presets import FOCUS_PRESETS, expand_focus
from .summaries import build_sleep_summary, get_latest_sleep_summary

__all__ = [
    "__version__",
    "ExportCache",
    "ExportSettings",
    "FOCUS_PRESETS",
    "GarminExporter",
    "SECTION_MAX_AGE_DAYS",
    "SECTION_REGISTRY",
    "build_sleep_summary",
    "expand_focus",
    "get_latest_sleep_summary",
    "load_env",
    "login",
    "settings",
]
