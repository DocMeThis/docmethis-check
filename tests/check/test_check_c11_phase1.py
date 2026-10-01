# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Phase 1 tests for the GitLab Code Quality formatter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from docmethis_check.formatters.gitlab import format as format_gitlab
from docmethis_check.models import AnnotationPlacement, CheckEntry, CheckResult

if TYPE_CHECKING:
    from pathlib import Path


def _entry(
    tmp_path: Path,
    *,
    code: str = "DMT-2001",
    severity: str = "error",
    file: str | None = None,
    line_start: int = 12,
    message: str = "Parameter is not documented.",
    **kwargs: object,
) -> CheckEntry:
    """Build a representative diagnostic entry."""
    return CheckEntry(
        severity=severity,  # type: ignore[arg-type]
        code=code,
        symbol="module.calculate",
        file=file or str(tmp_path / "src" / "module.py"),
        line_start=line_start,
        col_start=1,
        message=message,
        **kwargs,
    )


def _report(tmp_path: Path, result: CheckResult) -> list[dict[str, object]]:
    """Render and decode a report without writing to stdout."""
    output = tmp_path / "gl-code-quality.json"
    format_gitlab(result, file=str(output), project_root=tmp_path)
    return json.loads(output.read_text(encoding="utf-8"))


def test_formats_required_fields_and_severities(tmp_path: Path) -> None:
    """Errors and warnings map to the required Code Quality finding shape."""
    result = CheckResult(
        checks=[
            _entry(tmp_path, code="DMT-2001", severity="error"),
            _entry(tmp_path, code="DMT-3001", severity="warning", message="Return is not documented."),
        ],
    )

    findings = _report(tmp_path, result)

    assert findings[0] == {
        "description": "[DMT-2001] Parameter is not documented.",
        "check_name": "DocMeThis/DMT-2001",
        "fingerprint": findings[0]["fingerprint"],
        "severity": "major",
        "location": {
            "path": "src/module.py",
            "lines": {"begin": 12, "end": 12},
        },
    }
    assert findings[1]["severity"] == "minor"
    assert findings[1]["check_name"] == "DocMeThis/DMT-3001"


def test_empty_result_and_analysis_errors_produce_no_findings(tmp_path: Path) -> None:
    """Analysis errors remain outside the Code Quality finding list."""
    result = CheckResult()
    result.analysis_errors = [{"file": "broken.py", "type": "parse_error", "message": "Unable to parse."}]

    assert _report(tmp_path, result) == []


def test_path_outside_project_root_is_rejected(tmp_path: Path) -> None:
    """Absolute findings outside the project cannot be published."""
    result = CheckResult(checks=[_entry(tmp_path, file=str(tmp_path.parent / "outside.py"))])

    with pytest.raises(ValueError, match="outside project root"):
        format_gitlab(result, project_root=tmp_path)


def test_absolute_path_requires_project_root(tmp_path: Path) -> None:
    """An absolute finding path is unsafe without a root for relativization."""
    result = CheckResult(checks=[_entry(tmp_path)])

    with pytest.raises(ValueError, match="project_root is required"):
        format_gitlab(result)


def test_relative_path_is_accepted_without_project_root(tmp_path: Path) -> None:
    """Already-relative paths can be rendered without an explicit root."""
    result = CheckResult(checks=[_entry(tmp_path, file="src/module.py")])

    output = tmp_path / "report.json"
    format_gitlab(result, file=str(output))

    assert json.loads(output.read_text(encoding="utf-8"))[0]["location"]["path"] == "src/module.py"


@pytest.mark.parametrize(
    ("placement", "expected_line"),
    [
        (AnnotationPlacement.SIGNATURE, 12),
        (AnnotationPlacement.DOCSTRING, 20),
        (AnnotationPlacement.PRECISE, 30),
    ],
)
def test_annotation_placement_controls_location_line(
    tmp_path: Path,
    placement: AnnotationPlacement,
    expected_line: int,
) -> None:
    """Code Quality locations follow the existing annotation policy."""
    result = CheckResult(
        checks=[
            _entry(
                tmp_path,
                docstring_line=20,
                annotation_line=30,
            ),
        ],
    )

    output = tmp_path / "report.json"
    format_gitlab(result, file=str(output), project_root=tmp_path, annotation_placement=placement)
    location = json.loads(output.read_text(encoding="utf-8"))[0]["location"]["lines"]

    assert location == {"begin": expected_line, "end": expected_line}


def test_fingerprint_ignores_location_message_severity_and_docstring_hash(tmp_path: Path) -> None:
    """A diagnostic identity survives presentation-only changes."""
    original = _entry(tmp_path, line_start=12, message="Original message.", docstring_sha256="a" * 64)
    moved = _entry(tmp_path, line_start=90, severity="warning", message="Rewritten message.", docstring_sha256="b" * 64)

    first = _report(tmp_path, CheckResult(checks=[original]))[0]["fingerprint"]
    second = _report(tmp_path, CheckResult(checks=[moved]))[0]["fingerprint"]

    assert first == second


def test_fingerprint_changes_when_canonical_identity_changes(tmp_path: Path) -> None:
    """Diagnostic identity fields keep distinct findings distinct."""
    first = _entry(tmp_path, parameter_name="limit", section="Parameters")
    second = _entry(tmp_path, parameter_name="offset", section="Parameters")

    findings = _report(tmp_path, CheckResult(checks=[first, second]))

    assert findings[0]["fingerprint"] != findings[1]["fingerprint"]


def test_output_order_is_deterministic_and_independent_of_input_order(tmp_path: Path) -> None:
    """The same findings render identically regardless of input order."""
    first = _entry(tmp_path, code="DMT-3001", file=str(tmp_path / "z.py"), line_start=40)
    second = _entry(tmp_path, code="DMT-2001", file=str(tmp_path / "a.py"), line_start=2)

    forward = format_gitlab(
        CheckResult(checks=[first, second]),
        file=str(tmp_path / "forward.json"),
        project_root=tmp_path,
    )
    reverse = format_gitlab(
        CheckResult(checks=[second, first]),
        file=str(tmp_path / "reverse.json"),
        project_root=tmp_path,
    )

    assert forward == reverse
    assert json.loads(forward)[0]["location"]["path"] == "a.py"


def test_unsupported_severity_is_rejected(tmp_path: Path) -> None:
    """Disabled or unknown severities are not silently mapped."""
    result = CheckResult(checks=[_entry(tmp_path, severity="disabled")])

    with pytest.raises(ValueError, match="Unsupported Check severity"):
        format_gitlab(result, project_root=tmp_path)
