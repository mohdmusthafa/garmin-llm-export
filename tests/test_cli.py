"""Tests for the CLI (GLE-5): help text, discovery flags, validation."""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from garmin_llm_export.cli import build_parser, main
from garmin_llm_export.exporter import SECTION_REGISTRY


# ---------------------------------------------------------------------------
# Parser structure
# ---------------------------------------------------------------------------
class TestCLIParser:
    def test_help_lists_all_flags(self, capsys):
        # Force main() to handle --help by replacing sys.argv
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--help"]
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0
        finally:
            sys.argv = old_argv
        captured = capsys.readouterr().out
        for flag in [
            "--all", "--days", "--activities", "--sections", "--focus",
            "--list-presets", "--list-sections", "--compact", "--split",
            "--update", "--no-cache", "--delay", "--tokenstore", "--login",
            "-v", "--verbose",
        ]:
            assert flag in captured, f"--help is missing flag '{flag}'"

    def test_help_is_under_60_lines(self, capsys):
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--help"]
            with pytest.raises(SystemExit):
                main()
        finally:
            sys.argv = old_argv
        captured = capsys.readouterr().out
        assert len(captured.splitlines()) < 60, (
            f"--help output is {len(captured.splitlines())} lines, expected < 60"
        )

    def test_help_contains_quick_start(self, capsys):
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--help"]
            with pytest.raises(SystemExit):
                main()
        finally:
            sys.argv = old_argv
        captured = capsys.readouterr().out
        # Epilog must include the common query cookbook
        assert "--focus sleep" in captured
        assert "--all --split" in captured

    def test_help_groups_flags(self, capsys):
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--help"]
            with pytest.raises(SystemExit):
                main()
        finally:
            sys.argv = old_argv
        captured = capsys.readouterr().out
        # Argument groups
        for group in ("Data selection", "Output control", "Caching and pacing"):
            assert group in captured, f"Missing help group '{group}'"


# ---------------------------------------------------------------------------
# --list-presets
# ---------------------------------------------------------------------------
class TestListPresets:
    def test_list_presets_prints_all_preset_names(self, capsys):
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--list-presets"]
            rc = main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        captured = capsys.readouterr().out
        for name in ("sleep", "recovery", "training", "body", "all"):
            assert name in captured, f"--list-presets is missing '{name}'"

    def test_list_presets_includes_descriptions(self, capsys):
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--list-presets"]
            main()
        finally:
            sys.argv = old_argv
        captured = capsys.readouterr().out
        # Each preset has a non-empty description; just spot-check substrings
        assert "Daily Health + Training" in captured
        assert "Body Composition" in captured

    def test_list_presets_short_circuits_before_login(self, monkeypatch):
        # main() should never call login() when --list-presets is set.
        called = {"login": False}

        def fake_login(*a, **kw):
            called["login"] = True
            raise RuntimeError("login should not be called")

        import garmin_llm_export.cli as cli_mod
        monkeypatch.setattr(cli_mod, "login", fake_login)
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--list-presets"]
            rc = main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        assert called["login"] is False


# ---------------------------------------------------------------------------
# --list-sections
# ---------------------------------------------------------------------------
class TestListSections:
    def test_list_sections_prints_all_section_ids(self, capsys):
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--list-sections"]
            rc = main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        captured = capsys.readouterr().out
        for sec in SECTION_REGISTRY:
            assert sec.id in captured, f"--list-sections is missing '{sec.id}'"

    def test_list_sections_short_circuits(self, monkeypatch):
        called = {"login": False}

        def fake_login(*a, **kw):
            called["login"] = True
            raise RuntimeError("login should not be called")

        import garmin_llm_export.cli as cli_mod
        monkeypatch.setattr(cli_mod, "login", fake_login)
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--list-sections"]
            rc = main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        assert called["login"] is False


# ---------------------------------------------------------------------------
# --focus and --sections validation
# ---------------------------------------------------------------------------
class TestArgumentValidation:
    def test_focus_and_sections_are_mutually_exclusive(self):
        old_argv = sys.argv
        try:
            sys.argv = [
                "garmin-export", "--focus", "sleep", "--sections", "daily_health",
            ]
            with pytest.raises(SystemExit) as exc:
                main()
            # argparse exits with code 2 on usage errors
            assert exc.value.code == 2
        finally:
            sys.argv = old_argv

    def test_focus_unknown_choice_errors(self):
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--focus", "bogus"]
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 2
        finally:
            sys.argv = old_argv

    def test_sections_unknown_value_errors(self):
        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--sections", "not_a_section"]
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 2
        finally:
            sys.argv = old_argv

    def test_sections_accepts_comma_separated_list(self):
        parser = build_parser()
        args = parser.parse_args(["--sections", "daily_health,training,profile"])
        assert args.sections == "daily_health,training,profile"


# ---------------------------------------------------------------------------
# Smoke test: --login short-circuits before fetching
# ---------------------------------------------------------------------------
class TestLoginShortCircuit:
    def test_login_flag_exits_early(self, tmp_path: Path, monkeypatch, capsys):
        # Avoid network: stub login() to return a sentinel api.
        sentinel = object()

        def fake_login(tokenstore):
            return sentinel

        import garmin_llm_export.cli as cli_mod
        monkeypatch.setattr(cli_mod, "login", fake_login)

        called = {"exporter": False}

        class FakeExporter:
            def __init__(self, *a, **kw):
                pass

            def run(self):
                called["exporter"] = True

        import garmin_llm_export.cli as cli_mod
        monkeypatch.setattr(cli_mod, "GarminExporter", FakeExporter)

        old_argv = sys.argv
        try:
            sys.argv = ["garmin-export", "--login"]
            rc = main()
        finally:
            sys.argv = old_argv

        assert rc == 0
        assert called["exporter"] is False, "GarminExporter.run() should not be called for --login"
