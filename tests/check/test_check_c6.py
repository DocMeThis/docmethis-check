# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Tests C6 of module check."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from docmethis_extract_python.static_extraction.models import (
    MISSING_VALUE,
    Confidence,
    FunctionRecord,
    MethodType,
    Parameter,
    ParameterType,
    Provenance,
    ResolvedTypes,
    Signature,
    TypeInfo,
    Visibility,
)
from docmethis_verify.models.enums import PatchGranularity, VerificationErrorType

from docmethis_check.config import CheckConfig, load_check_config
from docmethis_check.diagnostics import diagnostics_for_file, diagnostics_for_function
from docmethis_check.formatters.github import format as format_github
from docmethis_check.models import AnnotationPlacement, CheckEntry, CheckResult, SymbolKind
from docmethis_check.runner import _filter_regression, _increment_summary

if TYPE_CHECKING:
    from pathlib import Path


def _function_without_docstring(tmp_path: Path) -> FunctionRecord:
    """Build a minimal FunctionRecord without an existing docstring."""
    source_file = tmp_path / "module.py"
    source_file.write_text("def foo():\n    pass\n", encoding="utf-8")
    return FunctionRecord(
        qualified_name="module.foo",
        file_path=source_file,
        line_start=1,
        line_end=2,
        col_start=0,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class=None,
        parent_module="module",
        existing_docstring=None,
    )


def _function_with_docstring(tmp_path: Path) -> FunctionRecord:
    """Build a minimal FunctionRecord with an existing docstring."""
    source_file = tmp_path / "module.py"
    source_file.write_text('def foo():\n    """Summary."""\n    pass\n', encoding="utf-8")
    return FunctionRecord(
        qualified_name="module.foo",
        file_path=source_file,
        line_start=1,
        line_end=3,
        col_start=0,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class=None,
        parent_module="module",
        existing_docstring="Summary.",
        docstring_line_start=2,
    )


def _function_without_summary(tmp_path: Path, *, method: bool = False) -> FunctionRecord:
    """Build a function or method with a NumPy section but no summary."""
    source_file = tmp_path / ("method.py" if method else "module.py")
    source = "class Service:\n    def foo(self):\n        pass\n" if method else "def foo():\n    pass\n"
    source_file.write_text(source, encoding="utf-8")
    return FunctionRecord(
        qualified_name="module.Service.foo" if method else "module.foo",
        file_path=source_file,
        line_start=2 if method else 1,
        line_end=3 if method else 2,
        col_start=4 if method else 0,
        col_end=12 if method else 1,
        method_kind=MethodType.INSTANCE_METHOD if method else MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class="module.Service" if method else None,
        parent_module="module",
        existing_docstring="Parameters\n----------\n",
        docstring_line_start=3 if method else 2,
    )


def _function_with_line_parameter(tmp_path: Path) -> FunctionRecord:
    """Build a function with a signature whose parameter line can be located."""
    function = _function_with_docstring(tmp_path)
    function.signature = Signature(
        parameters=[
            Parameter(
                name="value",
                annotation_raw="int",
                default_value=MISSING_VALUE,
                kind=ParameterType.POSITIONAL_OR_KEYWORD,
                line_start=4,
            ),
        ],
        return_annotation=MISSING_VALUE,
        decorators=[],
    )
    return function


def _function_with_misspelled_returns(tmp_path: Path) -> FunctionRecord:
    """Build a function whose Returns section contains a likely typo."""
    function = _function_with_docstring(tmp_path)
    function.existing_docstring = """Summary.

Retruns
-------
int
    Value computed.
"""
    function.types = ResolvedTypes(
        parameters={},
        return_type=TypeInfo(type_str="int", confidence=Confidence.EXPLICIT, provenance=Provenance.ANNOTATION),
    )
    return function


def _function_with_misspelled_returns_colon(tmp_path: Path) -> FunctionRecord:
    """Build a function whose Returns section contains a typo and a colon."""
    function = _function_with_docstring(tmp_path)
    function.existing_docstring = """Summary.

Retruns:
--------
int
    Value computed.
"""
    function.types = ResolvedTypes(
        parameters={},
        return_type=TypeInfo(type_str="int", confidence=Confidence.EXPLICIT, provenance=Provenance.ANNOTATION),
    )
    return function


def _function_with_undocumented_return(tmp_path: Path) -> FunctionRecord:
    """Build a typed function without a Returns section."""
    function = _function_with_docstring(tmp_path)
    function.types = ResolvedTypes(
        parameters={},
        return_type=TypeInfo(type_str="int", confidence=Confidence.EXPLICIT, provenance=Provenance.ANNOTATION),
    )
    return function


