# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Canonical pivot JSON formatter for the check module."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docmethis_check.models import CheckEntry, CheckResult, ImpactAnalysis

__all__ = ["format"]


def format(result: CheckResult, file: str | None = None) -> str:  # noqa: A001
    """Produce the check JSON report.

    Parameters
    ----------
    result : CheckResult
        Check result to format.
    file : str | None = None
        Output file path (stdout when None).

    Returns
    -------
    str
        The generated JSON.

    """
    data = {
        "version": result.version,
        "summary": {
            "checked_files": len(result.checked_files),
            "checks": {
                "pass": result.summary.pass_count,
                "warning": result.summary.warning_count,
                "error": result.summary.error_count,
                "total": result.summary.total_count,
            },
        },
        "checks": [_serialize_check(check) for check in result.checks],
        "diff": _serialize_diff(result),
        "regression_filter": _serialize_regression_filter(result),
    }
    if result.impact_analysis is not None:
        data["impact_analysis"] = _serialize_impact_analysis(result.impact_analysis)

    text = json.dumps(data, ensure_ascii=False, indent=2)

    if file:
        Path(file).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
        sys.stdout.write("\n")

    return text


def _serialize_check(check: CheckEntry) -> dict[str, object]:
    """Convert a CheckEntry to a JSON dict without empty fields.

    Parameters
    ----------
    check : CheckEntry
        The CheckEntry object to serialize.

    Returns
    -------
    dict[str, object]
        A dictionary of the check's data fields, excluding any fields whose value is None.

    """
    data = {
        "severity": check.severity,
        "code": check.code,
        "symbol_kind": check.symbol_kind.value,
        "symbol": check.symbol,
        "file": check.file,
        "line_start": check.line_start,
        "col_start": check.col_start,
        "message": check.message,
    }
    if check.accessor_kind is not None:
        data["accessor_kind"] = check.accessor_kind
    for name in ("visibility", "owner_symbol", "attribute_name", "line_end", "col_end"):
        value = getattr(check, name)
        if value is not None:
            data[name] = value
    if check.docstring_line is not None:
        data["docstring_line"] = check.docstring_line
    if check.docstring_span is not None:
        data["docstring_span"] = {"start": check.docstring_span[0], "end": check.docstring_span[1]}
    if check.discriminator is not None:
        data["discriminator"] = check.discriminator

    structured_fields = {
        "section": check.section,
        "parameter_name": check.parameter_name,
        "expected": check.expected,
        "observed": check.observed,
        "granularity": check.granularity,
        "error_type": check.error_type,
        "docstring_sha256": check.docstring_sha256,
    }
    data.update({name: value for name, value in structured_fields.items() if value is not None})
    if check.dia is not None:
        data["dia"] = check.dia
    return data


def _serialize_impact_analysis(impact_analysis: ImpactAnalysis) -> dict[str, object]:
    """Serialize the top-level DIA block without empty fields.

    Parameters
    ----------
    impact_analysis : ImpactAnalysis
        The ImpactAnalysis instance containing the data to be serialized into the top-level DIA block, excluding empty fields.

    Returns
    -------
    dict[str, object]
        A dictionary representing the serialized impact analysis, containing the completeness value and, when present, the reason,
        impacts, and api_diff values.

    """
    data: dict[str, object] = {"completeness": impact_analysis.completeness}
    if impact_analysis.reason is not None:
        data["reason"] = impact_analysis.reason
    if impact_analysis.impacts:
        data["impacts"] = impact_analysis.impacts
    if impact_analysis.api_diff:
        data["api_diff"] = impact_analysis.api_diff
    return data


def _serialize_diff(result: CheckResult) -> dict[str, object] | None:
    """Serialize diff metadata when available.

    Parameters
    ----------
    result : CheckResult
        The CheckResult instance whose diff metadata (strategy, completeness, reason, and files) is serialized.

    Returns
    -------
    dict[str, object] | None
        A dictionary containing the diff strategy and completeness, optionally including reason and files when present, or None if
        no diff strategy is available.

    """
    if result.diff_strategy is None:
        return None
    data: dict[str, object] = {
        "strategy": result.diff_strategy,
        "completeness": result.diff_completeness,
    }
    if result.diff_reason is not None:
        data["reason"] = result.diff_reason
    if result.diff_files:
        data["files"] = result.diff_files
    return data


def _serialize_regression_filter(result: CheckResult) -> dict[str, object] | None:
    """Serialize regression filtering state when degradation was observed.

    Parameters
    ----------
    result : CheckResult
        The check result containing the regression filtering state to be serialized.

    Returns
    -------
    dict[str, object] | None
        Returns a dictionary describing the regression filtering state when degradation was observed, containing the completeness
        value and, if available, the reason; returns None if no regression filter completeness information is present.

    """
    if result.regression_filter_completeness is None:
        return None
    data: dict[str, object] = {
        "completeness": result.regression_filter_completeness,
    }
    if result.regression_filter_reason is not None:
        data["reason"] = result.regression_filter_reason
    return data
