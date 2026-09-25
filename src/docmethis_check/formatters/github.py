# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""GitHub Actions annotation formatter."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from docmethis_check.models import AnnotationPlacement, annotation_line

if TYPE_CHECKING:
    from docmethis_check.models import CheckEntry, CheckResult

__all__ = ["format"]


def _relativize(path: str) -> str:
    """Return the path relative to the GitHub workspace when applicable.

    Parameters
    ----------
    path : str
        The path to be relativized against the GitHub workspace when applicable.

    Returns
    -------
    str
        The input path converted to a POSIX-style relative path when it is located under the GitHub workspace directory;
        otherwise, the original path is returned unchanged.

    """
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace and path.startswith(workspace):
        try:
            return Path(path).relative_to(workspace).as_posix()
        except ValueError:
            pass
    return path


def format(  # noqa: A001
    result: CheckResult,
    file: str | None = None,
    *,
    annotation_placement: AnnotationPlacement = AnnotationPlacement.SIGNATURE,
) -> str:
    """Produce GitHub Actions annotations for a check report.

    Parameters
    ----------
    result : CheckResult
        The check report to be formatted into GitHub Actions annotations.
    file : str | None = None
        Optional path to a file where the generated annotations should be written; if omitted, output is written to stdout.
    annotation_placement : AnnotationPlacement
        Controls where the GitHub Actions annotation is placed in the formatted output.

    Returns
    -------
    str
        The formatted annotation text as a string, representing the complete GitHub Actions annotation output that was written to
        the specified file or standard output.

    """
    lines = _format_impact_analysis(result)
    lines.extend(_format_check(check, annotation_placement) for check in result.checks)
    if result.diff_completeness == "none":
        reason = result.diff_reason or "missing_diff_base"
        message = f"DocMeThis analysis skipped: no reliable Git base or common ancestor could be determined ({reason})."
        lines.insert(0, f"::warning title=DocMeThis inconclusive diff::{_escape_message(message)}")
    text = "\n".join(lines) + "\n" if lines else ""

    if file is not None:
        Path(file).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)

    return text


def _format_impact_analysis(result: CheckResult) -> list[str]:
    """Render the ``Public Contract Diff`` notice at the top of the annotations file.

    Parameters
    ----------
    result : CheckResult
        The CheckResult object to format; its impact_analysis, checks, and api_diff attributes are used to render the Public
        Contract Diff notice.

    Returns
    -------
    list[str]
        Returns a list of strings containing GitHub Actions notice commands that render the Public Contract Diff (DocMeThis DIA)
        report, or an empty list when there is no impact analysis or nothing to report.

    """
    analysis = result.impact_analysis
    if analysis is None:
        return []

    if analysis.completeness == "inconclusive":
        reason = analysis.reason or "missing_base"
        message = f"Documentation impact analysis not run ({reason})."
        return [f"::notice title=DocMeThis DIA inconclusive::{_escape_message(message)}"]

    dia_entries = [check for check in result.checks if check.dia is not None]
    changed_unverified_impacts = [impact for impact in analysis.impacts if impact.get("status") == "changed_unverified"]
    if not dia_entries and not changed_unverified_impacts and not analysis.api_diff:
        return []

    content = ["Public Contract Diff (DocMeThis DIA):"]
    content.extend(_dia_block_line(check) for check in dia_entries)
    content.extend(
        (
            f"- {impact['symbol']}: {impact['impact_kind']} {impact['value']} - potential "
            f"{_unverified_impact_label(impact.get('symbol_context'))}"
        )
        for impact in changed_unverified_impacts
    )
    for change in analysis.api_diff:
        label = str(change["aspect"])
        if (parameter := change.get("parameter")) is not None:
            label = f"{label} ({parameter})"
        if "before" in change or "after" in change:
            before = change.get("before", "absent")
            after = change.get("after", "absent")
            label = f"{label} ({before} -> {after})"
        content.append(f"- {change['symbol']}: {label}")

    return [f"::notice title=DocMeThis DIA::{_escape_message(chr(10).join(content))}"]


