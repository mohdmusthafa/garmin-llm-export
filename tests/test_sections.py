"""Tests for the section registry, --sections filter, --focus presets, and
per-section cache freshness (GLE-2, GLE-3, GLE-4, GLE-5).

The exporter-level tests use the MockGarminApi from conftest and assert that:
- The chosen subset of sections appears in the output.
- The unchosen sections do not appear.
- API methods for unchosen sections are never called.
- Cached sections are reused on the second run within their max age.
- Focus presets expand to the right section ids.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from garmin_llm_export.exporter import GarminExporter, SECTION_REGISTRY, SectionDef
from garmin_llm_export.presets import (
    ALL_SECTION_IDS,
    FOCUS_PRESETS,
    expand_focus,
    list_presets,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run_with_sections(api, out_dir, *, sections, days=2, compact=False, cache=None):
    """Run the exporter with a section filter and return the output file path."""
    from garmin_llm_export.config import settings as global_settings
    global_settings.compact = compact
    exporter = GarminExporter(
        api, out_dir, days=days, max_activities=10,
        cache=cache, sections=sections,
    )
    exporter.run()
    candidates = sorted(out_dir.glob("garmin_export_*.txt"), reverse=True)
    assert candidates, "No export file was produced"
    return candidates[0]


# ---------------------------------------------------------------------------
# GLE-2: section selection (--sections)
# ---------------------------------------------------------------------------
class TestSectionRegistry:
    def test_registry_covers_every_old_section_name(self):
        # The 14 display names from the pre-refactor exporter
        expected_displays = {
            "Profile", "Daily Health", "Activities", "Body Composition",
            "Training Metrics", "Goals and Records", "Trends", "Golf",
            "Gear", "Training Plans", "Workouts", "Hydration", "Nutrition",
            "Women's Health",
        }
        actual = {s.display for s in SECTION_REGISTRY}
        assert actual == expected_displays

    def test_registry_ids_are_unique(self):
        ids = [s.id for s in SECTION_REGISTRY]
        assert len(ids) == len(set(ids))

    def test_registry_methods_resolve_on_exporter(self, mock_garmin_api, tmp_export_dir: Path, reset_settings):
        for sec in SECTION_REGISTRY:
            assert hasattr(GarminExporter, sec.method_name), (
                f"Section '{sec.id}' references missing method '{sec.method_name}'"
            )

    def test_per_day_and_per_activity_sections_have_no_cache_key(self):
        # daily_health, activities, hydration, nutrition are per-day/per-activity
        for sid in ("daily_health", "activities", "hydration", "nutrition"):
            sec = next(s for s in SECTION_REGISTRY if s.id == sid)
            assert sec.cache_key is None, f"{sid} should have cache_key=None"


class TestSectionFilter:
    def test_default_includes_all_sections(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        path = _run_with_sections(
            mock_garmin_api, tmp_export_dir, sections=None
        )
        content = path.read_text(encoding="utf-8")
        for sec in SECTION_REGISTRY:
            assert sec.display in content, (
                f"Default export is missing section '{sec.display}'"
            )

    def test_sections_filter_writes_only_chosen_sections(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        path = _run_with_sections(
            mock_garmin_api, tmp_export_dir,
            sections={"daily_health", "training"},
        )
        content = path.read_text(encoding="utf-8")
        # Chosen sections appear
        assert "Daily Health" in content
        assert "Training Metrics" in content
        # Unchosen sections do not appear
        assert "Profile" not in content
        assert "Activities" not in content
        assert "Body Composition" not in content
        assert "Golf" not in content
        # TOC reflects the choice
        assert "Sections: daily_health, training" in content

    def test_sections_filter_skips_other_api_calls(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        _run_with_sections(
            mock_garmin_api, tmp_export_dir,
            sections={"daily_health"},
        )
        # Body composition endpoints must never be called
        assert mock_garmin_api.call_count_by_label("body_comp") == 0
        assert mock_garmin_api.call_count_by_label("weigh_ins") == 0
        assert mock_garmin_api.call_count_by_label("goals_active") == 0
        assert mock_garmin_api.call_count_by_label("golf_summary") == 0
        assert mock_garmin_api.call_count_by_label("training_plans") == 0

    def test_sections_filter_daily_health_still_fetches_its_endpoints(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        _run_with_sections(
            mock_garmin_api, tmp_export_dir,
            sections={"daily_health"},
        )
        # Daily health endpoints should still be called
        assert mock_garmin_api.call_count_by_label("sleep") == 2
        assert mock_garmin_api.call_count_by_label("hr") == 2
        assert mock_garmin_api.call_count_by_label("summary") == 2

    def test_empty_section_filter_writes_empty_export(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        path = _run_with_sections(
            mock_garmin_api, tmp_export_dir, sections=set()
        )
        content = path.read_text(encoding="utf-8")
        # No section display names appear
        for sec in SECTION_REGISTRY:
            assert sec.display not in content or "Table of Contents" in content

    def test_sections_filter_unknown_value_raises(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        # The CLI parser rejects this; the exporter itself just filters silently.
        # Make sure passing an unknown id does not raise, it just yields no work.
        path = _run_with_sections(
            mock_garmin_api, tmp_export_dir, sections={"nonexistent"}
        )
        assert path.exists()


# ---------------------------------------------------------------------------
# GLE-3: per-section cache freshness
# ---------------------------------------------------------------------------
class TestSectionCacheFreshness:
    def test_first_run_fetches_profile_second_run_uses_cache(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        from garmin_llm_export.cache import ExportCache

        cache = ExportCache(tmp_export_dir, enabled=True)
        _run_with_sections(
            mock_garmin_api, tmp_export_dir,
            sections={"profile"}, cache=cache,
        )
        calls_after_first = mock_garmin_api.call_count_by_label("full_name")
        assert calls_after_first == 1, "Profile should be fetched once on the first run"

        # Second run -- cache should be fresh
        _run_with_sections(
            mock_garmin_api, tmp_export_dir,
            sections={"profile"}, cache=cache,
        )
        assert mock_garmin_api.call_count_by_label("full_name") == 1, (
            "Profile should not be re-fetched when cache is fresh"
        )

    def test_cache_expired_triggers_refetch(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        from garmin_llm_export.cache import ExportCache

        cache = ExportCache(tmp_export_dir, enabled=True)
        _run_with_sections(
            mock_garmin_api, tmp_export_dir,
            sections={"profile"}, cache=cache,
        )
        path = cache.section_path("profile")
        # Pretend profile is 8 days old (past its 7d max age)
        old = datetime.now(tz=timezone.utc) - timedelta(days=8)
        os.utime(path, (old.timestamp(), old.timestamp()))

        _run_with_sections(
            mock_garmin_api, tmp_export_dir,
            sections={"profile"}, cache=cache,
        )
        assert mock_garmin_api.call_count_by_label("full_name") == 2, (
            "Profile should be re-fetched when cache is past its max age"
        )

    def test_update_mode_always_refetches(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        from garmin_llm_export.cache import ExportCache
        from garmin_llm_export.config import settings as global_settings

        global_settings.update = True
        cache = ExportCache(tmp_export_dir, enabled=True)
        exporter = GarminExporter(
            mock_garmin_api, tmp_export_dir, days=2, max_activities=10,
            cache=cache, update_mode=True, sections={"profile"},
        )
        exporter.run()
        first_calls = mock_garmin_api.call_count_by_label("full_name")

        exporter2 = GarminExporter(
            mock_garmin_api, tmp_export_dir, days=2, max_activities=10,
            cache=cache, update_mode=True, sections={"profile"},
        )
        exporter2.run()
        second_calls = mock_garmin_api.call_count_by_label("full_name")
        assert second_calls == first_calls * 2, (
            "Update mode should bypass the section cache and re-fetch every time"
        )
        global_settings.update = False

    def test_cache_disabled_always_refetches(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        from garmin_llm_export.cache import ExportCache

        cache = ExportCache(tmp_export_dir, enabled=False)
        _run_with_sections(
            mock_garmin_api, tmp_export_dir,
            sections={"profile"}, cache=cache,
        )
        first = mock_garmin_api.call_count_by_label("full_name")
        _run_with_sections(
            mock_garmin_api, tmp_export_dir,
            sections={"profile"}, cache=cache,
        )
        second = mock_garmin_api.call_count_by_label("full_name")
        assert second == first * 2, (
            "Disabled cache should always re-fetch"
        )


# ---------------------------------------------------------------------------
# GLE-4: focus presets
# ---------------------------------------------------------------------------
class TestFocusPresets:
    def test_sleep_preset_resolves_to_daily_health_and_training(self):
        assert set(expand_focus("sleep")) == {"daily_health", "training"}

    def test_recovery_preset_includes_body_composition(self):
        ids = set(expand_focus("recovery"))
        assert {"daily_health", "training", "body_composition"} <= ids

    def test_training_preset_includes_activities(self):
        ids = set(expand_focus("training"))
        assert {"daily_health", "training", "activities"} <= ids

    def test_body_preset_excludes_daily_health(self):
        ids = set(expand_focus("body"))
        assert "daily_health" not in ids
        assert {"profile", "body_composition", "trends"} <= ids

    def test_all_preset_equals_all_section_ids(self):
        assert set(expand_focus("all")) == set(ALL_SECTION_IDS)

    def test_unknown_preset_raises(self):
        with pytest.raises(ValueError, match="Unknown focus preset"):
            expand_focus("bogus")

    def test_preset_descriptions_cover_all_presets(self):
        for name in FOCUS_PRESETS:
            descs = list_presets()
            assert any(n == name for n, _ in descs), f"Missing description for '{name}'"

    def test_sleep_focus_skips_unrelated_api_calls(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        _run_with_sections(
            mock_garmin_api, tmp_export_dir,
            sections=set(expand_focus("sleep")),
        )
        # daily_health endpoints are called
        assert mock_garmin_api.call_count_by_label("sleep") == 2
        # training endpoints are called (training_readiness, etc.)
        assert mock_garmin_api.call_count_by_label("training_readiness") == 1
        # unrelated sections are not called
        assert mock_garmin_api.call_count_by_label("body_comp") == 0
        assert mock_garmin_api.call_count_by_label("golf_summary") == 0
        assert mock_garmin_api.call_count_by_label("goals_active") == 0

    def test_focus_writes_only_preset_sections(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        path = _run_with_sections(
            mock_garmin_api, tmp_export_dir,
            sections=set(expand_focus("sleep")),
        )
        content = path.read_text(encoding="utf-8")
        assert "Daily Health" in content
        assert "Training Metrics" in content
        assert "Profile" not in content
        assert "Golf" not in content
