# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Check diagnostics for a declared value type with no normal return path."""

from __future__ import annotations

import json
import subprocess
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from docmethis_extract_python.api import Visibility

from docmethis_check.config import CheckConfig
from docmethis_check.diagnostics import diagnostics_for_file
from docmethis_check.formatters.github import format as format_github
from docmethis_check.formatters.json import format as format_json
from docmethis_check.formatters.text import format as format_text
from docmethis_check.models import CheckEntry, CheckResult, CheckSummary, SymbolKind
from docmethis_check.runner import run_check

if TYPE_CHECKING:
    from pathlib import Path


def _check(source: str, root: Path, *, config: CheckConfig | None = None) -> dict[str, dict[str, str]]:
    """Return emitted codes and severities grouped by symbol for one source snapshot."""
    entries, _keys = diagnostics_for_file(
        dedent(source),
        root / "module.py",
        root,
        config or CheckConfig(symbol_kinds=frozenset({SymbolKind.FUNCTION})),
    )
    return {
        symbol: {entry.code: entry.severity for entry in entries if entry.symbol == symbol}
        for symbol in {entry.symbol for entry in entries}
    }


def test_check_3012_requires_proven_absence_and_a_resolved_value_annotation(tmp_path: Path) -> None:
    """Only a proven normal-return absence plus a resolved value annotation emits DMT-3013."""
    codes = _check(
        """
        from typing import Never
        from application.types import UnknownResult

        class DiffRange:
            pass

        def always_raises() -> DiffRange:
            raise RuntimeError()

        def all_branches_raise(flag: bool) -> DiffRange:
            if flag:
                raise RuntimeError()
            else:
                raise ValueError()

        def nullable_raises() -> DiffRange | None:
            raise RuntimeError()

        def conditionally_returns(flag: bool) -> DiffRange:
            if flag:
                raise RuntimeError()
            return DiffRange()

        def falls_through(flag: bool) -> DiffRange:
            if flag:
                raise RuntimeError()

        def returns_opaque_call() -> DiffRange:
            return unknown_helper()

        def catches_then_returns() -> DiffRange:
            try:
                raise RuntimeError()
            except RuntimeError:
                return DiffRange()

        def intentionally_never() -> Never:
            raise RuntimeError()

        def returns_none() -> None:
            raise RuntimeError()

        def unresolved_alias() -> UnknownResult:
            raise RuntimeError()

        @opaque_decorator
        def opaque_control_flow() -> DiffRange:
            raise RuntimeError()

        def generator() -> int:
            yield 1

        def unannotated():
            raise RuntimeError()
        """,
        tmp_path,
    )

    assert "DMT-3013" in codes["module.always_raises"]
    assert "DMT-3013" in codes["module.all_branches_raise"]
    assert "DMT-3013" in codes["module.nullable_raises"]
    for symbol in (
        "module.conditionally_returns",
        "module.falls_through",
        "module.returns_opaque_call",
        "module.catches_then_returns",
        "module.intentionally_never",
        "module.returns_none",
        "module.unresolved_alias",
        "module.unannotated",
        "module.opaque_control_flow",
        "module.generator",
    ):
        assert "DMT-3013" not in codes.get(symbol, {})
    for symbol in ("module.always_raises", "module.nullable_raises", "module.unresolved_alias"):
        assert "DMT-3001" not in codes.get(symbol, {})
    assert "DMT-3001" in codes["module.conditionally_returns"]


def test_check_3012_suppresses_missing_return_type_for_proven_nonreturning_function(tmp_path: Path) -> None:
    """A non-returning function is not asked to document a Returns type."""
    codes = _check(
        '''
        class Result:
            pass

        def fail() -> Result:
            """Always fails.

            Returns
            -------
                No value is returned.
            """
            raise RuntimeError()
        ''',
        tmp_path,
    )

    assert "DMT-3013" in codes["module.fail"]
    assert "DMT-3001" not in codes["module.fail"]
    assert "DMT-3010" not in codes["module.fail"]


