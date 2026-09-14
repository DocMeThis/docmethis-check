# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Test configuration for the check module, including M1 and Docker speedups."""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from docmethis_extract_python.api import ProjectRecord

_PROJECT_CACHE: dict[tuple[Path, str], ProjectRecord] = {}
_CACHE_INSTALLED = False


@pytest.fixture(autouse=True)
def _isolate_ci_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real CI environment from affecting temporary Git repositories."""
    for variable in (
        "GITHUB_EVENT_NAME",
        "GITHUB_BASE_REF",
        "GITHUB_SHA",
        "GITHUB_BEFORE_SHA",
        "GITHUB_EVENT_PATH",
        "CI_COMMIT_BEFORE_SHA",
        "CI_COMMIT_SHA",
    ):
        monkeypatch.delenv(variable, raising=False)


def pytest_configure(config: object) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "docker: test requiring Docker (skipped locally without RUN_DOCKER_TESTS=1)")


def pytest_collection_modifyitems(config: object, items: list[object]) -> None:  # noqa: ARG001
    """Skip Docker tests and install the M1 cache when check tests are collected."""
    global _CACHE_INSTALLED  # noqa: PLW0603

    if not _CACHE_INSTALLED and any("tests/check/" in str(item.path) for item in items):
        _install_cache()
        _CACHE_INSTALLED = True

    if os.environ.get("RUN_DOCKER_TESTS") == "1":
        return

    for item in items:
        if item.get_closest_marker("docker"):
            item.add_marker(pytest.mark.skip(reason="Docker test disabled. Set RUN_DOCKER_TESTS=1 to run it."))


def _hash_project(root: Path) -> str:
    """Compute a content hash of a Python project for the cache."""
    hash_accu = hashlib.sha256()
    for source_file in sorted(root.rglob("*.py")):
        try:
            hash_accu.update(source_file.read_bytes())
        except (OSError, PermissionError):
            continue
    return hash_accu.hexdigest()


def _install_cache() -> None:
    """Replace analyze_project with a content-hash cache."""
    import docmethis_check.runner as check_runner  # noqa: PLC0415

    _original = check_runner.analyze_project

    def _cached_analyze(root: Path, *args: object, **kwargs: object) -> object:
        mapping_key = (root.resolve(), _hash_project(root))
        if mapping_key in _PROJECT_CACHE:
            return _PROJECT_CACHE[mapping_key]

        outcome = _original(root, *args, **kwargs)
        _PROJECT_CACHE[mapping_key] = outcome
        return outcome

    check_runner.analyze_project = _cached_analyze
