# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Integration tests for C0 of the check module."""

from __future__ import annotations

import json
import subprocess
import sys
from textwrap import dedent
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from docmethis_check.config import CheckConfig, load_check_config
from docmethis_check.formatters.json import format as format_json
from docmethis_check.git_diff import (
    NoDiffBaseError,
    _commit_exists,
    _is_ancestor,
    _ref_exists,
    diff_range_for_env,
    discover_changed_python_files,
)
from docmethis_check.models import CheckEntry, CheckMode, CheckResult
from docmethis_check.runner import run_check


def _write_module(tmp_path: Path, payload: str) -> Path:
    source_file = tmp_path / "module.py"
    source_file.write_text(dedent(payload).strip() + "\n", encoding="utf-8")
    return source_file


def _git(tmp_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True)


def _initialize_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@test")
    _git(tmp_path, "config", "user.name", "Test")


def _commit(tmp_path: Path, message: str) -> None:
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", message)


def test_git_diff_commit_exists_uses_quiet_rev_parse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Commit existence distinguishes a missing commit from an unexpected Git error."""
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        commands.append(cmd)
        assert kwargs["check"] is False
        return SimpleNamespace(returncode=1, stderr="", stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert not _commit_exists("git", tmp_path, "HEAD")
    assert commands == [["git", "-C", str(tmp_path), "rev-parse", "--verify", "--quiet", "HEAD^{commit}"]]


def test_git_diff_ref_exists_reports_unexpected_return_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An unexpected Git error is not confused with a missing reference."""

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        assert cmd == ["git", "-C", str(tmp_path), "rev-parse", "--verify", "--quiet", "origin/main"]
        assert kwargs["check"] is False
        return SimpleNamespace(returncode=128, stderr="fatal", stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(NoDiffBaseError, match="rev-parse"):
        _ref_exists("git", tmp_path, "origin/main")


def test_git_diff_is_ancestor_reports_unexpected_return_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only return code 1 represents a negative result for --is-ancestor."""

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        assert cmd == ["git", "-C", str(tmp_path), "merge-base", "--is-ancestor", "a", "b"]
        assert kwargs["check"] is False
        return SimpleNamespace(returncode=128, stderr="fatal", stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(NoDiffBaseError, match="is-ancestor"):
        _is_ancestor("git", tmp_path, "a", "b")


def test_cli_stdout(tmp_path: Path) -> None:
    """The check produces valid JSON on stdout when requested explicitly."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        '''
        def base() -> int:
            return 1


        def complete(x: int) -> int:
            """Returns the received value.

            Parameters
            ----------
            x : int
                Value received.

            Returns
            -------
            int
                Value received.
            """
            return x
        ''',
    )

    outcome = subprocess.run(
        [sys.executable, "-m", "docmethis_check", str(tmp_path), "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert outcome.returncode == 0
    data = json.loads(outcome.stdout)
    assert "version" in data
    assert "summary" in data
    assert "checks" in data
    assert (tmp_path / ".docmethis_cache.json").exists()


def test_cli_clean_repository_creates_cache_without_diagnostics(tmp_path: Path) -> None:
    """A repository without a diff initializes the cache and produces no diagnostics."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        def preexisting_debt() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")

    outcome = subprocess.run(
        [sys.executable, "-m", "docmethis_check", str(tmp_path), "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )

    data = json.loads(outcome.stdout)
    assert outcome.returncode == 0
    assert outcome.stdout
    assert data["summary"]["checks"] == {"pass": 0, "warning": 0, "error": 0, "total": 0}
    assert data["checks"] == []
    assert (tmp_path / ".docmethis_cache.json").exists()


def test_cli_no_cache_disables_cache_write(tmp_path: Path) -> None:
    """--no-cache prevents writing the cache artifact."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")

    outcome = subprocess.run(
        [sys.executable, "-m", "docmethis_check", str(tmp_path), "--no-cache"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert outcome.returncode == 0
    assert not (tmp_path / ".docmethis_cache.json").exists()


def test_cli_outside_git_repository_requires_explicit_range(tmp_path: Path) -> None:
    """Without a Git repository, range inference fails with a controlled error."""
    _write_module(
        tmp_path,
        """
        def function() -> int:
            return 1
        """,
    )

    outcome = subprocess.run(
        [sys.executable, "-m", "docmethis_check", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert outcome.returncode == 2
    assert "Provide --git-diff explicitly" in outcome.stderr


def test_git_diff_resolves_github_push(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A GitHub push uses the before/current SHA pair."""
    _initialize_repo(tmp_path)
    source_file = tmp_path / "f.py"
    source_file.write_text("x=1\n", encoding="utf-8")
    _commit(tmp_path, "initial")
    _write_module(tmp_path, "y=2\n")
    _commit(tmp_path, "second")

    before = subprocess.run(
        ["git", "rev-parse", "HEAD~1"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.strip()

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_BEFORE_SHA", before)
    monkeypatch.setenv("GITHUB_SHA", sha)

    outcome = diff_range_for_env(git="git", project_root=tmp_path, git_diff=None)
    assert outcome.revision_spec == f"{before}..{sha}"
    assert outcome.strategy == "github_linear_before_after"


def test_git_diff_resolves_github_push_from_event_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A real GitHub push reads its previous SHA from the event payload."""
    _initialize_repo(tmp_path)
    source_file = tmp_path / "f.py"
    source_file.write_text("x=1\n", encoding="utf-8")
    _commit(tmp_path, "initial")
    source_file.write_text("x=2\n", encoding="utf-8")
    _commit(tmp_path, "second")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.strip()
    before = subprocess.run(
        ["git", "rev-parse", "HEAD~1"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"before": before}), encoding="utf-8")

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_SHA", sha)

    outcome = diff_range_for_env(git="git", project_root=tmp_path, git_diff=None)
    assert outcome.revision_spec == f"{before}..{sha}"
    assert outcome.strategy == "github_linear_before_after"


def test_git_diff_resolves_initial_github_push_without_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A first GitHub push without base_ref raises an error."""
    _initialize_repo(tmp_path)
    source_file = tmp_path / "f.py"
    source_file.write_text("x=1\n", encoding="utf-8")
    _commit(tmp_path, "initial")

    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.strip()

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_BEFORE_SHA", "0" * 40)
    monkeypatch.setenv("GITHUB_SHA", sha)

    with pytest.raises(NoDiffBaseError):
        diff_range_for_env(git="git", project_root=tmp_path, git_diff=None)


def test_git_diff_resolves_first_gitlab_pipeline_without_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A first GitLab pipeline without base_ref raises an error."""
    _initialize_repo(tmp_path)
    source_file = tmp_path / "f.py"
    source_file.write_text("x=1\n", encoding="utf-8")
    _commit(tmp_path, "initial")

    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.strip()

    monkeypatch.setenv("CI_COMMIT_BEFORE_SHA", "0" * 40)
    monkeypatch.setenv("CI_COMMIT_SHA", sha)

    with pytest.raises(NoDiffBaseError):
        diff_range_for_env(git="git", project_root=tmp_path, git_diff=None)


def test_cli_output_file(tmp_path: Path) -> None:
    """The check writes JSON and fails on a diff error."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1


        def undocumented() -> int:
            return 1
        """,
    )
    output_file = tmp_path / "report.json"
    outcome = subprocess.run(
        [
            sys.executable,
            "-m",
            "docmethis_check",
            str(tmp_path),
            "--json-output-file",
            str(output_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert outcome.returncode == 1
    assert outcome.stdout == ""
    assert output_file.is_file()
    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert "summary" in data
    assert data["summary"]["checks"]["error"] == 2


def test_cli_accepts_explicit_git_diff(tmp_path: Path) -> None:
    """The --git-diff option forces the analyzed range."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1


        def undocumented() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "second")

    outcome = subprocess.run(
        [
            sys.executable,
            "-m",
            "docmethis_check",
            str(tmp_path),
            "--git-diff",
            "HEAD~1..HEAD",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    data = json.loads(outcome.stdout)
    assert outcome.returncode == 1
    assert data["checks"][0]["code"] == "DMT-1120"


def test_run_check_reports_only_functions_affected_by_diff(tmp_path: Path) -> None:
    """Pre-existing public debt outside changed lines is not reported."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        def preexisting_debt() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")

    _write_module(
        tmp_path,
        """
        def preexisting_debt() -> int:
            return 1


        def new_public() -> int:
            return 2
        """,
    )

    outcome = run_check(str(tmp_path))

    assert {check.code for check in outcome.checks} == {"DMT-1120", "DMT-3001"}
    assert {check.symbol for check in outcome.checks} == {"module.new_public"}
    assert outcome.summary.error_count == 2


def test_run_check_ignores_affected_private_function(tmp_path: Path) -> None:
    """DMT-1120 covers public functions by default."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")

    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1


        def _private() -> int:
            return 2
        """,
    )

    outcome = run_check(str(tmp_path))

    assert outcome.checks == []


def test_run_check_reports_missing_returns_for_typed_function(tmp_path: Path) -> None:
    """A typed function without Returns produces DMT-3001."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        '''
        def base() -> int:
            return 1


        def computes(x: int) -> int:
            """Returns the received value.

            Parameters
            ----------
            x : int
                Value received.
            """
            return x
        ''',
    )

    outcome = run_check(str(tmp_path))

    assert [(check.code, check.severity) for check in outcome.checks] == [("DMT-3001", "error")]
    assert outcome.checks[0].docstring_line == 6
    assert outcome.summary.warning_count == 0
    assert outcome.summary.error_count == 1


def test_run_check_distinguishes_returns_section_without_type(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A Returns section without a type uses a distinct code."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        '''
        def base() -> int:
            return 1


        def computes() -> int:
            """Returns a value.

            Returns
            -------
            int
                Value computed.
            """
            return 1
        ''',
    )
    monkeypatch.setattr(
        "docmethis_check.diagnostics.verify_docstring",
        lambda _: SimpleNamespace(
            errors=(),
            warnings=(
                SimpleNamespace(
                    section="Parameters",
                    kind="missing_blank_line",
                    detail="blank line missing",
                ),
            ),
        ),
    )

    outcome = run_check(str(tmp_path))

    # The docstring documents an int type, so it does not produce DMT-3010.
    # Standard reports the missing blank line as a warning.
    assert [(check.code, check.severity) for check in outcome.checks] == [
        ("DMT-6051", "warning"),
    ]
    assert outcome.summary.warning_count == 1
    assert outcome.summary.error_count == 0
    assert outcome.summary.pass_count == 0


def test_run_check_reports_multiline_signature_and_docstring_lines(tmp_path: Path) -> None:
    """The docstring line comes from the AST, not from calculating line_start + 1."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        '''
        def decorator(fn: object) -> object:
            """Returns the function decorated.

            Parameters
            ----------
            fn : object
                Decorated function.

            Returns
            -------
            object
                Decorated function.
            """
            return fn


        def base() -> int:
            return 1
        ''',
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        '''
        def decorateur(fn: object) -> object:
            """Returns the function decorated.

            Parameters
            ----------
            fn : object
                Decorated function.

            Returns
            -------
            object
                Decorated function.
            """
            return fn


        def base() -> int:
            return 1


        @decorator
        def computes(
            x: int,
        ) -> int:
            """Returns the received value.

            Parameters
            ----------
            x : int
                Value received.
            """
            return x
        ''',
    )

    outcome = run_check(str(tmp_path))

    assert [(check.code, check.severity) for check in outcome.checks] == [("DMT-3001", "error")]
    assert outcome.checks[0].docstring_line == outcome.checks[0].line_start + 3


def test_run_check_counts_complete_docstring_as_pass(tmp_path: Path) -> None:
    """A complete docstring is counted as a pass."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        '''
        def base() -> int:
            return 1


        def complete(x: int) -> int:
            """Returns the received value.

            Parameters
            ----------
            x : int
                Value received.

            Returns
            -------
            int
                Value received.
            """
            return x
        ''',
    )

    outcome = run_check(str(tmp_path))

    assert outcome.checks == []
    assert outcome.summary.pass_count == 1


def test_git_diff_ignores_pure_deletion_without_new_lines(tmp_path: Path) -> None:
    """A pure deletion declares no new line to the checker."""
    _initialize_repo(tmp_path)
    source_file = _write_module(
        tmp_path,
        """
        def old_function() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")
    source_file.write_text("", encoding="utf-8")

    files, _ = discover_changed_python_files(tmp_path)

    assert len(files) == 1
    assert files[0].path.samefile(source_file)


def test_check_result_model() -> None:
    """The models CheckResult/CheckEntry are usable."""
    outcome = CheckResult()
    assert outcome.version == 1
    assert len(outcome.checks) == 0

    outcome.checks.append(
        CheckEntry(
            severity="error",
            code="DMT-1120",
            symbol="test.func",
            file="/tmp/test.py",
            line_start=1,
            col_start=1,
            message="No docstring.",
        )
    )
    assert len(outcome.checks) == 1
    assert outcome.checks[0].code == "DMT-1120"


def test_json_format(tmp_path: Path) -> None:
    """The JSON formatter produces the expected pivot format."""
    test_file = str(tmp_path / "test.py")
    outcome = CheckResult()
    outcome.checked_files = [test_file]
    outcome.summary.error_count = 1
    outcome.checks.append(
        CheckEntry(
            severity="error",
            code="DMT-1120",
            symbol="test.func",
            file=test_file,
            line_start=1,
            col_start=1,
            message="No docstring.",
        )
    )

    text = format_json(outcome)
    data = json.loads(text)
    assert data["version"] == 1
    assert data["summary"]["checks"]["error"] == 1
    assert len(data["checks"]) == 1
    assert data["checks"][0]["code"] == "DMT-1120"
    assert data["checks"][0]["severity"] == "error"
    assert "docstring_line" not in data["checks"][0]


class TestRegressionC5:
    """Tests of regression filtering (C5 -- base/head)."""

    def test_regression_filters_preexisting_debt(self, tmp_path: Path) -> None:
        """Pre-existing debt (DMT-1120) is filtered in regression mode."""
        _initialize_repo(tmp_path)
        _write_module(
            tmp_path,
            """
            def base() -> int:
                return 1
        """,
        )
        _commit(tmp_path, "initial")

        _write_module(
            tmp_path,
            '''
            def base() -> int:
                return 1

            def complete() -> int:
                """New documented function."""
                return 2
        ''',
        )
        _commit(tmp_path, "second")

        config = load_check_config(tmp_path, check_mode="regression")
        outcome = run_check(str(tmp_path), git_diff="HEAD~1..HEAD", config=config)

        assert not any(c.symbol == "module.base" for c in outcome.checks)
        assert any(c.symbol == "module.complete" and c.code == "DMT-3001" for c in outcome.checks)

    def test_regression_distinguishes_warnings_by_element(self, tmp_path: Path) -> None:
        """A warning regression for a different element (x -> y) is kept."""
        _initialize_repo(tmp_path)
        _write_module(
            tmp_path,
            '''
            def f() -> None:
                """Summary.

                Parameters
                ----------
                x :
                """
        ''',
        )
        _commit(tmp_path, "initial")

        _write_module(
            tmp_path,
            '''
            def f() -> None:
                """Summary.

                Parameters
                ----------
                y :
                """
        ''',
        )
        _commit(tmp_path, "second")

        config = CheckConfig(check_mode=CheckMode.REGRESSION, severity={"DMT-6218": "warning"})
        outcome = run_check(str(tmp_path), git_diff="HEAD~1..HEAD", config=config)

        warnings_dmt6218 = [c for c in outcome.checks if c.code == "DMT-6218"]
        assert len(warnings_dmt6218) == 1
        assert warnings_dmt6218[0].symbol == "module.f"

    def test_regression_keeps_new_diagnostic(self, tmp_path: Path) -> None:
        """A new diagnostic on a fresh function is kept."""
        _initialize_repo(tmp_path)
        _write_module(
            tmp_path,
            '''
            def existante() -> int:
                """Already documented."""
                return 1
        ''',
        )
        _commit(tmp_path, "initial")

        _write_module(
            tmp_path,
            '''
            def existante() -> int:
                """Already documented."""
                return 1

            def new() -> int:
                return 2
        ''',
        )
        _commit(tmp_path, "new")

        config = load_check_config(tmp_path, check_mode="regression")
        outcome = run_check(str(tmp_path), git_diff="HEAD~1..HEAD", config=config)

        assert any(c.symbol == "module.new" and c.code == "DMT-1120" for c in outcome.checks)
        assert not any(c.symbol == "module.existante" for c in outcome.checks)

    def test_regression_new_file_does_not_require_base_snapshot(self, tmp_path: Path) -> None:
        """A new file is treated as having an empty base even in strict regression mode."""
        _initialize_repo(tmp_path)
        _write_module(
            tmp_path,
            '''
            def existante() -> int:
                """Already documented."""
                return 1
        ''',
        )
        _commit(tmp_path, "initial")

        new_file = tmp_path / "new.py"
        new_file.write_text("def nouvelle() -> int:\n    return 2\n", encoding="utf-8")
        _commit(tmp_path, "add module")

        config = load_check_config(tmp_path, check_mode="regression", on_missing_base="fail")
        outcome = run_check(str(tmp_path), git_diff="HEAD~1..HEAD", config=config)

        assert outcome.diff_completeness == "complete"
        assert any(check.file == "new.py" and check.code == "DMT-1120" for check in outcome.checks)

    def test_catchup_unchanged(self, tmp_path: Path) -> None:
        """In catchup mode, all diagnostics are emitted without filtering."""
        _initialize_repo(tmp_path)
        _write_module(
            tmp_path,
            """
            def base() -> int:
                return 1
        """,
        )
        _commit(tmp_path, "initial")

        _write_module(
            tmp_path,
            """
            def base() -> int:
                return 1

            def new() -> int:
                return 2
        """,
        )
        _commit(tmp_path, "second")

        config = load_check_config(tmp_path, check_mode="catchup")
        outcome = run_check(str(tmp_path), git_diff="HEAD~1..HEAD", config=config)

        checks_dmt1120 = [c for c in outcome.checks if c.code == "DMT-1120"]
        assert len(checks_dmt1120) >= 1

    def test_on_missing_base_emit_all(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """on_missing_base='emit_all' emits everything without changing diff completeness."""
        _initialize_repo(tmp_path)
        _write_module(
            tmp_path,
            """
            def base() -> int:
                return 1
        """,
        )
        _commit(tmp_path, "initial")
        _write_module(
            tmp_path,
            """
            def base() -> int:
                return 1

            def new() -> int:
                return 2
        """,
        )

        def fake_base(*_: object, **__: object) -> None:
            return None

        monkeypatch.setattr("docmethis_check.runner.base_diagnostics", fake_base)

        config = load_check_config(tmp_path, check_mode="regression", on_missing_base="emit_all")
        outcome = run_check(str(tmp_path), config=config)

        assert outcome.diff_completeness == "partial"
        assert outcome.diff_reason == "no_ci_environment_detected"
        assert any(c.code == "DMT-1120" for c in outcome.checks)

    def test_on_missing_base_fail(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """on_missing_base='fail' raises an error when the base is unavailable."""
        _initialize_repo(tmp_path)
        _write_module(
            tmp_path,
            """
            def base() -> int:
                return 1
        """,
        )
        _commit(tmp_path, "initial")
        # Create an uncommitted change to produce a local diff.
        _write_module(
            tmp_path,
            """
            def base() -> int:
                return 1

            def new() -> int:
                return 2
        """,
        )

        def fake_base(*_: object, **__: object) -> None:
            return None

        monkeypatch.setattr("docmethis_check.runner.base_diagnostics", fake_base)

        config = load_check_config(tmp_path, check_mode="regression", on_missing_base="fail")
        with pytest.raises(RuntimeError, match="unavailable"):
            run_check(str(tmp_path), config=config)