@pytest.mark.parametrize(
    ("profile", "override", "expected"),
    [
        pytest.param("loose", {}, None, id="loose-disabled"),
        pytest.param("standard", {}, "warning", id="standard-warning"),
        pytest.param("strict", {}, "error", id="strict-error"),
        pytest.param("strict", {"DMT-3013": "disabled"}, None, id="explicitly-disabled"),
    ],
)
def test_check_3012_obeys_profile_severity(
    tmp_path: Path,
    profile: str,
    override: dict[str, str],
    expected: str | None,
) -> None:
    """DMT-3013 follows the severity profile and explicit per-code overrides."""
    codes = _check(
        "def fail() -> int:\n    raise RuntimeError()\n",
        tmp_path,
        config=CheckConfig(profile=profile, severity=override, symbol_kinds=frozenset({SymbolKind.FUNCTION})),
    )

    result_codes = codes.get("module.fail", {})
    assert result_codes.get("DMT-3013") == expected


def test_check_3012_respects_symbol_and_visibility_filters(tmp_path: Path) -> None:
    """The new finding uses Check's existing symbol and visibility gates."""
    source = "def _fail() -> int:\n    raise RuntimeError()\n"
    assert "DMT-3013" not in _check(source, tmp_path).get("module._fail", {})

    protected_codes = _check(
        source,
        tmp_path,
        config=CheckConfig(
            symbol_kinds=frozenset({SymbolKind.FUNCTION}),
            include_visibility=frozenset({Visibility.PUBLIC, Visibility.PROTECTED}),
        ),
    )
    assert "DMT-3013" in protected_codes["module._fail"]

    wrong_kind_codes = _check(
        source,
        tmp_path,
        config=CheckConfig(
            symbol_kinds=frozenset({SymbolKind.CLASS}),
            include_visibility=frozenset({Visibility.PUBLIC, Visibility.PROTECTED}),
        ),
    )
    assert "DMT-3013" not in wrong_kind_codes.get("module._fail", {})


def test_check_3012_survives_regression_filter_when_behavior_changes(tmp_path: Path) -> None:
    """The new no-normal-return finding is introduced when a previously returning function starts raising."""

    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=tmp_path,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()

    _git("init")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test")
    source = tmp_path / "module.py"
    source.write_text(
        'class DiffRange:\n    """A result value."""\n\n\n'
        'def resolve() -> DiffRange:\n    """Resolve the range."""\n    return DiffRange()\n',
        encoding="utf-8",
    )
    _git("add", ".")
    _git("commit", "-m", "base")
    base = _git("rev-parse", "HEAD")
    source.write_text(
        'class DiffRange:\n    """A result value."""\n\n\n'
        'def resolve() -> DiffRange:\n    """Resolve the range."""\n    raise RuntimeError()\n',
        encoding="utf-8",
    )
    _git("add", ".")
    _git("commit", "-m", "raise instead of returning")
    head = _git("rev-parse", "HEAD")

    excluded = run_check(
        tmp_path,
        git_diff=f"{base}..{head}",
        write_cache=False,
        config=CheckConfig(symbol_kinds=frozenset({SymbolKind.FUNCTION}), exclude_paths=("module.py",)),
    )
    assert "DMT-3013" not in {entry.code for entry in excluded.checks}

    result = run_check(
        tmp_path,
        git_diff=f"{base}..{head}",
        write_cache=False,
        config=CheckConfig(symbol_kinds=frozenset({SymbolKind.FUNCTION})),
    )

    codes = {entry.code for entry in result.checks if entry.symbol == "module.resolve"}
    assert "DMT-3013" in codes
    assert "DMT-3001" not in codes


