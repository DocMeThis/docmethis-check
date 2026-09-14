# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""C7 phase 2 tests for diff detection and minimal presence diagnostics."""

from __future__ import annotations

from textwrap import dedent
from typing import TYPE_CHECKING

from docmethis_extract_python.static_extraction.models import ClassRecord, ModuleRecord, ProjectRecord, Visibility

from docmethis_check import runner
from docmethis_check.config import CheckConfig
from docmethis_check.git_diff import ChangedFile, DiffRange
from docmethis_check.models import SymbolKind

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _class(source_file: Path, *, line_start: int = 3, line_end: int = 5) -> ClassRecord:
    """Create a minimal public class for C7 tests."""
    return ClassRecord(
        qualified_name="module.Service",
        file_path=source_file,
        line_start=line_start,
        line_end=line_end,
        col_start=0,
        col_end=0,
        visibility=Visibility.PUBLIC,
        parent_module="module",
        existing_docstring=None,
    )


def _module(source_file: Path, *, classes: list[ClassRecord] | None = None) -> ModuleRecord:
    """Create a minimal public module for C7 tests."""
    return ModuleRecord(
        file_path=source_file,
        module_name="module",
        line_start=1,
        line_end=8,
        visibility=Visibility.PUBLIC,
        existing_docstring=None,
        classes=classes or [],
    )


def test_collect_symbols_affected_returns_modules_and_classes(tmp_path: Path) -> None:
    """The runner identifies classes and modules affected by a changed file."""
    source_file = (tmp_path / "module.py").resolve()
    module = _module(source_file, classes=[_class(source_file)])
    project = ProjectRecord(project_root=tmp_path, project_name="test", modules=[module])
    modified_files = {source_file: ChangedFile(path=source_file, changed_lines=frozenset({4}))}
    ctx = runner._DiagnosticContext(
        configuration=CheckConfig(symbol_kinds=frozenset({SymbolKind.MODULE, SymbolKind.CLASS})),
        diff_range=DiffRange("", "test", "complete"),
        root=tmp_path,
    )

    symbols, files = runner._collect_affected_symbols(project, modified_files, ctx)

    assert [(t.kind, t.symbol) for t in files[source_file]] == [
        (SymbolKind.MODULE, "module"),
        (SymbolKind.CLASS, "module.Service"),
    ]
    assert {(t.kind, t.symbol) for t in symbols} == {(SymbolKind.MODULE, "module"), (SymbolKind.CLASS, "module.Service")}


def test_run_check_filters_one_diagnostic_for_affected_class(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A class affected by the diff passes through runner filtering."""
    source_file = (tmp_path / "module.py").resolve()
    source_file.write_text(
        dedent(
            """
            class Service:
                pass
            """
        ).lstrip(),
        encoding="utf-8",
    )
    project = ProjectRecord(
        project_root=tmp_path,
        project_name="test",
        modules=[_module(source_file, classes=[_class(source_file, line_start=1, line_end=2)])],
    )

    monkeypatch.setattr(
        runner,
        "discover_changed_python_files",
        lambda *_args, **_kwargs: (
            [ChangedFile(path=source_file, changed_lines=frozenset({1}))],
            DiffRange("", "test", "complete"),
        ),
    )
    monkeypatch.setattr(runner, "analyze_project", lambda *_args, **_kwargs: project)

    outcome = runner.run_check(tmp_path, config=CheckConfig(symbol_kinds=frozenset({SymbolKind.CLASS})))

    assert [(check.code, check.symbol_kind, check.symbol) for check in outcome.checks] == [
        ("DMT-1110", SymbolKind.CLASS, "module.Service")
    ]
    assert outcome.summary.error_count == 1
