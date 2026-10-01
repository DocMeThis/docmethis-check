# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Phase 4 tests for the GitLab CI reference job contract."""

from __future__ import annotations

from pathlib import Path


def _workflow() -> str:
    """Read the reference GitLab CI configuration."""
    root = Path(__file__).resolve().parents[2]
    return (root / ".gitlab-ci.yml").read_text(encoding="utf-8")


def test_workflow_avoids_duplicate_branch_and_merge_request_pipelines() -> None:
    """An open MR takes the MR pipeline, not a concurrent branch pipeline."""
    workflow = _workflow()

    assert "if: '$CI_PIPELINE_SOURCE == \"merge_request_event\"'" in workflow
    assert "if: '$CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS'\n      when: never" in workflow
    assert "if: '$CI_COMMIT_BRANCH'" in workflow
    assert "- when: never" in workflow


def test_job_uses_the_versioned_image_and_keeps_git_history() -> None:
    """The job runs the published image and leaves image replacement to one variable."""
    workflow = _workflow()

    assert 'DOCMETHIS_CHECK_IMAGE: "ghcr.io/docmethis/docmethis-check@sha256:<release-digest>"' in workflow
    assert 'name: "$DOCMETHIS_CHECK_IMAGE"' in workflow
    assert 'entrypoint: [""]' in workflow
    assert 'GIT_DEPTH: "0"' in workflow
    assert ":latest" not in workflow


def test_job_writes_both_reports_even_when_check_fails() -> None:
    """GitLab receives Code Quality and pivot JSON artifacts for every run."""
    workflow = _workflow()

    assert 'git config --global --add safe.directory "$CI_PROJECT_DIR"' in workflow
    assert "python -m docmethis_check" in workflow
    assert "--gitlab-output-file gl-code-quality-report.json" in workflow
    assert "--json-output-file docmethis-report.json" in workflow
    assert "when: always" in workflow
    assert "codequality: gl-code-quality-report.json" in workflow
    assert "- docmethis-report.json" in workflow


def test_job_has_no_gitlab_api_or_installation_dependency() -> None:
    """The reference job uses the image directly without credentials or pip installs."""
    workflow = _workflow()

    assert "CI_JOB_TOKEN" not in workflow
    assert "CI_API_V4_URL" not in workflow
    assert "pip install" not in workflow
    assert "BUILD_FIX.py" not in workflow