def test_check_3012_is_rendered_by_all_current_output_formats(tmp_path: Path) -> None:
    """The new check-only code survives the JSON, GitHub, and text formatters."""
    entry = CheckEntry(
        severity="warning",
        code="DMT-3013",
        symbol="module.fail",
        file="module.py",
        line_start=4,
        col_start=1,
        message="No normal return path is detected for function annotated 'int'.",
        expected="int",
        observed="proven_absent",
        error_type="NormalReturnPathAbsent",
    )
    result = CheckResult(checks=[entry], summary=CheckSummary(warning_count=1), checked_files=["module.py"])
    json_path = tmp_path / "report.json"
    github_path = tmp_path / "annotations.txt"
    text_path = tmp_path / "report.txt"

    format_json(result, file=str(json_path))
    format_github(result, file=str(github_path))
    format_text(result, file=str(text_path), project_root=tmp_path, color="never")

    report = json.loads(json_path.read_text(encoding="utf-8"))
    check = report["checks"][0]
    assert check["code"] == "DMT-3013"
    assert check["expected"] == "int"
    assert check["observed"] == "proven_absent"
    assert "title=DMT-3013" in github_path.read_text(encoding="utf-8")
    assert "DMT-3013" in text_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "source",
    [
        "def fail() -> int:\n    try:\n        while True:\n            1 / 0\n    finally:\n        return 42\n",
        (
            "def fail() -> int:\n    while True:\n        try:\n            while True:\n                1 / 0\n"
            "        finally:\n            break\n    return 42\n"
        ),
        "def fail() -> int:\n    def inner(value=(yield 1)):\n        pass\n    raise RuntimeError()\n",
        (
            "import typing as t\nfrom types import SimpleNamespace as t\n"
            "def fail() -> t.Optional[int]:\n    raise RuntimeError()\n"
        ),
    ],
    ids=["finally-return", "finally-break", "nested-default-generator", "conflicting-imports"],
)
def test_check_does_not_report_a_false_normal_return_absence(source: str, tmp_path: Path) -> None:
    """Normal exits, generators and ambiguous annotation bindings cannot establish DMT-3013."""
    assert "DMT-3013" not in _check(source, tmp_path).get("module.fail", {})


@pytest.mark.parametrize(
    ("imports", "annotation", "expected"),
    [
        ("", "None", None),
        ("from typing import NoReturn as Exit", "Exit", None),
        ("from types import NoneType as Nil", "Nil", None),
        ("from application import NoReturn as Exit", "Exit", "error"),
    ],
)
def test_dmt3010_uses_verify_annotation_resolution(tmp_path: Path, imports: str, annotation: str, expected: str | None) -> None:
    """Check forwards import facts so Verify can decide applicability even with unknown flow."""
    source = f'''\
{imports}
@opaque_decorator
def fail() -> {annotation}:
    """Describe the result.

    Returns
    -------
        Result description without a type.
    """
    raise RuntimeError()
'''
    codes = _check(source, tmp_path, config=CheckConfig(severity={"DMT-3010": "error"}))
    assert codes.get("module.fail", {}).get("DMT-3010") == expected


@pytest.mark.parametrize(
    ("profile", "override", "expected"),
    [
        ("loose", {}, None),
        ("standard", {}, "warning"),
        ("strict", {}, "error"),
        ("strict", {"DMT-3010": "disabled"}, None),
        ("loose", {"DMT-3010": "error"}, "error"),
    ],
)
def test_dmt3010_applicable_diagnostics_keep_profile_and_override_policy(
    tmp_path: Path,
    profile: str,
    override: dict[str, str],
    expected: str | None,
) -> None:
    """User policy still controls the publication of an applicable missing return type."""
    codes = _check(
        '''
        def result() -> int:
            """Return a result.

            Returns
            -------
                Result description without a type.
            """
            return 1
        ''',
        tmp_path,
        config=CheckConfig(profile=profile, severity=override),
    )
    assert codes.get("module.result", {}).get("DMT-3010") == expected


def test_dmt3010_proven_absence_is_not_overridable(tmp_path: Path) -> None:
    """An error override cannot activate a type request that Verify deems inapplicable."""
    codes = _check(
        '''
        def fail() -> int:
            """Fail to produce a result.

            Returns
            -------
                No value is returned.
            """
            raise RuntimeError()
        ''',
        tmp_path,
        config=CheckConfig(severity={"DMT-3010": "error"}),
    )
    assert "DMT-3013" in codes["module.fail"]
    assert "DMT-3010" not in codes["module.fail"]
