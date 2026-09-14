# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Contract tests for Verify format-warning propagation in Check."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

from docmethis_extract_python.static_extraction.models import FunctionRecord, MethodType, Visibility

from docmethis_check.config import CheckConfig
from docmethis_check.diagnostics import diagnostics_for_function
from docmethis_check.formatters.json import format as format_json
from docmethis_check.models import CheckResult

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_format_warning_fields_reach_check_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Check keeps the discriminator, expected value, and warning-local span."""
    source_file = tmp_path / "module.py"
    source_file.write_text('def foo():\n    """Summary."""\n    pass\n', encoding="utf-8")
    function = FunctionRecord(
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
    monkeypatch.setattr(
        "docmethis_check.diagnostics.verify_docstring",
        lambda _request: SimpleNamespace(
            errors=(),
            warnings=(
                SimpleNamespace(
                    section="Parameters",
                    kind="param_colon_spacing",
                    detail="incorrect spacing",
                    expected=" : ",
                    discriminator="value",
                    span=SimpleNamespace(start=2, end=9),
                ),
            ),
        ),
    )

    entries, _keys = diagnostics_for_function(function, CheckConfig(severity={"DMT-6212": "warning"}))
    assert len(entries) == 1
    entry = entries[0]
    assert entry.expected == " : "
    assert entry.discriminator == "value"
    assert entry.docstring_span == (2, 9)
    report_path = tmp_path / "check.json"
    text = format_json(CheckResult(checks=entries), file=str(report_path))
    report = json.loads(text)
    assert report["checks"][0]["docstring_span"] == {"start": 2, "end": 9}
    assert report["checks"][0]["discriminator"] == "value"
