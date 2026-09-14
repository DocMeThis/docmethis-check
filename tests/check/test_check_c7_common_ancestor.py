# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""C7 regressions for nonlinear pushes."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from docmethis_check import cli, runner
from docmethis_check.git_diff import NoDiffBaseError, diff_range_for_env
from docmethis_check.models import CheckResult

if TYPE_CHECKING:
    from pathlib import Path


def _git(root: Path, *args: str) -> str:
    """Run Git in the temporary repository and return stdout."""
    outcome = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return outcome.stdout.strip()


def _initialize_repository(root: Path) -> None:
    """Create a minimal Git repository for the diff scenarios."""
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")


def _commit(root: Path, message: str) -> str:
    """Commit the current contents and return the created SHA."""
    _git(root, "add", ".")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove CI variables that could affect the tests."""
    for variable in (
        "GITHUB_BASE_REF",
        "GITHUB_BEFORE_SHA",
        "GITHUB_EVENT_NAME",
        "GITHUB_REF_NAME",
        "GITHUB_SHA",
        "GITHUB_WORKSPACE",
        "CI_COMMIT_BEFORE_SHA",
        "CI_COMMIT_BRANCH",
        "CI_COMMIT_SHA",
    ):
        monkeypatch.delenv(variable, raising=False)


def test_nonlinear_push_uses_common_ancestor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Analyze a rewritten history from its latest common ancestor."""
    _initialize_repository(tmp_path)
    module = tmp_path / "module.py"
    module.write_text("x = 1\n", encoding="utf-8")
    ancestor = _commit(tmp_path, "ancestor")

    module.write_text("x = 2\n", encoding="utf-8")
    previous_sha = _commit(tmp_path, "old history")

    _git(tmp_path, "checkout", "-b", "rewritten", ancestor)
    module.write_text("x = 3\n", encoding="utf-8")
    new_sha = _commit(tmp_path, "new history")

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_BEFORE_SHA", previous_sha)
    monkeypatch.setenv("GITHUB_SHA", new_sha)
    monkeypatch.setenv("GITHUB_REF_NAME", "rewritten")

    outcome = diff_range_for_env(git="git", project_root=tmp_path)

    assert outcome.revision_spec == f"{ancestor}..{new_sha}"
    assert outcome.strategy == "github_nonlinear_before_after_merge_base"
    assert outcome.completeness == "complete_relative_to_common_ancestor"
    assert outcome.reason == "before_sha_not_ancestor_of_sha"


def test_disjoint_base_ref_does_not_prevent_common_ancestor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A disjoint configured base still allows the common ancestor before and after."""
    _initialize_repository(tmp_path)
    module = tmp_path / "module.py"
    module.write_text("x = 1\n", encoding="utf-8")
    ancestor = _commit(tmp_path, "ancestor")

    module.write_text("x = 2\n", encoding="utf-8")
    previous_sha = _commit(tmp_path, "old history")

    _git(tmp_path, "checkout", "-b", "rewritten", ancestor)
    module.write_text("x = 3\n", encoding="utf-8")
    new_sha = _commit(tmp_path, "new history")

    _git(tmp_path, "checkout", "--orphan", "base-disjoint")
    _git(tmp_path, "rm", "-f", "module.py")
    module.write_text("x = 4\n", encoding="utf-8")
    disjoint_base = _commit(tmp_path, "disjoint base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", disjoint_base)

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_BEFORE_SHA", previous_sha)
    monkeypatch.setenv("GITHUB_SHA", new_sha)
    monkeypatch.setenv("GITHUB_REF_NAME", "rewritten")

    outcome = diff_range_for_env(git="git", project_root=tmp_path, base_ref="main")

    assert outcome.revision_spec == f"{ancestor}..{new_sha}"
    assert outcome.strategy == "github_nonlinear_before_after_merge_base"
    assert outcome.completeness == "complete_relative_to_common_ancestor"


def test_nonlinear_push_prefers_merge_base_with_base_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A configured base takes priority over the ancestor before and after."""
    _initialize_repository(tmp_path)
    module = tmp_path / "module.py"
    module.write_text("x = 1\n", encoding="utf-8")
    ancestor = _commit(tmp_path, "ancestor")

    module.write_text("x = 2\n", encoding="utf-8")
    previous_sha = _commit(tmp_path, "old history")

    _git(tmp_path, "checkout", "-b", "rewritten", ancestor)
    module.write_text("x = 3\n", encoding="utf-8")
    new_sha = _commit(tmp_path, "new history")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", previous_sha)

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_BEFORE_SHA", previous_sha)
    monkeypatch.setenv("GITHUB_SHA", new_sha)
    monkeypatch.setenv("GITHUB_REF_NAME", "rewritten")

    outcome = diff_range_for_env(git="git", project_root=tmp_path, base_ref="main")

    assert outcome.revision_spec == f"{ancestor}..{new_sha}"
    assert outcome.strategy == "github_nonlinear_merge_base"
    assert outcome.completeness == "complete_relative_to_base"
    assert outcome.reason == "before_sha_not_ancestor_of_sha"


