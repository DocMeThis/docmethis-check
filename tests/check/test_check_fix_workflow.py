# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Contract tests for the DocMeThis Fix workflow."""

from __future__ import annotations

from pathlib import Path


def _workflow() -> str:
    """Read the Fix workflow contract."""
    root = Path(__file__).resolve().parents[2]
    return (root / ".github" / "workflows" / "docmethis-fix.yml").read_text(encoding="utf-8")


def test_workflow_centralizes_the_private_runtime_version() -> None:
    """The install command uses the version declared at the YAML root."""
    workflow = _workflow()

    assert 'DOCMETHIS_FIX_VERSION: "0.1.0"' in workflow
    assert '"docmethis-fix==${DOCMETHIS_FIX_VERSION}"' in workflow
    assert "Reserved for the exact private runtime contract" not in workflow


def test_workflow_hands_check_findings_to_fix_on_the_existing_pr() -> None:
    """Check findings are handed to Fix on the source branch of the PR."""
    workflow = _workflow()

    assert "pull_request:" in workflow
    assert "types: [opened, reopened, synchronize]" in workflow
    assert "workflow_dispatch:" not in workflow
    assert "push:" not in workflow
    assert "git remote set-url" not in workflow
    assert "head.repo.full_name == github.repository" in workflow
    assert "github.actor != 'dependabot[bot]'" in workflow
    assert '"$FIX_PYTHON" -m docmethis_check .' in workflow
    assert "GITHUB_BEFORE_SHA: ${{ github.event.before }}" in workflow
    assert "--git-diff" not in workflow
    assert "--check-mode regression" in workflow
    assert "--base-ref main" not in workflow
    assert "--no-cache" in workflow
    assert "--json-output-file rapport.json" in workflow
    assert 'if [ "$status" -gt 1 ]; then' in workflow
    assert '"$FIX_PYTHON" -m docmethis_fix rapport.json' in workflow
    assert "--push" in workflow
    assert "\n            --pr " not in workflow


def test_workflow_transmits_the_base_repository_account_type() -> None:
    """F18: the base repository owner type is passed to Fix, never a user-set value."""
    workflow = _workflow()

    assert "DOCMETHIS_FIX_ACCOUNT_TYPE: ${{ github.event.pull_request.base.repo.owner.type }}" in workflow
    assert "vars.DOCMETHIS_FIX_ACCOUNT_TYPE" not in workflow
    assert "secrets.DOCMETHIS_FIX_ACCOUNT_TYPE" not in workflow


def test_workflow_keeps_credentials_and_push_permissions_explicit() -> None:
    """Gateway and provider credentials are runtime inputs with source push access."""
    workflow = _workflow()

    assert "contents: write" in workflow
    assert "pull-requests: write" not in workflow
    assert "DOCMETHIS_FIX_API_KEY" in workflow
    assert "DOCMETHIS_FIX_API_KEY_FILE=$key_file" in workflow
    assert "umask 077" in workflow
    assert "token: ${{ github.token }}" in workflow
    assert "persist-credentials: true" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}" in workflow
    assert "OPENCODE_API_KEY: ${{ secrets.OPENCODE_API_KEY }}" in workflow
    assert "DOCMETHIS_GITHUB_TOKEN" not in workflow
    assert "DocMeThis-Fix: true" not in workflow
    assert "gh workflow run" not in workflow
    assert "fix.patch" in workflow
    assert ".docmethis/inference-diagnostics.jsonl" in workflow
