# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Phase 0 tests for GitLab merge request diff resolution."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from docmethis_check.config import CheckConfig
from docmethis_check.formatters.json import format as format_json
from docmethis_check.formatters.text import format as format_text
from docmethis_check.git_diff import DiffRange, NoDiffBaseError, diff_range_for_env
from docmethis_check.models import CheckResult
from docmethis_check.runner import run_check

if TYPE_CHECKING:
    from pathlib import Path


def _git(root: Path, *args: str) -> str:
    """Run Git in a temporary repository and return stdout."""
    outcome = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return outcome.stdout.strip()


def _commit(root: Path, message: str) -> str:
    """Commit the current repository contents and return its SHA."""
    _git(root, "add", ".")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _history(root: Path) -> tuple[str, str, str]:
    """Create a base, source, and synthetic pipeline commit."""
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "C11")
    module = root / "module.py"
    module.write_text("value = 1\n", encoding="utf-8")
    base_sha = _commit(root, "base")
    module.write_text("value = 2\n", encoding="utf-8")
    source_sha = _commit(root, "source")
    module.write_text("value = 3\n", encoding="utf-8")
    synthetic_sha = _commit(root, "synthetic merge result")
    return base_sha, source_sha, synthetic_sha


def _divergent_merge_history(root: Path) -> tuple[str, str, str]:
    """Create divergent target/source commits and a synthetic merge snapshot."""
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "C11")
    module = root / "module.py"
    module.write_text(
        'def changed() -> int:\n    """Returns the base value."""\n    return 1\n',
        encoding="utf-8",
    )
    base_sha = _commit(root, "base")

    _git(root, "checkout", "-b", "source", base_sha)
    module.write_text("def changed() -> int:\n    return 2\n", encoding="utf-8")
    source_sha = _commit(root, "source")

    _git(root, "checkout", "-b", "target", base_sha)
    (root / "target.py").write_text("value = 3\n", encoding="utf-8")
    _commit(root, "target")
    _git(root, "merge", "--no-ff", source_sha, "-m", "synthetic merge result")

    module.write_text(
        'def changed() -> int:\n    """Returns the merged value."""\n    return 2\n',
        encoding="utf-8",
    )
    _git(root, "add", "module.py")
    _git(root, "commit", "--amend", "--no-edit")
    synthetic_sha = _git(root, "rev-parse", "HEAD")
    return base_sha, source_sha, synthetic_sha


def _set_merge_request_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    event_type: str | None,
    base_sha: str | None,
    commit_sha: str | None,
    source_sha: str | None,
    before_sha: str | None = None,
) -> None:
    """Configure the GitLab MR variables used by the resolver."""
    monkeypatch.setenv("CI_PIPELINE_SOURCE", "merge_request_event")
    if event_type is None:
        monkeypatch.delenv("CI_MERGE_REQUEST_EVENT_TYPE", raising=False)
    else:
        monkeypatch.setenv("CI_MERGE_REQUEST_EVENT_TYPE", event_type)
    if base_sha is None:
        monkeypatch.delenv("CI_MERGE_REQUEST_DIFF_BASE_SHA", raising=False)
    else:
        monkeypatch.setenv("CI_MERGE_REQUEST_DIFF_BASE_SHA", base_sha)
    if commit_sha is None:
        monkeypatch.delenv("CI_COMMIT_SHA", raising=False)
    else:
        monkeypatch.setenv("CI_COMMIT_SHA", commit_sha)
    if source_sha is None:
        monkeypatch.delenv("CI_MERGE_REQUEST_SOURCE_BRANCH_SHA", raising=False)
    else:
        monkeypatch.setenv("CI_MERGE_REQUEST_SOURCE_BRANCH_SHA", source_sha)
    if before_sha is None:
        monkeypatch.delenv("CI_COMMIT_BEFORE_SHA", raising=False)
    else:
        monkeypatch.setenv("CI_COMMIT_BEFORE_SHA", before_sha)


def _set_push_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    before_sha: str,
    commit_sha: str,
    branch: str = "main",
) -> None:
    """Configure the GitLab push variables used by the resolver."""
    monkeypatch.setenv("CI_PIPELINE_SOURCE", "push")
    monkeypatch.setenv("CI_COMMIT_BEFORE_SHA", before_sha)
    monkeypatch.setenv("CI_COMMIT_SHA", commit_sha)
    monkeypatch.setenv("CI_COMMIT_BRANCH", branch)


def _nonlinear_history(root: Path) -> tuple[str, str, str]:
    """Create an ancestor, an old branch tip, and a rewritten branch tip."""
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "C11")
    module = root / "module.py"
    module.write_text("value = 1\n", encoding="utf-8")
    ancestor_sha = _commit(root, "ancestor")
    module.write_text("value = 2\n", encoding="utf-8")
    previous_sha = _commit(root, "old branch")
    _git(root, "checkout", "-b", "rewritten", ancestor_sha)
    module.write_text("value = 3\n", encoding="utf-8")
    head_sha = _commit(root, "rewritten branch")
    return ancestor_sha, previous_sha, head_sha