def _function_with_misspelled_parameters(tmp_path: Path) -> FunctionRecord:
    """Build a function whose Parameters section contains a likely typo."""
    function = _function_with_docstring(tmp_path)
    function.existing_docstring = """Summary.

Paramters
---------
            value : int
    Value received.
"""
    function.signature = Signature(
        parameters=[
            Parameter(
                name="value",
                annotation_raw="int",
                default_value=MISSING_VALUE,
                kind=ParameterType.POSITIONAL_OR_KEYWORD,
                line_start=1,
            ),
        ],
        return_annotation=MISSING_VALUE,
        decorators=[],
    )
    return function


def _function_with_misspelled_parameters_colon(tmp_path: Path) -> FunctionRecord:
    """Build a function whose Parameters section contains a typo and a colon."""
    function = _function_with_docstring(tmp_path)
    function.existing_docstring = """Summary.

Paramters:
----------
            value : int
    Value received.
"""
    function.signature = Signature(
        parameters=[
            Parameter(
                name="value",
                annotation_raw="int",
                default_value=MISSING_VALUE,
                kind=ParameterType.POSITIONAL_OR_KEYWORD,
                line_start=1,
            ),
        ],
        return_annotation=MISSING_VALUE,
        decorators=[],
    )
    return function


def test_config_accepts_disabled_severity(tmp_path: Path) -> None:
    """The configuration accepts disabled as a diagnostic severity."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.docmethis.check.severity]\nDMT-1120 = "disabled"\n',
        encoding="utf-8",
    )

    config = load_check_config(tmp_path)

    assert config.severity_for("DMT-1120", "error") == "disabled"


def test_config_rejects_unknown_severity(tmp_path: Path) -> None:
    """An unknown severity remains rejected with an explicit message."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.docmethis.check.severity]\nDMT-1120 = "silent"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="disabled"):
        load_check_config(tmp_path)


def test_disabled_diagnostic_produces_no_entry_or_key(tmp_path: Path) -> None:
    """A disabled diagnostic is absent from the report and regression filter."""
    config = CheckConfig(severity={"DMT-1120": "disabled"})

    entries, keys = diagnostics_for_function(_function_without_docstring(tmp_path), config)

    assert entries == []
    assert keys == {}


def test_without_docstring_has_no_fingerprint(tmp_path: Path) -> None:
    """A missing docstring provides no correction target to verify."""
    entries, _keys = diagnostics_for_function(_function_without_docstring(tmp_path), CheckConfig())

    assert [(entry.code, entry.docstring_sha256) for entry in entries] == [("DMT-1120", None)]


def test_increment_summary_ignores_disabled() -> None:
    """A disabled severity does not modify summary counters."""
    outcome = CheckResult()

    _increment_summary(outcome, "disabled")

    assert outcome.summary.pass_count == 0
    assert outcome.summary.warning_count == 0
    assert outcome.summary.error_count == 0


def test_warning_format_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An Verify warning mapped to a disabled code is absent from checks by default."""
    monkeypatch.setattr(
        "docmethis_check.diagnostics.verify_docstring",
        lambda _: SimpleNamespace(
            errors=(),
            warnings=(
                SimpleNamespace(
                    section="Parameters",
                    kind="missing_blank_line",
                    detail="blank line missing",
                    discriminator=None,
                ),
            ),
        ),
    )

    entries, keys = diagnostics_for_function(_function_with_docstring(tmp_path), CheckConfig())

    assert entries == []
    assert keys == {}


def test_warning_format_is_enabled_by_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An Verify warning disabled by default becomes visible when its severity is configured."""
    monkeypatch.setattr(
        "docmethis_check.diagnostics.verify_docstring",
        lambda _: SimpleNamespace(
            errors=(),
            warnings=(
                SimpleNamespace(
                    section="Parameters",
                    kind="missing_blank_line",
                    detail="blank line missing",
                    discriminator=None,
                ),
            ),
        ),
    )

    entries, keys = diagnostics_for_function(_function_with_docstring(tmp_path), CheckConfig(severity={"DMT-6051": "warning"}))

    assert [(entry.code, entry.severity, entry.message) for entry in entries] == [
        ("DMT-6051", "warning", "blank line missing"),
    ]
    assert next(iter(keys)).code == "DMT-6051"


@pytest.mark.parametrize("method", [False, True])
def test_empty_summary_is_reported_for_functions_and_methods(tmp_path: Path, method: bool) -> None:
    """The shared empty-summary warning maps to DMT-7001 for both symbol kinds."""
    function = _function_without_summary(tmp_path, method=method)

    entries, keys = diagnostics_for_function(function, CheckConfig(severity={"DMT-7001": "warning"}))

    assert [(entry.code, entry.symbol_kind, entry.symbol, entry.section) for entry in entries] == [
        (
            "DMT-7001",
            SymbolKind.METHOD if method else SymbolKind.FUNCTION,
            function.qualified_name,
            "Summary",
        ),
    ]
    assert keys[entries[0].diagnostic_key] == 1


