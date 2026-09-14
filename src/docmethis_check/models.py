# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Data models for the check module."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class CheckMode(StrEnum):
    """Check verification mode.

    Attributes
    ----------
    REGRESSION : str
        Regression mode: only symbols affected by the diff are checked.
    CATCHUP : str
        Catch-up mode: all symbols are checked.
    SURVEY : str
        Survey mode: reserved, not implemented yet.

    """

    REGRESSION = "regression"
    CATCHUP = "catchup"
    SURVEY = "survey"


class AnnotationPlacement(StrEnum):
    """Diagnostic annotation placement for output formatters.

    Attributes
    ----------
    SIGNATURE : str
        Annotation placed on the symbol signature or definition.
    DOCSTRING : str
        Annotation placed on the docstring.
    PRECISE : str
        Annotation placed on the most precise known source location.

    """

    SIGNATURE = "signature"
    DOCSTRING = "docstring"
    PRECISE = "precise"


class OnMissingBase(StrEnum):
    """Behavior when the base revision is unavailable.

    Attributes
    ----------
    EMIT_ALL : str
        Emit diagnostics for all symbols.
    FAIL : str
        Fail the check run.

    """

    EMIT_ALL = "emit_all"
    FAIL = "fail"


class MethodExceptionContract(StrEnum):
    """Documentation owner for exceptions exposed by class methods."""

    CALLABLE = "callable"
    CLASS_AGGREGATE = "class_aggregate"


class SymbolKind(StrEnum):
    """Symbol type checked by the check module.

    Attributes
    ----------
    FUNCTION : str
        Module-level function.
    METHOD : str
        Class method.
    CLASS : str
        Class.
    MODULE : str
        Module.

    """

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    MODULE = "module"


Severity = Literal["error", "warning", "disabled"]


@dataclass
class CheckEntry:
    """A detected inconsistency.

    Attributes
    ----------
    severity : Severity
        Severity of the check entry.
    code : str
        DMT diagnostic code.
    symbol : str
        Qualified name of the diagnosed symbol.
    file : str
        Project-relative path of the file the entry applies to.
    line_start : int
        Starting line of the diagnosed symbol.
    col_start : int
        Starting column of the diagnosed symbol.
    message : str
        Human-readable diagnostic message.
    symbol_kind : SymbolKind
        Kind of the diagnosed symbol.
    accessor_kind : str | None
        Property accessor role, when the diagnostic targets a getter or setter.
    visibility : str | None
        Effective visibility of the diagnosed symbol.
    owner_symbol : str | None
        Qualified owner symbol for member diagnostics.
    attribute_name : str | None
        Exact attribute name for class-attribute diagnostics.
    line_end : int | None
        Ending line of the diagnosed target, when known.
    col_end : int | None
        Ending column of the diagnosed target, when known.
    docstring_line : int | None
        Line of the docstring the entry applies to, when known.
    docstring_span : tuple[int, int] | None
        Half-open character bounds of the warning in the normalized docstring body, when known.
    annotation_line : int | None
        Line the annotation should be placed on, when known.
    section : str | None
        Docstring section the entry applies to, when known.
    parameter_name : str | None
        Parameter the entry applies to, when known.
    expected : str | None
        Expected value, when known.
    observed : str | None
        Observed value, when known.
    granularity : str | None
        Granularity of the fix, when known.
    error_type : str | None
        Structured error type, when known.
    docstring_sha256 : str | None
        SHA-256 digest of the docstring body, when known.
    diagnostic_key : object | None
        Stable diagnostic key used for regression filtering.
    dia : dict[str, object] | None
        DIA impact block attached to the entry, when emitted.

    """

    severity: Severity
    code: str
    symbol: str
    file: str
    line_start: int
    col_start: int
    message: str
    symbol_kind: SymbolKind = SymbolKind.FUNCTION
    accessor_kind: str | None = None
    visibility: str | None = None
    owner_symbol: str | None = None
    attribute_name: str | None = None
    line_end: int | None = None
    col_end: int | None = None
    docstring_line: int | None = None
    annotation_line: int | None = None
    section: str | None = None
    parameter_name: str | None = None
    expected: str | None = None
    observed: str | None = None
    granularity: str | None = None
    error_type: str | None = None
    docstring_sha256: str | None = None
    diagnostic_key: object | None = None
    dia: dict[str, object] | None = None
    discriminator: str | None = None
    docstring_span: tuple[int, int] | None = None


def annotation_line(check: CheckEntry, placement: AnnotationPlacement) -> int:
    """Return the annotation line selected by a placement policy."""
    if placement is AnnotationPlacement.DOCSTRING:
        return check.docstring_line if check.docstring_line is not None else check.line_start

    if placement is AnnotationPlacement.PRECISE:
        if check.annotation_line is not None:
            return check.annotation_line
        return check.docstring_line if check.docstring_line is not None else check.line_start

    return check.line_start


@dataclass
class ImpactAnalysis:
    """Top-level DIA report block: global state, impacts, and declarative diff.

    Attributes
    ----------
    completeness : str
        Completeness indicator of the impact analysis.
    reason : str | None
        Reason for a partial or missing analysis, when relevant.
    impacts : list[dict[str, object]]
        List of serialized documentation impacts.
    api_diff : list[dict[str, object]]
        List of serialized declarative signature changes.

    """

    completeness: str
    reason: str | None = None
    impacts: list[dict[str, object]] = field(default_factory=list)
    api_diff: list[dict[str, object]] = field(default_factory=list)


@dataclass
class CheckSummary:
    """Check summary.

    Attributes
    ----------
    pass_count : int
        Number of passed checks.
    warning_count : int
        Number of warning checks.
    error_count : int
        Number of error checks.

    """

    pass_count: int = 0
    warning_count: int = 0
    error_count: int = 0

    @property
    def total_count(self) -> int:
        """Return the total number of checks performed.

        Returns
        -------
        int
            The total number of checks performed, equal to the sum of pass, warning, and error counts.

        """
        return self.pass_count + self.warning_count + self.error_count


@dataclass
class CheckResult:
    """Complete result of a check run.

    Attributes
    ----------
    version : int
        Report schema version.
    summary : CheckSummary
        Aggregated check counts.
    checks : list[CheckEntry]
        List of emitted check entries.
    checked_files : list[str]
        Paths of the files that were checked.
    analysis_errors : list[dict[str, str]]
        Text-only analysis failures for files that could not be checked. These
        are intentionally excluded from the canonical JSON v1 payload.
    diff_strategy : str | None
        Strategy used to resolve the diff range, when known.
    diff_completeness : str | None
        Completeness of the diff range, when known.
    diff_reason : str | None
        Reason for the diff resolution, when known.
    regression_filter_completeness : str | None
        Completeness of the regression filter, when known.
    regression_filter_reason : str | None
        Reason for regression filtering degradation, when known.
    diff_files : list[dict[str, object]]
        Serialized changed files with their affected lines.
    impact_analysis : ImpactAnalysis | None
        DIA impact analysis block, when enabled and available.

    """

    version: int = 1
    summary: CheckSummary = field(default_factory=CheckSummary)
    checks: list[CheckEntry] = field(default_factory=list)
    checked_files: list[str] = field(default_factory=list)
    analysis_errors: list[dict[str, str]] = field(default_factory=list)
    diff_strategy: str | None = None
    diff_completeness: str | None = None
    diff_reason: str | None = None
    regression_filter_completeness: str | None = None
    regression_filter_reason: str | None = None
    diff_files: list[dict[str, object]] = field(default_factory=list)
    impact_analysis: ImpactAnalysis | None = None
