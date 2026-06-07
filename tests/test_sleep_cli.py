"""Tests for the garmin-sleep subcommand (GLE-8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from garmin_llm_export.sleep_cli import build_parser, main


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
class TestSleepCliParser:
    def test_parser_has_default_days_one(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.days == 1

    def test_parser_accepts_days(self):
        parser = build_parser()
        args = parser.parse_args(["--days", "7"])
        assert args.days == 7

    def test_parser_rejects_zero_days(self):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--days", "0"])
        assert exc.value.code == 2

    def test_parser_has_output_and_verbose(self):
        parser = build_parser()
        args = parser.parse_args(["--output", "tmp", "-v"])
        assert args.output == "tmp"
        assert args.verbose is True


# ---------------------------------------------------------------------------
# main() -- end-to-end with stubbed network
# ---------------------------------------------------------------------------
class TestSleepCliMain:
    def test_help_lists_all_flags(self, capsys):
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-sleep", "--help"]
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0
        finally:
            sys.argv = old_argv
        captured = capsys.readouterr().out
        assert "--days" in captured
        assert "--output" in captured
        assert "--no-cache" in captured

    def test_no_sleep_data_exits_with_error(self, tmp_path, monkeypatch):
        # Stub login so we don't touch the network.
        sentinel = object()

        def fake_login(tokenstore):
            return sentinel

        import garmin_llm_export.sleep_cli as sleep_cli_mod
        monkeypatch.setattr(sleep_cli_mod, "login", fake_login)

        # Make _run_for_nights return [] (no data)
        monkeypatch.setattr(sleep_cli_mod, "_run_for_nights", lambda *a, **kw: [])

        old_argv = sys.argv
        try:
            sys.argv = [
                "garmin-sleep", "--days", "1", "--output", str(tmp_path),
            ]
            rc = main()
        finally:
            sys.argv = old_argv
        assert rc == 1

    def test_writes_one_file_per_night_with_data(self, tmp_path, monkeypatch):
        # Pre-seed the cache with two days of sleep data. The canned
        # fixture only has data for 2026-06-04 and 2026-06-05, so we
        # patch ``last_sleep.date`` to a stub whose ``today()`` returns
        # DAY1, which makes the writer scan DAY1, DAY0.
        from datetime import date as real_date
        from garmin_llm_export.cache import ExportCache
        from tests.conftest import _canned_sleep_for_day, DAY0, DAY1

        class _FakeDate:
            @staticmethod
            def today():
                return DAY1

            # Pass-through for other uses (timedelta arithmetic etc.)
            @staticmethod
            def fromisoformat(s):
                return real_date.fromisoformat(s)

        import garmin_llm_export.last_sleep as last_sleep_mod
        monkeypatch.setattr(last_sleep_mod, "date", _FakeDate)
        # Also patch it in sleep_cli (which does its own `from datetime import date`).
        import garmin_llm_export.sleep_cli as sleep_cli_mod
        monkeypatch.setattr(sleep_cli_mod, "date", _FakeDate)

        out = tmp_path / "export"
        out.mkdir()
        cache = ExportCache(out, enabled=True)
        cache.put_day(DAY0.isoformat(), {
            "summary": {}, "sleep": _canned_sleep_for_day(DAY0.isoformat()),
        })
        cache.put_day(DAY1.isoformat(), {
            "summary": {}, "sleep": _canned_sleep_for_day(DAY1.isoformat()),
        })

        # Stub login and the exporter
        sentinel = object()

        def fake_login(tokenstore):
            return sentinel

        monkeypatch.setattr(sleep_cli_mod, "login", fake_login)

        class _NoOpExporter:
            def __init__(self, *a, **kw):
                pass
            def run(self):
                pass

        monkeypatch.setattr(sleep_cli_mod, "GarminExporter", _NoOpExporter)

        old_argv = sys.argv
        try:
            sys.argv = [
                "garmin-sleep", "--days", "2", "--output", str(out),
            ]
            rc = main()
        finally:
            sys.argv = old_argv

        assert rc == 0
        # Multi-night request -> single multi-section file
        files = list(out.glob("garmin_sleep_*nights.txt"))
        assert len(files) == 1, f"Expected 1 multi-night file, got {len(files)}"
        content = files[0].read_text(encoding="utf-8")
        # Both nights are summarised
        assert DAY0.isoformat() in content
        assert DAY1.isoformat() in content
        # And the trend block
        assert "night trend" in content

    def test_days_flag_passes_through(self, tmp_path, monkeypatch):
        sentinel = object()

        def fake_login(tokenstore):
            return sentinel

        import garmin_llm_export.sleep_cli as sleep_cli_mod
        monkeypatch.setattr(sleep_cli_mod, "login", fake_login)

        captured_args = {}

        def fake_run(api, out_dir, *, nights, cache):
            captured_args["nights"] = nights
            return []

        monkeypatch.setattr(sleep_cli_mod, "_run_for_nights", fake_run)

        old_argv = sys.argv
        try:
            sys.argv = [
                "garmin-sleep", "--days", "7", "--output", str(tmp_path),
            ]
            main()
        finally:
            sys.argv = old_argv
        assert captured_args["nights"] == 7