def test_parameter_diagnostic_reports_annotation_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An Verify parameter diagnostic points to the line extracted by M1."""
    monkeypatch.setattr(
        "docmethis_check.diagnostics.verify_docstring",
        lambda _: SimpleNamespace(
            errors=(
                SimpleNamespace(
                    error_type=VerificationErrorType.PARAMETER_MISSING,
                    dmt_code="DMT-2001",
                    section="Parameters",
                    parameter_name="value",
                    expected="value",
                    observed=None,
                    granularity=PatchGranularity.ENTRY,
                    detail="parameter not documented",
                ),
            ),
            warnings=(),
        ),
    )

    entries, _keys = diagnostics_for_function(_function_with_line_parameter(tmp_path), CheckConfig())

    assert entries[0].annotation_line == 4
    assert entries[0].section == "Parameters"
    assert entries[0].parameter_name == "value"
    assert entries[0].expected == "value"
    assert entries[0].observed is None
    assert entries[0].granularity == "entry"
    assert entries[0].error_type == "ParameterMissing"
    assert entries[0].docstring_sha256 == hashlib.sha256(b"Summary.").hexdigest()


def test_diagnostics_verification_duplicate_codes_keep_distinct_targets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two DMT-2001 entries remain distinguishable in the report pivot."""
    monkeypatch.setattr(
        "docmethis_check.diagnostics.verify_docstring",
        lambda _: SimpleNamespace(
            errors=(
                SimpleNamespace(
                    error_type=VerificationErrorType.PARAMETER_MISSING,
                    dmt_code="DMT-2001",
                    section="Parameters",
                    parameter_name="value",
                    expected="value",
                    observed=None,
                    granularity=PatchGranularity.ENTRY,
                    detail="parameter 'value' not documented",
                ),
                SimpleNamespace(
                    error_type=VerificationErrorType.PARAMETER_MISSING,
                    dmt_code="DMT-2001",
                    section="Parameters",
                    parameter_name="other",
                    expected="other",
                    observed=None,
                    granularity=PatchGranularity.ENTRY,
                    detail="parameter 'other' not documented",
                ),
            ),
            warnings=(),
        ),
    )
    function = _function_with_line_parameter(tmp_path)
    assert function.signature is not None
    function.signature.parameters.append(
        Parameter(
            name="other",
            annotation_raw="int",
            default_value=MISSING_VALUE,
            kind=ParameterType.POSITIONAL_OR_KEYWORD,
            line_start=5,
        )
    )

    entries, keys = diagnostics_for_function(function, CheckConfig())

    assert [(entry.code, entry.parameter_name, entry.expected, entry.granularity) for entry in entries] == [
        ("DMT-2001", "value", "value", "entry"),
        ("DMT-2001", "other", "other", "entry"),
    ]
    assert len(keys) == 2


