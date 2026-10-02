# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""GitLab Code Quality report formatter."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from docmethis_check.models import AnnotationPlacement, annotation_line

if TYPE_CHECKING:
    from docmethis_check.models import CheckEntry, CheckResult

__all__ = ["format"]

_SEVERITY_MAP = {
    "error": "major",
    "warning": "minor",
}
_FINGERPRINT_FIELDS = (
    ("code", "code"),
    ("relative_path", "relative_path"),
    ("symbol", "symbol"),
    ("section", "section"),
    ("parameter", "parameter_name"),
    ("attribute", "attribute_name"),
    ("accessor", "accessor_kind"),
    ("discriminator", "discriminator"),
)


def format(  # noqa: A001
    result: CheckResult,
    file: str | None = None,
    *,
    project_root: Path | None = None,
    annotation_placement: AnnotationPlacement = AnnotationPlacement.SIGNATURE,
) -> str:
    """Produce a GitLab Code Quality report.

    Parameters
    ----------
    result : CheckResult
        Check result containing the diagnostics to serialize.
    file : str | None = None
        Destination file for the report, or None to write the report to stdout.
    project_root : Path | None = None
        Project root used to validate and relativize finding paths.
    annotation_placement : AnnotationPlacement = AnnotationPlacement.SIGNATURE
        Location policy used to select each finding's annotated line.

    Returns
    -------
    str
        The serialized GitLab Code Quality JSON report.

    Raises
    ------
    ValueError
        If a finding path or severity cannot be represented by the GitLab report.

    """
    records: list[tuple[tuple[str, ...], dict[str, object]]] = []
    for check in result.checks:
        relative_path = _relative_path(check.file, project_root)
        severity = _severity(check)
        line = annotation_line(check, annotation_placement)
        if line < 1:
            msg = f"Diagnostic line must be positive: {line}."
            raise ValueError(msg)
        finding = {
            "description": f"[{check.code}] {check.message}",
            "check_name": f"DocMeThis/{check.code}",
            "fingerprint": _fingerprint(check, relative_path),
            "severity": severity,
            "location": {
                "path": relative_path,
                "lines": {"begin": line, "end": line},
            },
        }
        records.append((_sort_key(check, relative_path, line), finding))

    findings = [finding for _key, finding in sorted(records, key=lambda record: record[0])]
    text = json.dumps(findings, ensure_ascii=False, indent=2)
    if file is not None:
        Path(file).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
        sys.stdout.write("\n")
    return text


def _relative_path(path: str, project_root: Path | None) -> str:
    """Return a validated POSIX path relative to the project root.

    Parameters
    ----------
    path : str
        Finding path to validate and normalize.
    project_root : Path | None
        Project root used to resolve absolute and relative finding paths.

    Returns
    -------
    str
        Validated POSIX path relative to the project root.

    Raises
    ------
    ValueError
        If the path is absolute without a root, empty, or outside the root.

    """
    candidate = Path(path)
    if project_root is None:
        if candidate.is_absolute():
            msg = "project_root is required for absolute finding paths."
            raise ValueError(msg)
        if not path or ".." in candidate.parts:
            msg = f"Finding path is outside project root: {path}"
            raise ValueError(msg)
        relative = candidate
    else:
        root = project_root.resolve()
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            msg = f"Finding path is outside project root: {path}"
            raise ValueError(msg) from exc

    normalized = relative.as_posix()
    if not normalized or normalized == ".":
        msg = f"Finding path is empty: {path}"
        raise ValueError(msg)
    return normalized


def _severity(check: CheckEntry) -> str:
    """Map a Check severity to a GitLab Code Quality severity.

    Parameters
    ----------
    check : CheckEntry
        Diagnostic whose Check severity is converted.

    Returns
    -------
    str
        GitLab Code Quality severity.

    Raises
    ------
    ValueError
        If the Check severity has no GitLab mapping.

    """
    try:
        return _SEVERITY_MAP[check.severity]
    except KeyError as exc:
        msg = f"Unsupported Check severity for GitLab Code Quality: {check.severity}"
        raise ValueError(msg) from exc


def _fingerprint(check: CheckEntry, relative_path: str) -> str:
    """Return a stable identity hash independent of rendered location and text.

    Parameters
    ----------
    check : CheckEntry
        Diagnostic whose canonical identity fields are hashed.
    relative_path : str
        Normalized project-relative path used as part of the identity.

    Returns
    -------
    str
        Hexadecimal SHA-256 fingerprint for the diagnostic identity.

    """
    parts = []
    for label, attribute in _FINGERPRINT_FIELDS:
        value = relative_path if attribute == "relative_path" else getattr(check, attribute)
        if value is not None:
            parts.append(f"{label}={value}")
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _sort_key(check: CheckEntry, relative_path: str, line: int) -> tuple[str, int, str, str, str, str, str, str, str, str]:
    """Return the deterministic ordering key for one finding.

    Parameters
    ----------
    check : CheckEntry
        Diagnostic whose stable fields determine ordering.
    relative_path : str
        Normalized project-relative finding path.
    line : int
        Annotated source line for the finding.

    Returns
    -------
    tuple[str, int, str, str, str, str, str, str, str, str]
        Tuple used to sort findings deterministically.

    """
    return (
        relative_path,
        line,
        check.code,
        check.symbol,
        check.section or "",
        check.parameter_name or "",
        check.attribute_name or "",
        check.accessor_kind or "",
        check.discriminator or "",
        check.message,
    )
