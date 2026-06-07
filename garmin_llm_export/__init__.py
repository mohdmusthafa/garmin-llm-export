"""Export Garmin Connect data as LLM-readable plain text with raw JSON."""

__version__ = "0.1.0"

from .auth import load_env, login
from .cache import SECTION_MAX_AGE_DAYS, ExportCache
from .config import ExportSettings, settings
from .exporter import GarminExporter, SECTION_REGISTRY
from .presets import FOCUS_PRESETS, expand_focus

__all__ = [
    "__version__",
    "ExportCache",
    "ExportSettings",
    "FOCUS_PRESETS",
    "GarminExporter",
    "SECTION_MAX_AGE_DAYS",
    "SECTION_REGISTRY",
    "expand_focus",
    "load_env",
    "login",
    "settings",
]
