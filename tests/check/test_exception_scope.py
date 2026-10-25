# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Shared exception facts for direct Check, snapshots, and DIA."""

import subprocess
from pathlib import Path

import pytest

from docmethis_check.config import load_check_config
from docmethis_check.diagnostics import diagnostics_for_file
from docmethis_check.runner import run_check


def _configuration(root: Path, documentation_depth: int) -> None:
    (root / "pyproject.toml").write_text(
        "[tool.docmethis]\nexception_analysis_depth = 4\n"
        f"exception_documentation_depth = {documentation_depth}\n"
        '[tool.docmethis.check]\nprofile = "strict"\ncheck-mode = "catchup"\n'
        '[tool.docmethis.check.severity]\nDMT-4002 = "error"\n',
        encoding="utf-8",
    )


@pytest.mark.parametrize("documentation_depth", [0, 1, 4])
def test_cross_module_snapshot_respects_documentation_scope(tmp_path: Path, documentation_depth: int) -> None:
    """The snapshot keeps the complete callee context, even when the documentation excludes it."""
    _configuration(tmp_path, documentation_depth)
    (tmp_path / "backend.py").write_text("def helper() -> None:\n    raise ValueError('invalid')\n", encoding="utf-8")
    source = (
        "from backend import helper\n\n"
        'def api() -> None:\n    """Run the operation.\n\n    Raises\n    ------\n'
        '    ValueError\n        If invalid.\n    """\n    helper()\n'
    )
    path = tmp_path / "main.py"
    path.write_text(source, encoding="utf-8")
    entries, _ = diagnostics_for_file(source, path, tmp_path, load_check_config(tmp_path))
    raises = [entry for entry in entries if entry.symbol.endswith(".api") and entry.code in {"DMT-4001", "DMT-4002"}]
    assert [entry.code for entry in raises] == (["DMT-4002"] if documentation_depth == 0 else [])
    if raises:
        assert "configured documentation depth" in raises[0].message


def test_delegation_refactor_keeps_documentation_consistent_with_dia(tmp_path: Path) -> None:
    """Changing a local raise into a direct delegation does not invalidate Raises."""
    _configuration(tmp_path, 1)
    path = tmp_path / "module.py"
    prefix = (
        'def api() -> None:\n    """Run the operation.\n\n    Raises\n    ------\n    ValueError\n        If invalid.\n    """\n'
    )
    path.write_text(prefix + "    raise ValueError('invalid')\n", encoding="utf-8")
    for args in (
        ("init",),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.org"),
        ("add", "."),
        ("commit", "-m", "base"),
    ):
        subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True, check=True)
    path.write_text(prefix + "    helper()\n\ndef helper() -> None:\n    raise ValueError('invalid')\n", encoding="utf-8")

    result = run_check(project=tmp_path, git_diff="HEAD", write_cache=False, config=load_check_config(tmp_path))

    assert not [
        entry
        for entry in result.checks
        if entry.symbol.endswith(".api") and entry.code in {"DMT-4001", "DMT-4002", "DMT-4201", "DMT-4202"}
    ]
    assert result.impact_analysis is not None
    assert any(impact["symbol"].endswith(".api") for impact in result.impact_analysis.impacts)
