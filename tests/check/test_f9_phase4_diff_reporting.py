# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Check F9 phase 4 tests for publishing diff files."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from docmethis_check.formatters.json import format as format_json
from docmethis_check.models import CheckResult

if TYPE_CHECKING:
    from pathlib import Path


def test_json_publishes_files_from_diff(tmp_path: Path) -> None:
    """The Fix reader receives diff ownership without rerunning Git analysis."""
    outcome = CheckResult(
        diff_strategy="local_working_tree",
        diff_completeness="partial",
        diff_files=[{"path": "/tmp/module.py", "changed_lines": [1], "deleted_lines": []}],
    )
    output = tmp_path / "check.json"

    format_json(outcome, str(output))

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["diff"]["files"] == [{"path": "/tmp/module.py", "changed_lines": [1], "deleted_lines": []}]
