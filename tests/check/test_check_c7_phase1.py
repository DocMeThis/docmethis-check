# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""C7 phase 1 tests for direct diagnostics and module selection."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from docmethis_extract_python.static_extraction.models import FunctionRecord, MethodType, ModuleRecord, ProjectRecord, Visibility

from docmethis_check import runner
from docmethis_check.config import CheckConfig
from docmethis_check.diagnostics import produce_diagnostics
from docmethis_check.git_diff import ChangedFile, DiffRange
from docmethis_check.models import SymbolKind

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _module(source_file: Path) -> ModuleRecord:
    """Create a minimal public module for C7 diagnostics."""
    return ModuleRecord(
        file_path=source_file,
        module_name="module",
        line_start=1,
        line_end=8,
        visibility=Visibility.PUBLIC,
        existing_docstring=None,
    )


def _function(
    source_file: Path, *, visibility: Visibility = Visibility.PUBLIC, parent_class: str | None = None
) -> FunctionRecord:
    """Create a minimal function for symbol selection tests."""
    return FunctionRecord(
        qualified_name="module.f" if parent_class is None else "module.Service.f",
        file_path=source_file,
        line_start=1,
        line_end=2,
        col_start=0,
        col_end=0,
        method_kind=MethodType.FUNCTION if parent_class is None else MethodType.INSTANCE_METHOD,
        visibility=visibility,
        parent_class=parent_class,
        parent_module="module",
        existing_docstring=None,
    )


def test_symbol_record_selection_filters_symbol_kind(tmp_path: Path) -> None:
    """Common selection excludes functions outside the symbol_kinds scope."""
    func = _function(tmp_path / "module.py")
    config = CheckConfig(symbol_kinds=frozenset({SymbolKind.MODULE}))

    symbol_name = runner._selected_record_symbol(
        func, kind=SymbolKind.FUNCTION, module_visibility=Visibility.PUBLIC, config=config
    )

    assert symbol_name is None


def test_symbol_record_selection_filters_effective_visibility(tmp_path: Path) -> None:
    """Common selection applies the parent module's effective visibility."""
    func = _function(tmp_path / "module.py")
    config = CheckConfig(symbol_kinds=frozenset({SymbolKind.FUNCTION}), include_visibility=frozenset({Visibility.PUBLIC}))

    symbol_name = runner._selected_record_symbol(
        func, kind=SymbolKind.FUNCTION, module_visibility=Visibility.PROTECTED, config=config
    )

    assert symbol_name is None


def test_produce_diagnostics_module_without_docstring(tmp_path: Path) -> None:
    """The direct C7 verifier emits DMT-1101 for a selected module."""
    source_file = tmp_path / "module.py"
    module = _module(source_file)
    config = CheckConfig(symbol_kinds=frozenset({SymbolKind.MODULE}))

    entries, keys = produce_diagnostics(module, config, file="module.py")

    assert [(entry.code, entry.severity, entry.symbol_kind, entry.symbol) for entry in entries] == [
        ("DMT-1101", "error", SymbolKind.MODULE, "module")
    ]
    assert keys == Counter(entry.diagnostic_key for entry in entries)


def test_module_without_docstring_can_be_promoted_to_error(tmp_path: Path) -> None:
    """Project severity can promote DMT-1101 to an error."""
    source_file = tmp_path / "module.py"
    module = _module(source_file)
    config = CheckConfig(symbol_kinds=frozenset({SymbolKind.MODULE}), severity={"DMT-1101": "error"})

    entries, keys = produce_diagnostics(module, config, file="module.py")

    assert [(entry.code, entry.severity, entry.symbol_kind, entry.symbol) for entry in entries] == [
        ("DMT-1101", "error", SymbolKind.MODULE, "module")
    ]
    assert keys == Counter(entry.diagnostic_key for entry in entries)


def test_run_check_selects_module_on_file_changed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An opt-in module is checked as soon as its file changes."""
    source_file = (tmp_path / "module.py").resolve()
    source_file.write_text("x = 1\n", encoding="utf-8")
    project = ProjectRecord(project_root=tmp_path, project_name="test", modules=[_module(source_file)])

    monkeypatch.setattr(
        runner,
        "discover_changed_python_files",
        lambda *_args, **_kwargs: (
            [ChangedFile(path=source_file, changed_lines=frozenset({1}))],
            DiffRange("", "test", "complete"),
        ),
    )
    monkeypatch.setattr(runner, "analyze_project", lambda *_args, **_kwargs: project)

    outcome = runner.run_check(tmp_path, config=CheckConfig(symbol_kinds=frozenset({SymbolKind.MODULE})))

    assert [(check.code, check.severity, check.symbol_kind, check.symbol) for check in outcome.checks] == [
        ("DMT-1101", "error", SymbolKind.MODULE, "module")
    ]
    assert outcome.summary.warning_count == 0
    assert outcome.summary.error_count == 1
