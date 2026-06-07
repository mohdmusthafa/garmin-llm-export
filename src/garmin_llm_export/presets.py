"""Built-in section presets for the --focus CLI flag.

A preset is a short alias for a particular combination of section IDs. They
are shortcuts for common queries (sleep, recovery, training, body) and exist
to make the CLI easier to use without forcing the user to memorise the full
list of section identifiers used by --sections.

A preset is mutually exclusive with --sections: exactly one of the two must
be supplied (the default is the "all" preset).
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Canonical section identifiers.
#
# These are the strings users will type on the command line. They map 1:1 to
# the section keys used in the exporter's section registry. Display names and
# descriptions live alongside the registry in exporter.GarminExporter, not
# here -- presets only know about which *ids* belong together.
# ---------------------------------------------------------------------------
ALL_SECTION_IDS: Tuple[str, ...] = (
    "profile",
    "daily_health",
    "activities",
    "body_composition",
    "training",
    "goals",
    "trends",
    "golf",
    "gear",
    "training_plans",
    "workouts",
    "hydration",
    "nutrition",
    "womens_health",
)


# ---------------------------------------------------------------------------
# Focus presets
#
# `sleep` includes training because the training_readiness subscore is
# derived from sleep and is the most informative single number after the
# sleep score itself. If a user complains "my --focus sleep output is
# missing readiness", that is by design.
# ---------------------------------------------------------------------------
FOCUS_PRESETS: Dict[str, Tuple[str, ...]] = {
    "sleep": ("daily_health", "training"),
    "recovery": ("daily_health", "training", "body_composition"),
    "training": ("daily_health", "training", "activities"),
    "body": ("profile", "body_composition", "trends"),
    "all": ALL_SECTION_IDS,
}


# One-line description per preset, used by --list-presets and the help text.
FOCUS_PRESET_DESCRIPTIONS: Dict[str, str] = {
    "sleep": "Daily Health + Training (sleep score, HRV, training readiness)",
    "recovery": "Sleep + Training + Body Composition (full recovery picture)",
    "training": "Daily Health + Training + Activities (training load + workouts)",
    "body": "Profile + Body Composition + Trends (long-term body data)",
    "all": "Every section (the default)",
}


def expand_focus(name: str) -> Tuple[str, ...]:
    """Resolve a --focus value to a tuple of section IDs.

    Raises:
        ValueError: if `name` is not a known preset.
    """
    if name not in FOCUS_PRESETS:
        valid = ", ".join(FOCUS_PRESETS.keys())
        raise ValueError(
            f"Unknown focus preset '{name}'. Valid presets: {valid}"
        )
    return FOCUS_PRESETS[name]


def list_presets() -> List[Tuple[str, str]]:
    """Return [(name, description), ...] in the order they should be listed."""
    return [(name, FOCUS_PRESET_DESCRIPTIONS[name]) for name in FOCUS_PRESETS]