def _dia_block_line(check: CheckEntry) -> str:
    """Render a DIA block line: symbol, fact, label, fixability, and evidence.

    Parameters
    ----------
    check : CheckEntry
        The CheckEntry object containing the DIA data to render into a block line.

    Returns
    -------
    str
        Render a DIA block line for a check entry, including symbol, code, severity, status label, fixability indicator,
        before/after evidence, symbol context, and evidence path when present.

    """
    dia = check.dia or {}
    status = str(dia.get("status", ""))
    label = _status_label(status)
    fixable = " — fixable" if dia.get("fixable") else ""
    line = f"- {check.symbol}: {check.code} ({check.severity})"
    if label:
        line = f"{line} — {label}"
    line = f"{line}{fixable}"
    evidence = dia.get("evidence") or {}
    if evidence.get("before") is not None or evidence.get("after") is not None:
        line = f"{line} — {evidence.get('before')} -> {evidence.get('after')}"
    line = f"{line}{_visible_context(dia.get('symbol_context'))}"
    path = evidence.get("path")
    if path:
        line = f"{line}\n  evidence: {' -> '.join(str(segment) for segment in path)}"
    return line


def _visible_context(context: object) -> str:
    """Render BASE/HEAD context without overloading the GitHub line.

    Parameters
    ----------
    context : object
        A dictionary holding the BASE and HEAD revision contexts, each containing documentation origin, dispatch role,
        implementation kind, and inherited contract source.

    Returns
    -------
    str
        Returns a formatted string summarizing the base and head context entries, or an empty string if the input is not a
        dictionary or contains no usable base/head entries.

    """
    if not isinstance(context, dict):
        return ""
    fragments: list[str] = []
    for revision in ("base", "head"):
        value = context.get(revision)
        if not isinstance(value, dict):
            continue
        source = value.get("inherited_contract_source") or "-"
        fragments.append(
            f"{revision}: doc={value.get('documentation_origin')}, "
            f"dispatch={value.get('dispatch_role')}, impl={value.get('implementation_kind')}, "
            f"contract={source}"
        )
    return f" — context ({'; '.join(fragments)})" if fragments else ""


def _unverified_impact_label(context: object) -> str:
    """Describe an unverifiable impact without inventing a local change.

    Parameters
    ----------
    context : object
        The documentation context object that the function inspects to determine whether the documentation origin is unknown or
        changed.

    Returns
    -------
    str
        A string label indicating that the impact cannot be verified, with the specific wording depending on whether the
        documentation origin is unknown or changed.

    """
    if _unknown_documentation_origin(context):
        return "(documentation contract unresolved, relationship unverifiable)"
    return "(documentation contract changed, relationship unverifiable)"


def _unknown_documentation_origin(context: object) -> bool:
    """Return whether a BASE/HEAD view cannot resolve the contract.

    Parameters
    ----------
    context : object
        The object to inspect for unknown documentation origins. When it is a dict, each value that is a dict is checked for a
        'documentation_origin' key equal to 'unknown'.

    Returns
    -------
    bool
        Return whether a BASE/HEAD view cannot resolve the contract.

    """
    if not isinstance(context, dict):
        return False
    return any(isinstance(value, dict) and value.get("documentation_origin") == "unknown" for value in context.values())


def _status_label(status: str) -> str | None:
    """Return the conventional rendering label for a documentation status.

    Parameters
    ----------
    status : str
        The documentation status string to map to its conventional rendering label.

    Returns
    -------
    str | None
        The conventional rendering label for the given status, or None if the status has no conventional label.

    """
    if status == "contradiction":
        return "confirmed"
    if status == "documentation_unchanged":
        return "observed"
    if status == "changed_unverified":
        return "potential"
    return None


def _format_check(check: CheckEntry, annotation_placement: AnnotationPlacement) -> str:
    """Convert a diagnostic into a GitHub workflow command.

    Parameters
    ----------
    check : CheckEntry
        The check entry containing the diagnostic information to be formatted into a GitHub workflow command.
    annotation_placement : AnnotationPlacement
        The annotation placement strategy that determines how the line number is resolved for the GitHub workflow command.

    Returns
    -------
    str
        The formatted GitHub workflow command string representing the diagnostic.

    """
    level = "error" if check.severity == "error" else "warning"
    line = annotation_line(check, annotation_placement)
    properties = (
        f"file={_escape_property(_relativize(check.file))},line={line},col={check.col_start},title={_escape_property(check.code)}"
    )
    return f"::{level} {properties}::{_escape_message(check.message)} ({check.code})."


def _escape_message(value: str) -> str:
    """Escape a message value according to the GitHub Actions protocol.

    Parameters
    ----------
    value : str
        The message string to escape for GitHub Actions output.

    Returns
    -------
    str
        The escaped message string, with percent signs, carriage returns, and newlines replaced by their percent-encoded
        equivalents.

    """
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    """Escape a property value according to the GitHub Actions protocol.

    Parameters
    ----------
    value : str
        The property value to escape according to the GitHub Actions protocol.

    Returns
    -------
    str
        The escaped property value as a string.

    """
    return _escape_message(value).replace(":", "%3A").replace(",", "%2C")
