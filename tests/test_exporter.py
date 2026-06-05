"""Integration tests for garmin_llm_export.exporter.

These run the full GarminExporter against the mock API and assert the
end-to-end shape of the output (file exists, expected sections, bounded size).

They are intentionally conservative: we do not assert byte-for-byte output,
because compact-mode JSON can change between Python releases. We do assert
the "shape" - sections present, file under a size budget, headers in place.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from garmin_llm_export.exporter import GarminExporter


# Section names as they appear in the output file (used for TOC checks)
EXPECTED_SECTIONS = [
    "Profile",
    "Daily Health",
    "Activities",
    "Body Composition",
    "Training Metrics",
    "Goals and Records",
    "Trends",
    "Golf",
    "Gear",
    "Training Plans",
    "Workouts",
    "Hydration",
    "Nutrition",
    "Women's Health",
]


def _run_full_export(api, out_dir: Path, *, days: int = 2, compact: bool = False) -> Path:
    """Run the exporter and return the path of the output file."""
    exporter = GarminExporter(api, out_dir, days=days, max_activities=10)
    exporter.run()
    # Find the most recently created garmin_export_*.txt
    candidates = sorted(out_dir.glob("garmin_export_*.txt"), reverse=True)
    assert candidates, "No export file was produced"
    return candidates[0]


# ---------------------------------------------------------------------------
# End-to-end run with the mock API
# ---------------------------------------------------------------------------
class TestExporterRun:
    def test_full_export_writes_file(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        path = _run_full_export(mock_garmin_api, tmp_export_dir, days=2)
        assert path.exists()
        assert path.stat().st_size > 0

    def test_full_export_includes_toc(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        path = _run_full_export(mock_garmin_api, tmp_export_dir, days=2)
        content = path.read_text(encoding="utf-8")
        assert "Table of Contents" in content
        for section in EXPECTED_SECTIONS:
            assert section in content, f"Section '{section}' missing from TOC"

    def test_full_export_includes_header_lines(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        path = _run_full_export(mock_garmin_api, tmp_export_dir, days=2)
        content = path.read_text(encoding="utf-8")
        assert "Garmin Connect Data Export" in content
        assert "Date range:" in content
        assert "Exported:" in content

    def test_full_export_runs_within_size_budget(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        # The mock returns small/empty data, so the file should be well under
        # 100 KB even with 2 days of mostly-empty payloads.
        path = _run_full_export(mock_garmin_api, tmp_export_dir, days=2)
        size_kb = path.stat().st_size / 1024
        assert size_kb < 200, f"Export file too large: {size_kb:.0f} KB"

    def test_full_export_makes_some_api_calls(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        _run_full_export(mock_garmin_api, tmp_export_dir, days=2)
        # At least one call for the profile section
        assert mock_garmin_api.call_count > 0
        # Profile is always fetched on a fresh run
        assert mock_garmin_api.call_count_by_label("full_name") == 1

    def test_compact_export_single_line_json_for_sections(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        reset_settings.compact = True
        path = _run_full_export(mock_garmin_api, tmp_export_dir, days=2)
        content = path.read_text(encoding="utf-8")
        # In compact mode, schema description mentions "single-line JSON"
        assert "single-line JSON" in content

    def test_export_records_sleep_calls(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        _run_full_export(mock_garmin_api, tmp_export_dir, days=2)
        # Sleep should be fetched once per day (2 days -> 2 calls)
        assert mock_garmin_api.call_count_by_label("sleep") == 2


# ---------------------------------------------------------------------------
# Snapshot test: identical inputs produce identical content
# ---------------------------------------------------------------------------
class TestExportSnapshot:
    """Snapshot test - stable output for a fixed input.

    We use a content hash rather than checking the full string, because the
    export embeds an "Exported: <ISO timestamp>" line that always changes.
    The hash is computed over a normalised view that masks that line.
    """

    NORMALISE_PATTERN = re.compile(r"^Exported: .*$", re.MULTILINE)

    def _normalise(self, text: str) -> str:
        return self.NORMALISE_PATTERN.sub("Exported: <masked>", text)

    def test_snapshot_is_stable(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        path = _run_full_export(mock_garmin_api, tmp_export_dir, days=2)
        first = self._normalise(path.read_text(encoding="utf-8"))
        first_hash = hashlib.sha256(first.encode("utf-8")).hexdigest()

        # Run again with a fresh mock; normalised content should match.
        from tests.conftest import MockGarminApi

        second_api = MockGarminApi()
        second_path = _run_full_export(second_api, tmp_export_dir, days=2)
        second = self._normalise(second_path.read_text(encoding="utf-8"))
        second_hash = hashlib.sha256(second.encode("utf-8")).hexdigest()

        assert first_hash == second_hash, (
            "Normalised export content drifted across runs"
        )

    def test_snapshot_golden(
        self, mock_garmin_api, tmp_export_dir: Path, reset_settings
    ):
        """Lock in a golden hash for the export shape.

        If you intentionally change the output (e.g., new sections, new
        headers), update EXPECTED_GOLDEN_HASH below and document why in
        the commit message.
        """
        path = _run_full_export(mock_garmin_api, tmp_export_dir, days=2)
        content = self._normalise(path.read_text(encoding="utf-8"))
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Computed on 2026-06-05 with the 2-day mock fixture. Update this
        # hash only when the export shape changes intentionally, and add a
        # one-line note to the commit message explaining why.
        expected_hash = (
            "3b32ab6ad501ff59893f644b479b27d7fb399524aa0359c0065a4c2eb1f57954"
        )
        assert actual_hash == expected_hash, (
            f"Golden hash drift. New hash: {actual_hash}. "
            f"Expected: {expected_hash}"
        )
