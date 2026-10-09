# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Contract tests for the DocMeThis Fix workflow wrapper (F20)."""

from __future__ import annotations

import re
from pathlib import Path


def _workflow() -> str:
    """Read the Fix workflow contract."""
    root = Path(__file__).resolve().parents[2]
    return (root / ".github" / "workflows" / "docmethis-fix.yml").read_text(encoding="utf-8")


def test_workflow_delegates_every_privileged_step_to_the_official_callee() -> None:
    """The consumer wrapper contains no executable step, only the pinned call."""
    workflow = _workflow()

    assert "pull_request:" in workflow
    assert "types: [opened, reopened, synchronize]" in workflow
    assert "workflow_dispatch:" not in workflow
    assert "push:" not in workflow
    assert "uses: DocMeThis/docmethis-fix/.github/workflows/docmethis-fix.yml@" in workflow
    assert re.search(r"docmethis-fix\.yml@([0-9a-f]{40})", workflow) is not None
    assert "runs-on:" not in workflow
    assert "steps:" not in workflow
    assert "run:" not in workflow
    assert "persist-credentials" not in workflow
    assert "git remote set-url" not in workflow
    assert "DOCMETHIS_FIX_VERSION" not in workflow
    assert "https://pkg.docmethis.com" not in workflow


def test_workflow_grants_only_read_and_oidc_permissions() -> None:
    """Push access is brokered at runtime; the caller grants read + id-token only."""
    workflow = _workflow()

    assert "contents: read" in workflow
    assert "id-token: write" in workflow
    assert "contents: write" not in workflow
    assert "pull-requests: write" not in workflow
    assert "actions: write" not in workflow
    assert "cancel-in-progress" not in workflow


def test_workflow_maps_namespaced_secrets_to_the_generic_llm_facade() -> None:
    """Named secrets never share a namespace with PR-controlled configuration."""
    workflow = _workflow()

    assert "secrets: inherit" not in workflow
    assert "DOCMETHIS_FIX_API_KEY: ${{ secrets.DOCMETHIS_FIX_API_KEY }}" in workflow
    assert "LLM_API_KEY: ${{ secrets.OPENCODE_API_KEY }}" in workflow
    assert "OPENROUTER_API_KEY" not in workflow
    assert "DOCMETHIS_GITHUB_TOKEN" not in workflow
    assert "vars." not in workflow
