# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Phase 3 tests for the GitHub Docker publication contract."""

from __future__ import annotations

from pathlib import Path


def test_dockerfile_declares_the_public_source_and_runtime_contract() -> None:
    """The release workflow builds the existing CLI image, not a second image."""
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim@sha256:" in dockerfile
    assert "apt-get install -y --no-install-recommends git" in dockerfile
    assert 'LABEL org.opencontainers.image.source="https://github.com/DocMeThis/docmethis-check"' in dockerfile
    assert 'ENTRYPOINT ["/entrypoint.sh"]' in dockerfile


def test_github_workflow_publishes_only_the_release_tag_and_digest() -> None:
    """GitHub is the sole image builder and emits the digest for GitLab pinning."""
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "publish-docker.yml").read_text(encoding="utf-8")

    assert 'tags:\n      - "v*.*.*"' in workflow
    assert "packages: write" in workflow
    assert "docker/build-push-action@" in workflow
    assert "context: ." in workflow
    assert "file: ./Dockerfile" in workflow
    assert "push: true" in workflow
    assert "tags: ${{ env.IMAGE_NAME }}:${{ steps.version.outputs.version }}" in workflow
    assert "IMAGE_DIGEST: ${{ steps.image.outputs.digest }}" in workflow
    assert "org.opencontainers.image.source=https://github.com/DocMeThis/docmethis-check" in workflow
    assert ":latest" not in workflow


def test_github_workflow_checks_version_and_smoke_tests_the_published_digest() -> None:
    """A release tag must match the package and the pushed image must run."""
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "publish-docker.yml").read_text(encoding="utf-8")

    assert "tomllib" in workflow
    assert "RELEASE_VERSION" in workflow
    assert 'project_version" != "$RELEASE_VERSION"' in workflow
    assert "IMAGE_REFERENCE: ${{ env.IMAGE_NAME }}@${{ steps.image.outputs.digest }}" in workflow
    assert 'docker run --rm --entrypoint /bin/sh "$IMAGE_REFERENCE"' in workflow
