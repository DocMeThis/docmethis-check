# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Tests of contract JSON v1 of Check (iteration C8, phase 0)."""

from __future__ import annotations

import hashlib
import json
from textwrap import dedent
from typing import TYPE_CHECKING

from docmethis_check.config import CheckConfig
from docmethis_check.diagnostics import _compute_docstring_sha256, diagnostics_for_file
from docmethis_check.formatters.json import format as format_json
from docmethis_check.models import CheckEntry, CheckResult, SymbolKind

if TYPE_CHECKING:
    from pathlib import Path


def _entry(**kwargs: str) -> CheckEntry:
    """Build a minimal Check entry with optional v1 facts."""
    return CheckEntry(
        severity="error",
        code="DMT-2001",
        symbol="module.f",
        file="module.py",
        line_start=1,
        col_start=1,
        message="Parameter not documented.",
        **kwargs,
    )


def test_report_v1_omits_unknown_facts() -> None:
    """The additive contract keeps the historical shape when facts are absent."""
    outcome = CheckResult(checks=[_entry()])

    data = json.loads(format_json(outcome))

    assert data["version"] == 1
    assert data["checks"][0] == {
        "severity": "error",
        "code": "DMT-2001",
        "symbol_kind": "function",
        "symbol": "module.f",
        "file": "module.py",
        "line_start": 1,
        "col_start": 1,
        "message": "Parameter not documented.",
    }


def test_report_v1_serializes_structured_facts_without_internal_key() -> None:
    """Structured v1 facts are published without exposing diagnostic_key."""
    outcome = CheckResult(
        checks=[
            _entry(
                section="Parameters",
                parameter_name="value",
                expected="value",
                granularity="entry",
                error_type="ParameterMissing",
                docstring_sha256="a" * 64,
                diagnostic_key="internal",
            )
        ]
    )

    data = json.loads(format_json(outcome))
    check = data["checks"][0]

    assert check["section"] == "Parameters"
    assert check["parameter_name"] == "value"
    assert check["expected"] == "value"
    assert check["granularity"] == "entry"
    assert check["error_type"] == "ParameterMissing"
    assert check["docstring_sha256"] == "a" * 64
    assert "observed" not in check
    assert "diagnostic_key" not in check


def test_sha256_docstring_normalizes_line_endings() -> None:
    """The hash is identical after normalizing CRLF and CR to LF."""
    normalized_body = "Summary.\nNext line.\nEnd."

    digest = _compute_docstring_sha256("Summary.\r\nNext line.\rEnd.")

    assert digest == hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()


def test_missing_docstring_keeps_structural_diagnostics(tmp_path: Path) -> None:
    """A missing docstring does not hide the documentation units behind it."""
    payload = dedent(
        """\
        def foo(first, second) -> int:
            if first:
                raise ValueError("first")
            if second:
                raise TypeError("second")
        """,
    )

    entries, keys = diagnostics_for_file(
        payload,
        tmp_path / "module.py",
        tmp_path,
        CheckConfig(symbol_kinds=frozenset({SymbolKind.FUNCTION})),
    )

    codes = [entry.code for entry in entries]
    assert codes.count("DMT-1120") == 1
    assert codes.count("DMT-2001") == 2
    assert codes.count("DMT-3001") == 1
    assert codes.count("DMT-4001") == 2
    assert len(keys) == len(entries)
    assert next(entry for entry in entries if entry.code == "DMT-1120").docstring_sha256 is None
    assert all(entry.docstring_sha256 and len(entry.docstring_sha256) == 64 for entry in entries if entry.code != "DMT-1120")


def test_disabled_missing_docstring_still_allows_structural_diagnostics(tmp_path: Path) -> None:
    """Disabling the absence rule does not disable the structural checks."""
    payload = "def foo(value) -> int:\n    return value\n"

    entries, _keys = diagnostics_for_file(
        payload,
        tmp_path / "module.py",
        tmp_path,
        CheckConfig(
            severity={"DMT-1120": "disabled"},
            symbol_kinds=frozenset({SymbolKind.FUNCTION}),
        ),
    )

    assert sorted(entry.code for entry in entries) == ["DMT-2001", "DMT-3001"]


def test_report_v1_deduplicates_duplicate_local_raises(tmp_path: Path) -> None:
    """Two identical local raises produce only one Raises target."""
    payload = dedent(
        '''\
        def foo(value: int) -> None:
            """Summary.

            Parameters
            ----------
            value : int
                Value controlled.
            """
            if value < 0:
                raise ValueError("negative")
            if value == 0:
                raise ValueError("zero")
        ''',
    )

    entries, keys = diagnostics_for_file(
        payload,
        tmp_path / "module.py",
        tmp_path,
        CheckConfig(symbol_kinds=frozenset({SymbolKind.FUNCTION})),
    )
    data = json.loads(format_json(CheckResult(checks=entries)))

    assert [(entry.code, entry.section, entry.expected, entry.granularity, entry.error_type) for entry in entries] == [
        ("DMT-4001", "Raises", "ValueError", "entry", "RaisesMissing"),
    ]
    assert len(keys) == 1
    assert next(iter(keys.values())) == 1
    assert [
        (check["code"], check["section"], check["expected"], check["granularity"], check["error_type"])
        for check in data["checks"]
    ] == [
        ("DMT-4001", "Raises", "ValueError", "entry", "RaisesMissing"),
    ]
    assert len(data["checks"][0]["docstring_sha256"]) == 64


def test_report_v1_deduplicates_duplicate_ghost_entries(tmp_path: Path) -> None:
    """Repeated ghost entries keep one target by identity."""
    payload = dedent(
        '''\
        def foo() -> None:
            """Summary.

            Parameters
            ----------
            ghost : int
                First entry.
            ghost : int
                Second entry.

            Raises
            ------
            ValueError
                First entry.
            ValueError
                Second entry.
            """
            pass
        ''',
    )

    config = CheckConfig(severity={"DMT-4002": "error"}, symbol_kinds=frozenset({SymbolKind.FUNCTION}))
    entries, keys = diagnostics_for_file(payload, tmp_path / "module.py", tmp_path, config)
    data = json.loads(format_json(CheckResult(checks=entries)))

    assert [(entry.code, entry.section, entry.parameter_name, entry.observed, entry.granularity) for entry in entries] == [
        ("DMT-2002", "Parameters", "ghost", "ghost", "entry"),
        ("DMT-4002", "Raises", None, "ValueError", "entry"),
    ]
    assert len(keys) == 2
    assert sorted(keys.values()) == [1, 1]
    assert [(check["code"], check["error_type"], check.get("parameter_name"), check["observed"]) for check in data["checks"]] == [
        ("DMT-2002", "ParameterGhost", "ghost", "ghost"),
        ("DMT-4002", "RaisesGhost", None, "ValueError"),
    ]
    assert len({check["docstring_sha256"] for check in data["checks"]}) == 1