def test_gitlab_linear_push_uses_before_and_commit_sha(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A linear GitLab push uses CI_COMMIT_BEFORE_SHA as its base."""
    base_sha, head_sha, _synthetic_sha = _history(tmp_path)
    _set_push_environment(monkeypatch, before_sha=base_sha, commit_sha=head_sha)

    outcome = diff_range_for_env(git="git", project_root=tmp_path)

    assert outcome.revision_spec == f"{base_sha}..{head_sha}"
    assert outcome.strategy == "gitlab_linear_before_after"
    assert outcome.completeness == "complete"


def test_gitlab_first_push_with_zero_before_sha_is_explicitly_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A first push does not silently become a local working-tree diff."""
    _base_sha, head_sha, _synthetic_sha = _history(tmp_path)
    _set_push_environment(monkeypatch, before_sha="0" * 40, commit_sha=head_sha)

    outcome = diff_range_for_env(git="git", project_root=tmp_path, on_nonlinear_push_without_base="warn")

    assert outcome.revision_spec is None
    assert outcome.strategy == "gitlab_no_reliable_base"
    assert outcome.completeness == "none"
    assert outcome.reason == "initial_branch_push_without_base"
    assert outcome.head_rev == head_sha


def test_gitlab_nonlinear_push_uses_common_ancestor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A rewritten GitLab push uses the common ancestor of its two tips."""
    ancestor_sha, previous_sha, head_sha = _nonlinear_history(tmp_path)
    _set_push_environment(monkeypatch, before_sha=previous_sha, commit_sha=head_sha, branch="rewritten")

    outcome = diff_range_for_env(git="git", project_root=tmp_path)

    assert outcome.revision_spec == f"{ancestor_sha}..{head_sha}"
    assert outcome.strategy == "gitlab_nonlinear_before_after_merge_base"
    assert outcome.completeness == "complete_relative_to_common_ancestor"


def test_gitlab_nonlinear_push_prefers_configured_base_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A configured base ref selects the GitLab merge-base strategy."""
    ancestor_sha, previous_sha, head_sha = _nonlinear_history(tmp_path)
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", previous_sha)
    _set_push_environment(monkeypatch, before_sha=previous_sha, commit_sha=head_sha, branch="rewritten")

    outcome = diff_range_for_env(git="git", project_root=tmp_path, base_ref="main")

    assert outcome.revision_spec == f"{ancestor_sha}..{head_sha}"
    assert outcome.strategy == "gitlab_nonlinear_merge_base"
    assert outcome.completeness == "complete_relative_to_base"
    assert outcome.reason == "before_sha_not_ancestor_of_sha"


def test_gitlab_push_without_local_base_is_explicitly_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A missing GitLab push base is reported instead of guessed."""
    _base_sha, head_sha, _synthetic_sha = _history(tmp_path)
    _set_push_environment(monkeypatch, before_sha="1" * 40, commit_sha=head_sha)

    outcome = diff_range_for_env(git="git", project_root=tmp_path, on_nonlinear_push_without_base="warn")

    assert outcome.revision_spec is None
    assert outcome.strategy == "gitlab_no_reliable_base"
    assert outcome.completeness == "none"
    assert outcome.reason == "nonlinear_push_without_base"
    assert outcome.head_rev == head_sha


def test_detached_mr_uses_diff_base_and_commit_sha(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A detached MR uses the GitLab diff base and checked-out commit."""
    base_sha, source_sha, synthetic_sha = _history(tmp_path)
    _set_merge_request_environment(
        monkeypatch,
        event_type="detached",
        base_sha=base_sha,
        commit_sha=source_sha,
        source_sha=None,
        before_sha=synthetic_sha,
    )

    outcome = diff_range_for_env(git="git", project_root=tmp_path)

    assert outcome.revision_spec == f"{base_sha}..{source_sha}"
    assert outcome.strategy == "gitlab_merge_request_diff_base"
    assert outcome.completeness == "complete"
    assert outcome.base_rev == base_sha
    assert outcome.head_rev == source_sha


def test_merged_result_uses_source_sha_not_synthetic_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A merged-result MR ignores the synthetic pipeline commit as HEAD."""
    base_sha, source_sha, synthetic_sha = _history(tmp_path)
    _set_merge_request_environment(
        monkeypatch,
        event_type="merged_result",
        base_sha=base_sha,
        commit_sha=synthetic_sha,
        source_sha=source_sha,
    )

    outcome = diff_range_for_env(git="git", project_root=tmp_path)

    assert outcome.revision_spec == f"{base_sha}..{source_sha}"
    assert outcome.strategy == "gitlab_merged_result"
    assert outcome.head_rev == source_sha
    assert synthetic_sha not in outcome.revision_spec


def test_merge_train_uses_source_sha_when_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A merge train uses the source branch SHA when GitLab provides it."""
    base_sha, source_sha, synthetic_sha = _history(tmp_path)
    _set_merge_request_environment(
        monkeypatch,
        event_type="merge_train",
        base_sha=base_sha,
        commit_sha=synthetic_sha,
        source_sha=source_sha,
    )

    outcome = diff_range_for_env(git="git", project_root=tmp_path)

    assert outcome.revision_spec == f"{base_sha}..{source_sha}"
    assert outcome.strategy == "gitlab_merge_train"
    assert outcome.head_rev == source_sha


@pytest.mark.parametrize("event_type", ["merged_result", "merge_train"])
def test_source_sha_is_required_without_synthetic_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    event_type: str,
) -> None:
    """MR pipelines fail instead of silently analyzing a synthetic HEAD."""
    base_sha, _source_sha, synthetic_sha = _history(tmp_path)
    _set_merge_request_environment(
        monkeypatch,
        event_type=event_type,
        base_sha=base_sha,
        commit_sha=synthetic_sha,
        source_sha=None,
    )

    with pytest.raises(NoDiffBaseError, match="missing merge request source branch sha"):
        diff_range_for_env(git="git", project_root=tmp_path)


