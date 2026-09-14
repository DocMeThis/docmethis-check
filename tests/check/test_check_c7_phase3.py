# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Tests C7 phase 3 : completeness some docstrings of class."""

from __future__ import annotations

from inspect import cleandoc
from typing import TYPE_CHECKING

from docmethis_extract_python.static_extraction.models import (
    ClassRecord,
    FunctionRecord,
    MethodType,
    Parameter,
    ParameterType,
    Signature,
    Visibility,
)

from docmethis_check.config import CheckConfig
from docmethis_check.diagnostics import diagnostics_for_class
from docmethis_check.models import SymbolKind

if TYPE_CHECKING:
    from pathlib import Path


def _parameter(identifier_name: str, source_line: int) -> Parameter:
    """Create a minimal parameter for a test signature."""
    return Parameter(
        name=identifier_name,
        annotation_raw="<absent>",
        default_value="<absent>",
        kind=ParameterType.POSITIONAL_OR_KEYWORD,
        line_start=source_line,
    )


def _init_method(source_file: Path) -> FunctionRecord:
    """Create a public __init__ method with two documentable parameters."""
    return FunctionRecord(
        qualified_name="module.Service.__init__",
        file_path=source_file,
        line_start=10,
        line_end=12,
        col_start=4,
        col_end=0,
        method_kind=MethodType.INSTANCE_METHOD,
        visibility=Visibility.PUBLIC,
        parent_class="module.Service",
        parent_module="module",
        existing_docstring=None,
        signature=Signature(
            parameters=[_parameter("self", 10), _parameter("name", 10), _parameter("count", 11)],
            return_annotation="<absent>",
            decorators=[],
        ),
    )


def _class(source_file: Path, docstring: str) -> ClassRecord:
    """Create a public class with parameter and attribute findings."""
    return ClassRecord(
        qualified_name="module.Service",
        file_path=source_file,
        line_start=1,
        line_end=20,
        col_start=0,
        col_end=0,
        visibility=Visibility.PUBLIC,
        parent_module="module",
        existing_docstring=cleandoc(docstring),
        docstring_line_start=2,
        methods=[_init_method(source_file)],
        class_attributes=["name", "count", "_cache"],
        instance_attributes=["name", "total"],
    )


def test_class_reports_undocumented_init_parameter(tmp_path: Path) -> None:
    """An undocumented __init__ parameter produces DMT-2001."""
    class_ = _class(
        tmp_path / "module.py",
        """Summary.

        Parameters
        ----------
        name : str
            Name displayed.

        Attributes
        ----------
        name : str
            Name displayed.
        count : int
            Counter.
        total : int
            Total computed.
        """,
    )

    entries, keys = diagnostics_for_class(class_, CheckConfig(), file="module.py")

    assert [(entry.code, entry.symbol_kind, entry.symbol, entry.diagnostic_key.parameter_name) for entry in entries] == [
        ("DMT-2001", SymbolKind.CLASS, "module.Service", "count")
    ]
    assert entries[0].annotation_line == 11
    assert entries[0].docstring_sha256 is not None
    assert keys[entries[0].diagnostic_key] == 1


def test_class_reports_undocumented_public_attributes(tmp_path: Path) -> None:
    """Undocumented public attributes produce DMT-1150."""
    class_ = _class(
        tmp_path / "module.py",
        """Summary.

        Parametres
        ----------
        name : str
            Name displayed.
        count : int
            Counter.

        Attributs
        ---------
        name : str
            Name displayed.
        """,
    )

    entries, keys = diagnostics_for_class(class_, CheckConfig(), file="module.py")

    assert [(entry.code, entry.severity, entry.diagnostic_key.expected) for entry in entries] == [
        ("DMT-1150", "error", "count"),
        ("DMT-1150", "error", "total"),
    ]
    assert all(entry.symbol_kind is SymbolKind.CLASS for entry in entries)
    assert all(entry.docstring_sha256 is not None for entry in entries)
    assert all(keys[entry.diagnostic_key] == 1 for entry in entries)