def test_diagnostics_verification_duplicate_codes_same_target_keep_cardinality(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two occurrences of the same target keep one key and two C8 entries."""
    monkeypatch.setattr(
        "docmethis_check.diagnostics.verify_docstring",
        lambda _: SimpleNamespace(
            errors=(
                SimpleNamespace(
                    error_type=VerificationErrorType.PARAMETER_MISSING,
                    dmt_code="DMT-2001",
                    section="Parameters",
                    parameter_name="value",
                    expected="value",
                    observed=None,
                    granularity=PatchGranularity.ENTRY,
                    detail="first occurrence of 'value' not documented",
                ),
                SimpleNamespace(
                    error_type=VerificationErrorType.PARAMETER_MISSING,
                    dmt_code="DMT-2001",
                    section="Parameters",
                    parameter_name="value",
                    expected="value",
                    observed=None,
                    granularity=PatchGranularity.ENTRY,
                    detail="second occurrence of 'value' not documented",
                ),
            ),
            warnings=(),
        ),
    )

    entries, keys = diagnostics_for_function(_function_with_line_parameter(tmp_path), CheckConfig())

    assert [(entry.code, entry.parameter_name, entry.expected, entry.granularity) for entry in entries] == [
        ("DMT-2001", "value", "value", "entry"),
        ("DMT-2001", "value", "value", "entry"),
    ]
    assert entries[0].diagnostic_key == entries[1].diagnostic_key
    assert len(keys) == 1
    assert next(iter(keys.values())) == 2


def test_docstring_hash_change_does_not_affect_regression_filter(tmp_path: Path) -> None:
    """A prose change keeps the same diagnostic key for regression filtering."""
    function = _function_with_undocumented_return(tmp_path)
    function.existing_docstring = "Summary initial."
    entries_base, keys_base = diagnostics_for_function(function, CheckConfig())
    function.existing_docstring = "Summary changed."
    entries_head, keys_head = diagnostics_for_function(function, CheckConfig())

    assert entries_base[0].docstring_sha256 != entries_head[0].docstring_sha256
    assert keys_base == keys_head
    assert _filter_regression(entries_head, keys_head, keys_base) == []


def test_github_format_respects_annotation_placement(tmp_path: Path) -> None:
    """The GitHub formatter selects the line according to the configured mode."""
    outcome = CheckResult(
        checks=[
            CheckEntry(
                severity="warning",
                code="DMT-2001",
                symbol="module.foo",
                file="module.py",
                line_start=3,
                col_start=1,
                message="parameter not documented",
                docstring_line=12,
                annotation_line=42,
            ),
        ],
    )

    signature = format_github(outcome, file=str(tmp_path / "signature.txt"))
    docstring = format_github(
        outcome,
        file=str(tmp_path / "docstring.txt"),
        annotation_placement=AnnotationPlacement.DOCSTRING,
    )
    precise = format_github(
        outcome,
        file=str(tmp_path / "precise.txt"),
        annotation_placement=AnnotationPlacement.PRECISE,
    )

    assert "line=3" in signature
    assert "line=12" in docstring
    assert "line=42" in precise


def test_dmt230_precise_targets_signature(tmp_path: Path) -> None:
    """In precise mode, DMT-3001 targets the signature rather than the docstring."""
    entries, _keys = diagnostics_for_function(_function_with_undocumented_return(tmp_path), CheckConfig())

    assert [
        (
            entry.code,
            entry.annotation_line,
            entry.docstring_line,
            entry.section,
            entry.expected,
            entry.granularity,
            entry.error_type,
        )
        for entry in entries
    ] == [("DMT-3001", 1, 2, "Returns", "int", "section", "ReturnsMissing")]
    assert entries[0].docstring_sha256 == hashlib.sha256(b"Summary.").hexdigest()

    output = format_github(
        CheckResult(checks=entries),
        file=str(tmp_path / "annotations.txt"),
        annotation_placement=AnnotationPlacement.PRECISE,
    )

    assert "line=1" in output
    assert "line=2" not in output


def test_misspelled_returns_emits_dmt401_without_dmt230(tmp_path: Path) -> None:
    """A likely Returns typo reports the root cause instead of DMT-3001."""
    entries, _keys = diagnostics_for_function(_function_with_misspelled_returns(tmp_path), CheckConfig())

    assert [entry.code for entry in entries] == ["DMT-6049"]
    assert "Retruns" in entries[0].message


def test_misspelled_returns_with_colon_emits_dmt401_without_dmt230(tmp_path: Path) -> None:
    """A likely Returns typo with a colon also suppresses DMT-3001."""
    entries, _keys = diagnostics_for_function(_function_with_misspelled_returns_colon(tmp_path), CheckConfig())

    assert [entry.code for entry in entries] == ["DMT-6049"]
    assert "Retruns:" in entries[0].message
    assert entries[0].docstring_line == 4
    assert entries[0].annotation_line == 4


def test_dmt401_targets_misspelled_section_title_from_file(tmp_path: Path) -> None:
    """DMT-6049 targets the absolute line of the misspelled title extracted by M1."""
    source_file = tmp_path / "module.py"
    payload = '''def foo() -> int:
    """Summary.

    Retruns:
    --------
    int
    """
    return 1
'''

    entries, _keys = diagnostics_for_file(
        payload,
        source_file,
        tmp_path,
        CheckConfig(symbol_kinds=frozenset({SymbolKind.FUNCTION})),
    )

    assert [(entry.code, entry.docstring_line, entry.annotation_line) for entry in entries] == [("DMT-6049", 4, 4)]


def test_misspelled_parameters_emits_dmt401_without_dmt2001(tmp_path: Path) -> None:
    """A likely Parameters typo suppresses downstream parameter diagnostics."""
    entries, _keys = diagnostics_for_function(_function_with_misspelled_parameters(tmp_path), CheckConfig())

    assert [entry.code for entry in entries] == ["DMT-6049"]
    assert "Paramters" in entries[0].message


def test_misspelled_parameters_with_colon_emits_dmt401_without_dmt2001(tmp_path: Path) -> None:
    """A likely Parameters typo with a colon also suppresses DMT-2001."""
    entries, _keys = diagnostics_for_function(_function_with_misspelled_parameters_colon(tmp_path), CheckConfig())

    assert [entry.code for entry in entries] == ["DMT-6049"]
    assert "Paramters:" in entries[0].message
    assert entries[0].docstring_line == 4
    assert entries[0].annotation_line == 4
