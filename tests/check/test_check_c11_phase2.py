# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Phase 2 tests for the GitLab CLI output channel."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from docmethis_check import cli
from docmethis_check.models import AnnotationPlacement, CheckEntry, CheckResult, CheckSummary

if TYPE_CHECKING:
    from pathlib import Path


def _result(tmp_path: Path, *, errors: int = 0) -> CheckResult:
    """Build a result with one optional diagnostic under the project root."""
    checks = []
    if errors:
        checks.append(
            CheckEntry(
                severity="error",
                code="DMT-1120",
                symbol="module.undocumented",
                file=str(tmp_path / "src" / "module.py"),
                line_start=3,
                col_start=1,
                message="Docstring absent.",
                docstring_line=8,
                annotation_line=9,
            ),
        )
    return CheckResult(summary=CheckSummary(error_count=errors), checks=checks)


def test_help_exposes_gitlab_output_file() -> None:
    """The parser documents the GitLab Code Quality destination."""
    assert "--gitlab-output-file" in cli.create_parser().format_help()


def test_cli_writes_gitlab_json_and_github_outputs_without_stdout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """All explicit protocol outputs can be generated from one Check result."""
    result = _result(tmp_path, errors=1)
    monkeypatch.setattr(cli, "run_check", lambda **_: result)
    gitlab_file = tmp_path / "gl-code-quality-report.json"
    json_file = tmp_path / "docmethis-report.json"
    github_file = tmp_path / "annotations.txt"

    code = cli.main(
        [
            str(tmp_path),
            "--gitlab-output-file",
            str(gitlab_file),
            "--json-output-file",
            str(json_file),
            "--github-output-file",
            str(github_file),
            "--annotation-placement",
            "precise",
        ],
    )

    assert code == 1
    assert capsys.readouterr().out == ""
    assert json.loads(gitlab_file.read_text(encoding="utf-8"))[0]["location"]["lines"] == {"begin": 9, "end": 9}
    assert json.loads(json_file.read_text(encoding="utf-8"))["version"] == 1
    assert github_file.read_text(encoding="utf-8").startswith("::error file=")


def test_gitlab_output_file_suppresses_stdout_even_with_json_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicit GitLab destination takes precedence over stdout formatting."""
    monkeypatch.setattr(cli, "run_check", lambda **_: _result(tmp_path))
    output = tmp_path / "gl-code-quality-report.json"

    code = cli.main([str(tmp_path), "--format", "json", "--gitlab-output-file", str(output)])

    assert code == 0
    assert capsys.readouterr().out == ""
    assert json.loads(output.read_text(encoding="utf-8")) == []


def test_gitlab_ci_does_not_select_a_format_implicitly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """GITLAB_CI alone keeps the existing text stdout behavior."""
    monkeypatch.setenv("GITLAB_CI", "true")
    monkeypatch.setattr(cli, "run_check", lambda **_: _result(tmp_path))

    code = cli.main([str(tmp_path)])

    assert code == 0
    assert capsys.readouterr().out.startswith("DocMeThis Check")


def test_gitlab_formatter_receives_project_root_and_annotation_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The CLI forwards shared location configuration instead of duplicating it."""
    result = _result(tmp_path)
    received: dict[str, object] = {}

    def fake_format(_result: CheckResult, **kwargs: object) -> str:
        received.update(kwargs)
        return "[]"

    monkeypatch.setattr(cli, "run_check", lambda **_: result)
    monkeypatch.setattr(cli, "format_gitlab", fake_format)
    output = tmp_path / "gl-code-quality-report.json"

    assert cli.main([str(tmp_path), "--gitlab-output-file", str(output), "--annotation-placement", "precise"]) == 0
    assert received == {
        "file": str(output),
        "project_root": tmp_path.resolve(),
        "annotation_placement": AnnotationPlacement.PRECISE,
    }


@pytest.mark.parametrize(
    "arguments",
    [
        ("--gitlab-output-file", "--json-output-file"),
        ("--gitlab-output-file", "--github-output-file"),
    ],
)
def test_cli_rejects_duplicate_gitlab_destinations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arguments: tuple[str, str],
) -> None:
    """GitLab output cannot overwrite either existing protocol output."""
    monkeypatch.setattr(cli, "run_check", lambda **_: pytest.fail("Check must not run on output conflict"))
    output = tmp_path / "report.json"

    with pytest.raises(SystemExit, match="2"):
        cli.main([str(tmp_path), arguments[0], str(output), arguments[1], str(output)])


def test_cli_rejects_equivalent_output_paths(tmp_path: Path) -> None:
    """Equivalent normalized paths count as the same destination."""
    output = tmp_path / "report.json"
    alias = tmp_path / "nested" / ".." / "report.json"

    with pytest.raises(SystemExit, match="2"):
        cli.main(
            [
                str(tmp_path),
                "--gitlab-output-file",
                str(output),
                "--json-output-file",
                str(alias),
            ],
        )


def test_gitlab_output_write_error_returns_cli_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An output I/O failure is a CLI error, not a Check diagnostic."""
    monkeypatch.setattr(cli, "run_check", lambda **_: _result(tmp_path))
    output = tmp_path / "missing" / "report.json"

    code = cli.main([str(tmp_path), "--gitlab-output-file", str(output)])
    error = capsys.readouterr().err

    assert code == 2
    assert "docmethis_check:" in error
    assert "DMT-" not in error
