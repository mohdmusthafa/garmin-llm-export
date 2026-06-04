"""Export runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExportSettings:
    """Flags set by the CLI before export runs."""

    compact: bool = False
    split: bool = False
    update: bool = False
    split_word_limit: int = 480_000  # NotebookLM 500K word limit with margin


settings = ExportSettings()

DEFAULT_TOKENSTORE = "~/.garminconnect"