def test_merge_train_missing_source_fails_even_in_warn_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An incomplete merge train cannot be downgraded to a warning."""
    base_sha, _source_sha, synthetic_sha = _history(tmp_path)
    _set_merge_request_environment(
        monkeypatch,
        event_type="merge_train",
        base_sha=base_sha,
        commit_sha=synthetic_sha,
        source_sha=None,
    )

    with pytest.raises(NoDiffBaseError, match="missing merge request source branch sha"):
        diff_range_for_env(git="git", project_root=tmp_path, on_nonlinear_push_without_base="warn")


def test_missing_mr_base_fails_without_falling_back_to_local_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An MR without a local base cannot fall back to a working-tree diff."""
    _base_sha, source_sha, _synthetic_sha = _history(tmp_path)
    _set_merge_request_environment(
        monkeypatch,
        event_type="detached",
        base_sha="0" * 40,
        commit_sha=source_sha,
        source_sha=None,
    )

    with pytest.raises(NoDiffBaseError, match="missing merge request diff base sha"):
        diff_range_for_env(git="git", project_root=tmp_path, on_nonlinear_push_without_base="warn")


def test_run_check_uses_source_snapshot_after_merged_result_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A merged-result job checks out and analyzes the source branch snapshot."""
    base_sha, source_sha, synthetic_sha = _divergent_merge_history(tmp_path)
    _set_merge_request_environment(
        monkeypatch,
        event_type="merged_result",
        base_sha=base_sha,
        commit_sha=synthetic_sha,
        source_sha=source_sha,
    )

    # This is the checkout performed by the GitLab reference job before invoking Check.
    _git(tmp_path, "checkout", "--detach", source_sha)
    result = run_check(tmp_path, write_cache=False, config=CheckConfig(dia=False))

    assert result.diff_base_rev == base_sha
    assert result.diff_head_rev == source_sha
    assert [(check.code, check.symbol) for check in result.checks] == [("DMT-1120", "module.changed")]


@pytest.mark.parametrize("event_type", [None, "unknown"])
def test_unknown_mr_event_type_is_controlled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, event_type: str | None) -> None:
    """Unknown MR event types do not default to detached semantics."""
    base_sha, source_sha, _synthetic_sha = _history(tmp_path)
    _set_merge_request_environment(
        monkeypatch,
        event_type=event_type,
        base_sha=base_sha,
        commit_sha=source_sha,
        source_sha=source_sha,
    )

    with pytest.raises(NoDiffBaseError, match="merge request event type"):
        diff_range_for_env(git="git", project_root=tmp_path)


def test_runner_and_formatters_expose_diff_revisions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Resolved base/head revisions remain visible in the result contracts."""
    expected = DiffRange(
        revision_spec=None,
        strategy="gitlab_no_reliable_base",
        completeness="none",
        reason="missing_merge_request_source_branch_sha",
        base_rev="base",
        head_rev="synthetic",
    )
    monkeypatch.setattr(
        "docmethis_check.runner.discover_changed_python_files",
        lambda *_args, **_kwargs: ([], expected),
    )

    result = run_check(tmp_path, write_cache=False)
    assert result.diff_base_rev == "base"
    assert result.diff_head_rev == "synthetic"

    report = tmp_path / "report.json"
    data = json.loads(format_json(result, file=str(report)))
    assert data["diff"]["base_rev"] == "base"
    assert data["diff"]["head_rev"] == "synthetic"

    text = format_text(result, file=str(tmp_path / "report.txt"), verbose=True)
    assert "base=base head=synthetic" in text


def test_check_result_formatter_keeps_optional_revisions_optional(tmp_path: Path) -> None:
    """Reports without resolved revisions keep the existing compact diff shape."""
    report = tmp_path / "report.json"
    data = json.loads(format_json(CheckResult(diff_strategy="explicit", diff_completeness="complete"), file=str(report)))

    assert data["diff"] == {"strategy": "explicit", "completeness": "complete"}
