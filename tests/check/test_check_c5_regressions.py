# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""C5 regressions for the check module."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from docmethis_extract_python.static_extraction.models import FunctionRecord, MethodType, ModuleRecord, ProjectRecord, Visibility

from docmethis_check import diagnostics, git_diff, runner
from docmethis_check.config import CheckConfig, load_check_config
from docmethis_check.formatters.github import format as format_github
from docmethis_check.formatters.json import format as format_json
from docmethis_check.git_diff import ChangedFile, DiffRange
from docmethis_check.models import AnnotationPlacement, CheckEntry, CheckMode, CheckResult, SymbolKind

if TYPE_CHECKING:
    from pathlib import Path


def test_run_check_respects_on_nonlinear_push_from_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The run_check API does not mask the pyproject configuration with its default."""
    values: list[str] = []

    def fake_discover(
        _project_root: Path,
        git_diff: str | None = None,
        *,
        base_ref: str | None = None,
        on_nonlinear_push_without_base: str = "fail",
    ) -> tuple[list[ChangedFile], DiffRange]:
        assert git_diff is None
        assert base_ref is None
        values.append(on_nonlinear_push_without_base)
        return [], DiffRange(revision_spec="", strategy="test", completeness="complete")

    monkeypatch.setattr(runner, "discover_changed_python_files", fake_discover)
    monkeypatch.setattr(
        runner,
        "analyze_project",
        lambda *_args, **_kwargs: ProjectRecord(project_root=tmp_path, project_name="test"),
    )

    runner.run_check(tmp_path, config=CheckConfig(on_nonlinear_push_without_base="head_commit"))

    assert values == ["head_commit"]


def test_config_rejects_survey_check_mode(tmp_path: Path) -> None:
    """The survey mode is not implemented and is rejected by pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text('[tool.docmethis.check]\ncheck-mode = "survey"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="survey"):
        load_check_config(tmp_path)


def test_check_config_rejects_survey_check_mode_directly() -> None:
    """The survey mode is rejected even when CheckConfig is built directly."""
    with pytest.raises(ValueError, match="survey"):
        CheckConfig(check_mode=CheckMode.SURVEY)


def test_parse_unified_diff_decodes_non_ascii_git_quotepath(tmp_path: Path) -> None:
    """Quoted Git paths encoded as octal UTF-8 are decoded without corruption."""
    diff = '--- "a/r\\303\\251sum\\303\\251.py"\n+++ "b/r\\303\\251sum\\303\\251.py"\n@@ -0,0 +1 @@\n+pass'

    files = git_diff._parse_unified_diff(diff, project_root=tmp_path)

    assert files == [
        ChangedFile(path=(tmp_path / "résumé.py").resolve(), changed_lines=frozenset({1})),
    ]


def test_parse_unified_diff_pure_deletion_has_no_changed_head_lines(tmp_path: Path) -> None:
    """A delete-only hunk does not mark a reported HEAD line as changed."""
    diff = "--- a/module.py\n+++ b/module.py\n@@ -1 +0,0 @@\n-pass"

    files = git_diff._parse_unified_diff(diff, project_root=tmp_path)

    assert files[0].path == (tmp_path / "module.py").resolve()
    assert files[0].changed_lines == frozenset()
    assert files[0].deleted_lines == frozenset({1})


def test_parse_unified_diff_pure_deletion_keeps_old_lines(tmp_path: Path) -> None:
    """A pure deletion keeps its deleted lines on the old side."""
    diff = "--- a/module.py\n+++ b/module.py\n@@ -2 +1,0 @@ def f():\n-    x = 1"

    files = git_diff._parse_unified_diff(diff, project_root=tmp_path)

    assert files == [
        ChangedFile(
            path=(tmp_path / "module.py").resolve(),
            changed_lines=frozenset(),
            deleted_lines=frozenset({2}),
        ),
    ]


def test_diff_range_explicit_three_dots_uses_merge_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An explicit A...B range uses the merge base for regression."""
    calls: list[tuple[str, Path, str, str]] = []

    def fake_merge_base(git: str, root: Path, ref: str, sha: str) -> str:
        calls.append((git, root, ref, sha))
        return "merge-base"

    monkeypatch.setattr(git_diff, "_merge_base", fake_merge_base)

    outcome = git_diff.diff_range_for_env(git="Git-test", project_root=tmp_path, git_diff="base...head")

    assert calls == [("Git-test", tmp_path, "base", "head")]
    assert outcome.revision_spec == "base...head"
    assert outcome.base_rev == "merge-base"
    assert outcome.head_rev == "head"


def test_diagnostics_for_file_outside_root_returns_empty(tmp_path: Path) -> None:
    """A file outside project_root does not raise ValueError."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    source_file = tmp_path / "externe.py"

    assert diagnostics.diagnostics_for_file(
        "def f():\n    pass\n",
        source_file,
        project_root,
        CheckConfig(),
    ) == ([], Counter())


def test_base_diagnostics_outside_root_returns_none(tmp_path: Path) -> None:
    """base_diagnostics remains defensive outside project_root."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    source_file = tmp_path / "externe.py"

    assert diagnostics.base_diagnostics("HEAD", source_file, project_root, CheckConfig()) is None


def test_run_check_does_not_count_excluded_visibilities(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The runner filter avoids counting functions outside visibility as passed."""
    source_file = tmp_path / "module.py"
    func = FunctionRecord(
        qualified_name="module._interne",
        file_path=source_file,
        line_start=1,
        line_end=2,
        col_start=1,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PROTECTED,
        parent_class=None,
        parent_module="module",
        existing_docstring=None,
    )
    project = ProjectRecord(
        project_root=tmp_path,
        project_name="test",
        modules=[ModuleRecord(file_path=source_file, module_name="module", functions=[func])],
    )

    monkeypatch.setattr(
        runner,
        "discover_changed_python_files",
        lambda *_args, **_kwargs: (
            [ChangedFile(path=source_file.resolve(), changed_lines=frozenset({1}))],
            DiffRange("", "test", "complete"),
        ),
    )
    monkeypatch.setattr(runner, "analyze_project", lambda *_args, **_kwargs: project)

    def forbidden_diagnostics(*_args: object, **_kwargs: object) -> tuple[list[object], Counter[object]]:
        msg = "diagnostics_for_function must not be called for an excluded visibility"
        raise AssertionError(msg)

    monkeypatch.setattr(runner, "diagnostics_for_file", forbidden_diagnostics)

    outcome = runner.run_check(tmp_path, config=CheckConfig(include_visibility=frozenset({Visibility.PUBLIC})))

    assert outcome.checks == []
    assert outcome.summary.pass_count == 0


def test_run_check_does_not_count_filtered_regression_as_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A filtered pre-existing error in regression mode does not become a pass."""
    source_file = tmp_path / "module.py"
    source_file.write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    func = FunctionRecord(
        qualified_name="module.f",
        file_path=source_file,
        line_start=1,
        line_end=2,
        col_start=1,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class=None,
        parent_module="module",
        existing_docstring=None,
    )
    project = ProjectRecord(
        project_root=tmp_path,
        project_name="test",
        modules=[ModuleRecord(file_path=source_file, module_name="module", functions=[func])],
    )
    entry = CheckEntry(
        severity="error",
        code="DMT-1120",
        symbol="module.f",
        file="module.py",
        line_start=1,
        col_start=1,
        message="Docstring missing.",
        diagnostic_key=("DMT-1120", "module.py", "module.f"),
    )
    keys = Counter({entry.diagnostic_key: 1})

    monkeypatch.setattr(
        runner,
        "discover_changed_python_files",
        lambda *_args, **_kwargs: (
            [ChangedFile(path=source_file.resolve(), changed_lines=frozenset({1}))],
            DiffRange("", "test", "complete", base_rev="base"),
        ),
    )
    monkeypatch.setattr(runner, "analyze_project", lambda *_args, **_kwargs: project)
    monkeypatch.setattr(runner, "diagnostics_for_file", lambda *_args, **_kwargs: ([entry], keys))
    monkeypatch.setattr(runner, "base_diagnostics", lambda *_args, **_kwargs: ([], keys))

    outcome = runner.run_check(tmp_path, config=CheckConfig(symbol_kinds=frozenset({SymbolKind.FUNCTION})))

    assert outcome.checks == []
    assert outcome.summary.pass_count == 0
    assert outcome.summary.error_count == 0


def test_regression_filter_signal_keeps_first_signal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Following regression fallback signals do not overwrite the first."""
    file_a = tmp_path / "a.py"
    file_b = tmp_path / "b.py"
    file_a.write_text("def a():\n    pass\n", encoding="utf-8")
    file_b.write_text("def b():\n    pass\n", encoding="utf-8")
    func_a = FunctionRecord(
        qualified_name="a.a",
        file_path=file_a,
        line_start=1,
        line_end=2,
        col_start=1,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class=None,
        parent_module="a",
        existing_docstring=None,
    )
    func_b = FunctionRecord(
        qualified_name="b.b",
        file_path=file_b,
        line_start=1,
        line_end=2,
        col_start=1,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class=None,
        parent_module="b",
        existing_docstring=None,
    )
    symbol_a = runner._SymbolCheck(kind=SymbolKind.FUNCTION, symbol=func_a.qualified_name)
    symbol_b = runner._SymbolCheck(kind=SymbolKind.FUNCTION, symbol=func_b.qualified_name)
    signals = iter(
        [
            runner._RegressionFilterSignal(completeness="first", reason="first-reason"),
            runner._RegressionFilterSignal(completeness="second", reason="second-reason"),
        ]
    )

    monkeypatch.setattr("docmethis_check.runner.diagnostics_for_file", lambda *_args, **_kwargs: ([], Counter()))
    monkeypatch.setattr(
        "docmethis_check.runner._get_base_keys",
        lambda *_args, **_kwargs: runner._BaseKeysResult(regression_filter_signal=next(signals)),
    )

    outcome = CheckResult()
    ctx = runner._DiagnosticContext(configuration=CheckConfig(), diff_range=DiffRange("", "test", "complete"), root=tmp_path)

    runner._execute_file_diagnostics(
        {file_a: [symbol_a], file_b: [symbol_b]},
        {symbol_a, symbol_b},
        outcome,
        ctx,
    )

    assert outcome.regression_filter_completeness == "first"
    assert outcome.regression_filter_reason == "first-reason"


def test_run_check_indented_deletion_affects_function(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A pure deletion indented in a function preserves bugfix intent ad87577."""
    source_file = tmp_path / "module.py"
    source_file.write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    func = FunctionRecord(
        qualified_name="module.f",
        file_path=source_file,
        line_start=1,
        line_end=2,
        col_start=1,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class=None,
        parent_module="module",
        existing_docstring=None,
    )
    project = ProjectRecord(
        project_root=tmp_path,
        project_name="test",
        modules=[ModuleRecord(file_path=source_file, module_name="module", functions=[func])],
    )
    entry = CheckEntry(
        severity="error",
        code="DMT-1120",
        symbol="module.f",
        file="module.py",
        line_start=1,
        col_start=1,
        message="Docstring missing.",
        diagnostic_key=("DMT-1120", "module.py", "module.f"),
    )

    monkeypatch.setattr(
        runner,
        "discover_changed_python_files",
        lambda *_args, **_kwargs: (
            [
                ChangedFile(
                    path=source_file.resolve(),
                    changed_lines=frozenset(),
                    deleted_lines=frozenset({2}),
                ),
            ],
            DiffRange("", "test", "complete", base_rev="base"),
        ),
    )
    monkeypatch.setattr(runner, "analyze_project", lambda *_args, **_kwargs: project)
    monkeypatch.setattr(runner, "base_functions", lambda *_args, **_kwargs: [func])
    monkeypatch.setattr(runner, "diagnostics_for_file", lambda *_args, **_kwargs: ([entry], Counter({entry.diagnostic_key: 1})))

    outcome = runner.run_check(tmp_path, config=CheckConfig())

    assert outcome.checks == [entry]


def test_run_check_reuses_base_snapshot_for_suppressions_and_regression_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The runner does not reload the base AST twice for a file with suppressions."""
    source_file = tmp_path / "module.py"
    source_file.write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    func = FunctionRecord(
        qualified_name="module.f",
        file_path=source_file,
        line_start=1,
        line_end=2,
        col_start=1,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class=None,
        parent_module="module",
        existing_docstring=None,
    )
    project = ProjectRecord(
        project_root=tmp_path,
        project_name="test",
        modules=[ModuleRecord(file_path=source_file, module_name="module", functions=[func])],
    )
    entry = CheckEntry(
        severity="error",
        code="DMT-1120",
        symbol="module.f",
        file="module.py",
        line_start=1,
        col_start=1,
        message="Docstring missing.",
        diagnostic_key=("DMT-1120", "module.py", "module.f"),
    )
    calls_content: list[tuple[str, str, Path]] = []
    calls_parse: list[str] = []

    monkeypatch.setattr(
        runner,
        "discover_changed_python_files",
        lambda *_args, **_kwargs: (
            [ChangedFile(path=source_file.resolve(), changed_lines=frozenset(), deleted_lines=frozenset({2}))],
            DiffRange("", "test", "complete", base_rev="base"),
        ),
    )
    monkeypatch.setattr(runner, "analyze_project", lambda *_args, **_kwargs: project)
    monkeypatch.setattr(runner, "diagnostics_for_file", lambda *_args, **_kwargs: ([entry], Counter({entry.diagnostic_key: 1})))

    def fake_content_base(base_rev: str, relative_path: str, project_root: Path) -> str:
        calls_content.append((base_rev, relative_path, project_root))
        return "def f() -> int:\n    return 1\n"

    def fake_snapshot(_content: str, relative_path: str) -> ModuleRecord:
        calls_parse.append(relative_path)
        return project.modules[0]

    monkeypatch.setattr(diagnostics, "_base_content_or_none", fake_content_base)
    monkeypatch.setattr(diagnostics, "_extract_module_record_snapshot", fake_snapshot)
    monkeypatch.setattr(diagnostics, "produce_diagnostics", lambda *_args, **_kwargs: ([], Counter()))

    outcome = runner.run_check(tmp_path, config=CheckConfig())

    assert outcome.checks == [entry]
    assert calls_content == [("base", "module.py", tmp_path)]
    assert calls_parse == ["module.py"]


def test_run_check_adjacent_function_deletion_does_not_affect_previous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A deletion pure starting by a def not marks not the function previous."""
    source_file = tmp_path / "module.py"
    source_file.write_text("def a() -> int:\n    return 1\n\ndef c() -> int:\n    return 3\n", encoding="utf-8")
    func = FunctionRecord(
        qualified_name="module.a",
        file_path=source_file,
        line_start=1,
        line_end=2,
        col_start=1,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class=None,
        parent_module="module",
        existing_docstring=None,
    )
    project = ProjectRecord(
        project_root=tmp_path,
        project_name="test",
        modules=[ModuleRecord(file_path=source_file, module_name="module", functions=[func])],
    )
    func_removed = FunctionRecord(
        qualified_name="module.b",
        file_path=source_file,
        line_start=3,
        line_end=4,
        col_start=0,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class=None,
        parent_module="module",
        existing_docstring=None,
    )

    monkeypatch.setattr(
        runner,
        "discover_changed_python_files",
        lambda *_args, **_kwargs: (
            [
                ChangedFile(
                    path=source_file.resolve(),
                    changed_lines=frozenset(),
                    deleted_lines=frozenset({3, 4}),
                ),
            ],
            DiffRange("", "test", "complete", base_rev="base"),
        ),
    )
    monkeypatch.setattr(runner, "analyze_project", lambda *_args, **_kwargs: project)
    monkeypatch.setattr(runner, "base_functions", lambda *_args, **_kwargs: [func_removed])

    def forbidden_diagnostics(*_args: object, **_kwargs: object) -> tuple[list[object], Counter[object]]:
        msg = "diagnostics_for_file must not be called for an adjacent function deletion"
        raise AssertionError(msg)

    monkeypatch.setattr(runner, "diagnostics_for_file", forbidden_diagnostics)

    outcome = runner.run_check(tmp_path, config=CheckConfig(symbol_kinds=frozenset({SymbolKind.FUNCTION})))

    assert outcome.checks == []
    assert outcome.summary.pass_count == 0


def test_run_check_non_indented_multiline_string_deletion_affects_function(tmp_path: Path) -> None:
    """A non-indented line removed from a multiline string affects its function owner."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        '''
        def f() -> str:
            text = """line 1
        line 2 not indented
        line 3"""
            return text
        ''',
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        '''
        def f() -> str:
            text = """line 1
        line 3"""
            return text
        ''',
    )

    outcome = runner.run_check(tmp_path, config=load_check_config(tmp_path, check_mode="catchup"))

    assert any(check.symbol == "module.f" and check.code == "DMT-1120" for check in outcome.checks)


def test_run_check_adjacent_method_deletion_does_not_affect_previous(tmp_path: Path) -> None:
    """Deleting a whole method does not mark the previous method as changed."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        class C:
            def a(self) -> int:
                return 1

            def b(self) -> int:
                return 2
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        """
        class C:
            def a(self) -> int:
                return 1
        """,
    )

    outcome = runner.run_check(tmp_path, config=load_check_config(tmp_path, check_mode="catchup"))

    assert not any(check.symbol == "module.C.a" for check in outcome.checks)


def test_run_check_missing_base_does_not_overwrite_diff_completeness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A missing-base fallback does not overwrite Git diff completeness."""
    source_file = tmp_path / "module.py"
    source_file.write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    func = FunctionRecord(
        qualified_name="module.f",
        file_path=source_file,
        line_start=1,
        line_end=2,
        col_start=1,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class=None,
        parent_module="module",
        existing_docstring=None,
    )
    project = ProjectRecord(
        project_root=tmp_path,
        project_name="test",
        modules=[ModuleRecord(file_path=source_file, module_name="module", functions=[func])],
    )
    entry = CheckEntry(
        severity="error",
        code="DMT-1120",
        symbol="module.f",
        file="module.py",
        line_start=1,
        col_start=1,
        message="Docstring missing.",
        diagnostic_key=("DMT-1120", "module.py", "module.f"),
    )

    monkeypatch.setattr(
        runner,
        "discover_changed_python_files",
        lambda *_args, **_kwargs: (
            [ChangedFile(path=source_file.resolve(), changed_lines=frozenset({1}))],
            DiffRange("base...head", "explicit", "complete", base_rev="base", head_rev="head"),
        ),
    )
    monkeypatch.setattr(runner, "analyze_project", lambda *_args, **_kwargs: project)
    monkeypatch.setattr(runner, "diagnostics_for_file", lambda *_args, **_kwargs: ([entry], Counter({entry.diagnostic_key: 1})))
    monkeypatch.setattr(runner, "base_diagnostics", lambda *_args, **_kwargs: None)

    outcome = runner.run_check(tmp_path, config=CheckConfig())

    assert outcome.diff_completeness == "complete"
    assert outcome.diff_reason is None
    assert outcome.regression_filter_completeness == "catchup_without_base"
    assert outcome.regression_filter_reason == "missing_base_revision"
    assert outcome.checks == [entry]


def test_json_serializes_dedicated_regression_filter(tmp_path: Path) -> None:
    """The regression fallback is exposed separately from Git diff metadata."""
    outcome = CheckResult(
        diff_strategy="explicit",
        diff_completeness="complete",
        regression_filter_completeness="catchup_without_base",
        regression_filter_reason="missing_base_revision",
    )

    data = json.loads(format_json(outcome, file=str(tmp_path / "report.json")))

    assert data["diff"] == {"strategy": "explicit", "completeness": "complete"}
    assert data["regression_filter"] == {
        "completeness": "catchup_without_base",
        "reason": "missing_base_revision",
    }


def test_github_format_uses_precomputed_annotation_line(tmp_path: Path) -> None:
    """The GitHub formatter respects the prepared annotation line in precise mode."""
    outcome = CheckResult(
        checks=[
            CheckEntry(
                severity="warning",
                code="DMT-3001",
                symbol="module.f",
                file="module.py",
                line_start=3,
                col_start=1,
                message="Return not documented.",
                docstring_line=12,
                annotation_line=42,
            )
        ]
    )

    output = format_github(
        outcome,
        file=str(tmp_path / "annotations.txt"),
        annotation_placement=AnnotationPlacement.PRECISE,
    )

    assert "line=42" in output
    assert "line=12" not in output


def _write_module(tmp_path: Path, payload: str) -> Path:
    """Write a Python test module."""
    source_file = tmp_path / "module.py"
    source_file.write_text(dedent(payload).strip() + "\n", encoding="utf-8")
    return source_file


def _git(tmp_path: Path, *args: str) -> None:
    """Run a Git command in the temporary repository."""
    subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True)


def _initialize_repo(tmp_path: Path) -> None:
    """Initialize a temporary Git repository."""
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@test")
    _git(tmp_path, "config", "user.name", "Test")


def _commit(tmp_path: Path, message: str) -> None:
    """Create a Git commit in the temporary repository."""
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", message)
