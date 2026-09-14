# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Acceptance tests for the C9 phase 2 CLI and output contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from docmethis_check import cli
from docmethis_check.formatters.github import format as format_github
from docmethis_check.formatters.json import format as format_json
from docmethis_check.formatters.text import format as format_text
from docmethis_check.models import CheckEntry, CheckResult, CheckSummary, ImpactAnalysis
from docmethis_check.runner import run_check


def _entry(tmp_path: Path, *, severity: str = "error", file_name: str = "module.py") -> CheckEntry:
    """Build a diagnostic entry with a filesystem path."""
    return CheckEntry(
        severity=severity,  # type: ignore[arg-type]
        code="DMT-3001",
        symbol="module.computes",
        file=str(tmp_path / file_name),
        line_start=3,
        col_start=1,
        message="Return not documented.",
        section="Returns",
        expected="int",
    )


def _result(tmp_path: Path) -> CheckResult:
    """Build a representative report for CLI channel tests."""
    return CheckResult(
        summary=CheckSummary(pass_count=2, error_count=1),
        checks=[_entry(tmp_path)],
        checked_files=[str(tmp_path / "module.py")],
        diff_strategy="explicit",
        diff_completeness="complete",
        diff_files=[{"path": str(tmp_path / "module.py"), "changed_lines": [3], "deleted_lines": []}],
        impact_analysis=ImpactAnalysis(completeness="complete"),
    )


def test_help_exposes_c9_output_options() -> None:
    """The live CLI help documents the new output controls."""
    help_text = " ".join(cli.create_parser().format_help().split())

    assert "--format {text,json}" in help_text
    assert "--verbose" in help_text
    assert "--color {auto,never,always}" in help_text
    assert "--ascii" in help_text
    assert "default: public only" in help_text
    assert "default: function,method,class" in help_text
    assert "default: getter,setter" in help_text
    assert "default: standard" in help_text
    assert "default: regression" in help_text
    assert "default: signature" in help_text
    assert "default: emit_all" in help_text
    assert "default: callable" in help_text
    assert "default: enabled" in help_text


def test_cli_json_is_identical_to_the_canonical_formatter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI JSON stdout is the same JSON v1 report produced directly."""
    result = _result(tmp_path)
    result.analysis_errors = [
        {"file": str(tmp_path / "broken.py"), "type": "parse_error", "message": "Unable to parse the file."},
    ]
    monkeypatch.setattr(cli, "run_check", lambda **_: result)
    expected_file = tmp_path / "expected.json"
    format_json(result, file=str(expected_file))

    assert cli.main([str(tmp_path), "--format", "json", "--verbose"]) == 1
    actual = json.loads(capsys.readouterr().out)
    expected = json.loads(expected_file.read_text(encoding="utf-8"))

    assert actual == expected


def test_explicit_file_channels_override_stdout_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Explicit JSON and GitHub files suppress stdout regardless of format."""
    result = _result(tmp_path)
    monkeypatch.setattr(cli, "run_check", lambda **_: result)
    json_file = tmp_path / "report.json"
    annotations_file = tmp_path / "annotations.txt"
    expected_json_file = tmp_path / "expected.json"
    expected_annotations_file = tmp_path / "expected.annotations"
    format_json(result, file=str(expected_json_file))
    format_github(result, file=str(expected_annotations_file))

    assert cli.main([str(tmp_path), "--format", "text", "--json-output-file", str(json_file)]) == 1
    assert capsys.readouterr().out == ""
    assert json.loads(json_file.read_text(encoding="utf-8")) == json.loads(expected_json_file.read_text(encoding="utf-8"))

    assert cli.main([str(tmp_path), "--format", "json", "--github-output-file", str(annotations_file)]) == 1
    assert capsys.readouterr().out == ""
    assert annotations_file.read_text(encoding="utf-8") == expected_annotations_file.read_text(encoding="utf-8")


def test_text_formatter_keeps_multiple_files_and_unresolved_scope_readable(tmp_path: Path) -> None:
    """Multiple file groups remain ordered and unresolved scope is explicit."""
    result = CheckResult(
        summary=CheckSummary(warning_count=1, error_count=1),
        checks=[
            _entry(tmp_path, severity="warning", file_name="z.py"),
            _entry(tmp_path, severity="error", file_name="a.py"),
        ],
        checked_files=[str(tmp_path / "z.py"), str(tmp_path / "a.py")],
        diff_strategy="github_no_reliable_base",
        diff_completeness="none",
        diff_reason="initial_branch_without_base",
        impact_analysis=ImpactAnalysis(completeness="complete"),
    )

    output = format_text(result, project_root=tmp_path)

    assert output.index("a.py") < output.index("z.py")
    assert "Scope: CI push range · unresolved" in output
    assert "impact_analysis.completeness" not in output


def test_piped_cli_output_is_text_without_ansi(tmp_path: Path) -> None:
    """A real non-TTY CLI invocation emits plain text."""
    source = tmp_path / "module.py"
    source.write_text('"""Module."""\n', encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "C9"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "add", "module.py"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True, check=True)

    outcome = subprocess.run(
        [sys.executable, "-m", "docmethis_check", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert outcome.returncode == 0
    assert outcome.stdout.startswith("DocMeThis Check — PASS\nScope: local changes · partial")
    assert "\x1b[" not in outcome.stdout


def test_changed_syntax_error_is_reported_in_text_report(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A parse failure is part of the report rather than a standalone extractor warning."""
    broken = tmp_path / "broken.py"
    broken.write_text('"""Initially valid."""\n', encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "C9"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "add", "broken.py"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True, check=True)
    broken.write_text("def broken(:\n", encoding="utf-8")

    result = run_check(tmp_path, write_cache=False)
    output = format_text(result, project_root=tmp_path)

    assert result.analysis_errors == [
        {"file": str(broken), "type": "parse_error", "message": "Unable to parse the file."},
    ]
    assert "No documentation issues found in checked files." in output
    assert "Skipped: broken.py — syntax error" in output
    assert not any("Syntax error in" in record.getMessage() for record in caplog.records)