def test_nonlinear_push_head_commit_uses_last_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The head_commit fallback uses the parent of HEAD when one exists."""
    _initialize_repository(tmp_path)
    module = tmp_path / "module.py"
    module.write_text("x = 1\n", encoding="utf-8")
    _commit(tmp_path, "parent")
    module.write_text("x = 2\n", encoding="utf-8")
    head_sha = _commit(tmp_path, "head")

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_BEFORE_SHA", "0" * 40)
    monkeypatch.setenv("GITHUB_SHA", head_sha)
    monkeypatch.setenv("GITHUB_REF_NAME", "new-branche")

    outcome = diff_range_for_env(
        git="git",
        project_root=tmp_path,
        on_nonlinear_push_without_base="head_commit",
    )

    assert outcome.revision_spec == f"{head_sha}^..{head_sha}"
    assert outcome.strategy == "github_head_commit_only"
    assert outcome.completeness == "partial"
    assert outcome.reason == "initial_branch_push_without_base"


def test_nonlinear_push_warns_when_before_sha_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The warn fallback handles a force-push whose old SHA is not local."""
    _initialize_repository(tmp_path)
    module = tmp_path / "module.py"
    module.write_text("x = 1\n", encoding="utf-8")
    head_sha = _commit(tmp_path, "head")

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_BEFORE_SHA", "1" * 40)
    monkeypatch.setenv("GITHUB_SHA", head_sha)
    monkeypatch.setenv("GITHUB_REF_NAME", "main")

    outcome = diff_range_for_env(
        git="git",
        project_root=tmp_path,
        on_nonlinear_push_without_base="warn",
    )

    assert outcome.revision_spec is None
    assert outcome.strategy == "github_no_reliable_base"
    assert outcome.completeness == "none"
    assert outcome.reason == "nonlinear_push_without_base"
    assert outcome.head_rev == head_sha


def test_nonlinear_push_head_commit_rejects_root_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The head_commit fallback rejects a root commit without a parent."""
    _initialize_repository(tmp_path)
    module = tmp_path / "module.py"
    module.write_text("x = 1\n", encoding="utf-8")
    head_sha = _commit(tmp_path, "root")

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_BEFORE_SHA", "0" * 40)
    monkeypatch.setenv("GITHUB_SHA", head_sha)
    monkeypatch.setenv("GITHUB_REF_NAME", "new-branche")

    with pytest.raises(NoDiffBaseError, match="first repository commit"):
        diff_range_for_env(
            git="git",
            project_root=tmp_path,
            on_nonlinear_push_without_base="head_commit",
        )


def test_missing_ancestor_warn_produces_annotations_and_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The warn mode returns explicit success without running M1 analysis."""
    _initialize_repository(tmp_path)
    module = tmp_path / "module.py"
    module.write_text("x = 1\n", encoding="utf-8")
    previous_sha = _commit(tmp_path, "old root")

    _git(tmp_path, "checkout", "--orphan", "new-root")
    _git(tmp_path, "rm", "-f", "module.py")
    module.write_text("x = 2\n", encoding="utf-8")
    new_sha = _commit(tmp_path, "new root")

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_BEFORE_SHA", previous_sha)
    monkeypatch.setenv("GITHUB_SHA", new_sha)
    monkeypatch.setenv("GITHUB_REF_NAME", "new-root")
    monkeypatch.setattr(
        runner,
        "analyze_project",
        lambda *_args, **_kwargs: pytest.fail("M1 must not run without a reliable base"),
    )

    report = tmp_path / "report.json"
    annotations = tmp_path / "annotations.txt"
    code = cli.main(
        [
            str(tmp_path),
            "--github-output-file",
            str(annotations),
            "--json-output-file",
            str(report),
            "--fail-on-warning",
            "--on-nonlinear-push-without-base",
            "warn",
        ]
    )

    output = annotations.read_text(encoding="utf-8")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0
    assert "::warning title=DocMeThis inconclusive diff::" in output
    assert data["diff"] == {
        "strategy": "github_no_reliable_base",
        "completeness": "none",
        "reason": "nonlinear_push_without_base",
    }
    assert data["checks"] == []


def test_inaccessible_json_report_returns_cli_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A write error is an infrastructure error, not a diagnostic."""
    monkeypatch.setattr(cli, "run_check", lambda **_kwargs: CheckResult())
    report = tmp_path / "inaccessible-directory" / "report.json"

    code = cli.main([str(tmp_path), "--json-output-file", str(report)])

    assert code == 2
    assert "docmethis_check:" in capsys.readouterr().err
