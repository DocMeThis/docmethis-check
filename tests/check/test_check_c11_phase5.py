# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Phase 5 tests for the published GitLab runtime image."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest


@pytest.mark.docker
def test_published_image_exposes_check_and_git() -> None:
    """The published image contains the direct GitLab CLI runtime contract."""
    image = os.environ.get("DOCMETHIS_CHECK_IMAGE")
    if not image:
        pytest.skip("Set DOCMETHIS_CHECK_IMAGE to run the published-image smoke test.")

    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker is not installed.")

    info = subprocess.run([docker, "info"], capture_output=True, text=True, check=False)
    if info.returncode != 0:
        pytest.skip("The Docker daemon is not available.")

    outcome = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            image,
            "-c",
            "python -m docmethis_check --help >/dev/null && git --version",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
