# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""C7 phase 5 tests for module docstring completeness."""

from __future__ import annotations

from inspect import cleandoc
from typing import TYPE_CHECKING

from docmethis_extract_python.static_extraction.models import ModuleRecord, Visibility

from docmethis_check.config import CheckConfig
from docmethis_check.diagnostics import diagnostics_for_module
from docmethis_check.models import Severity, SymbolKind

if TYPE_CHECKING:
    from pathlib import Path


def _module(source_file: Path, docstring: str) -> ModuleRecord:
    """Create a minimal public module with a docstring for C7 tests."""
    return ModuleRecord(
        file_path=source_file,
        module_name="module",
        line_start=1,
        line_end=12,
        visibility=Visibility.PUBLIC,
        docstring_line_start=1,
        existing_docstring=cleandoc(docstring),
    )


def _config_module(severity: dict[str, Severity] | None = None, *, profile: str = "standard") -> CheckConfig:
    """Return a Check configuration limited to modules."""
    return CheckConfig(symbol_kinds=frozenset({SymbolKind.MODULE}), severity=severity or {}, profile=profile)


def test_module_with_summary_reports_nothing(tmp_path: Path) -> None:
    """A usable module summary satisfies phase 5."""
    module = _module(
        tmp_path / "module.py",
        """Short module summary.

        Returns
        -------
        int
            Section ignored for modules.
        """,
    )

    entries, keys = diagnostics_for_module(module, _config_module({"DMT-7001": "warning"}), file="module.py")

    assert entries == []
    assert not keys


def test_module_missing_summary_is_disabled_in_loose_profile(tmp_path: Path) -> None:
    """A missing summary is disabled by the low-noise profile."""
    module = _module(
        tmp_path / "module.py",
        """Notes
        -----
        Details optional.
        """,
    )

    entries, keys = diagnostics_for_module(module, _config_module(profile="loose"), file="module.py")

    assert entries == []
    assert not keys


def test_module_reports_missing_summary_when_dmt500_enabled(tmp_path: Path) -> None:
    """DMT-7001 reports only a module's missing summary when enabled."""
    module = _module(
        tmp_path / "module.py",
        """Returns
        -------
        int
            Section not applicable to modules.

        Raises
        ------
        RuntimeError
            Section not applicable to modules.
        """,
    )

    entries, keys = diagnostics_for_module(module, _config_module({"DMT-7001": "warning"}), file="module.py")

    assert [(entry.code, entry.severity, entry.symbol_kind, entry.symbol) for entry in entries] == [
        ("DMT-7001", "warning", SymbolKind.MODULE, "module")
    ]
    assert entries[0].diagnostic_key.section == "Summary"
    assert entries[0].docstring_line == 1
    assert entries[0].annotation_line == 1
    assert entries[0].docstring_sha256 is not None
    assert keys[entries[0].diagnostic_key] == 1
