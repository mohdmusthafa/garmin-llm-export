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
    # Maximum line length, in characters, for any JSON in the output (GLE-11).
    # Lines longer than this defeat `grep` and the `read` tool of LLM agents.
    # 2000 chars is a comfortable bound for human eyes and most agents.
    line_budget: int = 2000
    # Add _local ISO-8601 siblings to recognised GMT timestamp fields (GLE-13).
    # True by default; set to False with --no-local-time.
    local_time: bool = True


settings = ExportSettings()

DEFAULT_TOKENSTORE = "~/.garminconnect"
