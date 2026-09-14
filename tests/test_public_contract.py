# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Tests for the standalone Check boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import docmethis_check

PACKAGE_ROOT = Path(docmethis_check.__file__).resolve().parent


def test_root_has_no_default_exports() -> None:
    """Check does not expose a user API from its package root in R2."""
    assert not getattr(docmethis_check, "__all__", ())


def test_source_has_no_historical_private_imports() -> None:
    """The candidate source depends only on the two target distributions."""
    forbidden_prefixes = ("docmethis.", "docmethis_gateway", "gateway", "scripts.")

    for source_path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported_names = [node.module or ""]
            else:
                continue

            assert not any(name.startswith(forbidden_prefixes) for name in imported_names), source_path
            assert not any(name.startswith("docmethis_extract_python.static_extraction") for name in imported_names), source_path
