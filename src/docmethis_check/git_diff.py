# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Discover Python files changed by Git."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from os import environ
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "ChangedFile",
    "DiffRange",
    "GitDiffError",
    "NoDiffBaseError",
    "diff_range_for_env",
    "discover_changed_python_files",
    "git_executable",
]

_SHA_LENGTH = 40


def _github_before_sha() -> str | None:
    """Read the previous push SHA from GitHub's event payload.

    Returns
    -------
    str | None
        The previous push SHA from GitHub's event payload, or None if it cannot be determined.

    """
    before_sha = environ.get("GITHUB_BEFORE_SHA")
    if before_sha:
        return before_sha

    event_path = environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None

    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("Could not read GitHub event payload from %s", event_path, exc_info=True)
        return None

    if not isinstance(event, dict):
        return None
    before_sha = event.get("before")
    return before_sha if isinstance(before_sha, str) else None


class GitDiffError(RuntimeError):
    """Controlled error while discovering the Git diff."""


class NoDiffBaseError(GitDiffError):
    """A reliable diff base could not be determined."""


@dataclass(frozen=True, slots=True)
class ChangedFile:
    """Python file with HEAD lines affected by a Git range.

    Attributes
    ----------
    path : Path
        Absolute path of the changed Python file.
    changed_lines : frozenset[int]
        Line numbers introduced or modified in HEAD.
    deleted_lines : frozenset[int]
        Line numbers deleted relative to HEAD.

    """

    path: Path
    changed_lines: frozenset[int]
    deleted_lines: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class DiffRange:
    """Resolved Git range and associated metadata.

    ``revision_spec == ""`` is reserved for ``local_working_tree`` mode and
    means the current working tree diff (``git diff HEAD``). ``None`` means no
    reliable base was available for the analysis.

    Attributes
    ----------
    revision_spec : str | None
        Git revision range to diff; empty for the working tree, None when no reliable base exists.
    strategy : str
        Strategy identifier used to resolve the range.
    completeness : str
        Completeness indicator of the resolved range.
    reason : str | None
        Reason for the chosen strategy, when relevant.
    base_rev : str | None
        Resolved base revision, when known.
    head_rev : str | None
        Resolved head revision, when known.

    """

    revision_spec: str | None
    strategy: str
    completeness: str
    reason: str | None = None
    base_rev: str | None = None
    head_rev: str | None = None


def _resolve_ci_push(  # noqa: PLR0913
    *,
    git: str,
    project_root: Path,
    current_sha: str,
    before_sha: str | None,
    strategy_prefix: str,
    sha_label: str,
    context_label: str,
    pushed_branch: str | None = None,
    base_ref: str | None = None,
    on_nonlinear_push_without_base: str = "fail",
) -> DiffRange:
    """Resolve a GitHub/GitLab push diff while checking ancestry.

    Parameters
    ----------
    git : str
        Name of the git executable or command to use when invoking Git operations.
    project_root : Path
        Path to the root directory of the project repository, used as the working directory for Git operations.
    current_sha : str
        The SHA of the current (head) commit to resolve the push diff for.
    before_sha : str | None
        The commit SHA that served as the previous head before the push, or None when no before-SHA is available.
    strategy_prefix : str
        Prefix used to build strategy identifiers in the returned DiffRange.
    sha_label : str
        Human-readable label for the commit SHA being resolved, used in error messages to identify the current commit.
    context_label : str
        Human-readable label that identifies the push context being resolved; used in error messages when no reliable diff base
        can be determined.
    pushed_branch : str | None = None
        Optional name of the branch that was pushed. When base_ref is provided, this value is used to avoid treating the
        configured base reference as a merge base if it is the same as the pushed branch.
    base_ref : str | None = None
        Optional name of the base branch or tag against which the push diff should be resolved. When provided and not equal to
        pushed_branch, the function attempts to use the merge base between origin/<base_ref> and current_sha as the diff base.
        Defaults to None.
    on_nonlinear_push_without_base : str = 'fail'
        Controls how to handle a nonlinear push that has no reliable base. If set to 'fail' (the default), raises NoDiffBaseError.
        If set to 'warn', returns a DiffRange with no revision spec and completeness 'none'. If set to 'head_commit', attempts to
        use the current commit's parent as the base, raising NoDiffBaseError when no parent exists.

    Returns
    -------
    DiffRange
        Returns a DiffRange object that describes the resolved diff range for the push. The DiffRange includes the revision spec
        (or None when no reliable base exists), the strategy used, a completeness indicator, and, when applicable, the base and
        head revisions and a reason for the chosen strategy.

    Raises
    ------
    NoDiffBaseError
        Explicitly raised.

    """
    if not _commit_exists(git, project_root, current_sha):
        msg = f"{sha_label} was not found locally."
        raise NoDiffBaseError(msg)

    before_is_zero = bool(before_sha and _is_zero_sha(before_sha))

    before_exists = bool(before_sha and not before_is_zero and _commit_exists(git, project_root, before_sha))

    if before_exists and before_sha is not None and _is_ancestor(git, project_root, before_sha, current_sha):
        return DiffRange(
            revision_spec=f"{before_sha}..{current_sha}",
            strategy=f"{strategy_prefix}linear_before_after",
            completeness="complete",
            base_rev=before_sha,
            head_rev=current_sha,
        )

    base_refspec = f"origin/{base_ref}" if base_ref else None
    if base_refspec and base_ref != pushed_branch and _ref_exists(git, project_root, base_refspec):
        merge = _merge_base_or_none(git, project_root, base_refspec, current_sha)
        if merge is not None:
            return DiffRange(
                revision_spec=f"{merge}..{current_sha}",
                strategy=f"{strategy_prefix}nonlinear_merge_base",
                completeness="complete_relative_to_base",
                reason="initial_branch_push" if before_is_zero else "before_sha_not_ancestor_of_sha",
                base_rev=merge,
                head_rev=current_sha,
            )

    if before_exists and before_sha is not None:
        merge = _merge_base_or_none(git, project_root, before_sha, current_sha)
        if merge is not None:
            return DiffRange(
                revision_spec=f"{merge}..{current_sha}",
                strategy=f"{strategy_prefix}nonlinear_before_after_merge_base",
                completeness="complete_relative_to_common_ancestor",
                reason="before_sha_not_ancestor_of_sha",
                base_rev=merge,
                head_rev=current_sha,
            )

    if on_nonlinear_push_without_base == "warn":
        return DiffRange(
            revision_spec=None,
            strategy=f"{strategy_prefix}no_reliable_base",
            completeness="none",
            reason="initial_branch_push_without_base" if before_is_zero else "nonlinear_push_without_base",
            head_rev=current_sha,
        )

    if on_nonlinear_push_without_base == "head_commit":
        parent = f"{current_sha}^"
        if _commit_exists(git, project_root, parent):
            return DiffRange(
                revision_spec=f"{parent}..{current_sha}",
                strategy=f"{strategy_prefix}head_commit_only",
                completeness="partial",
                reason="initial_branch_push_without_base" if before_is_zero else "nonlinear_push_without_base",
                base_rev=parent,
                head_rev=current_sha,
            )
        cause = "first repository commit (no parent)." if before_is_zero else "HEAD parent not found."
        msg = f"{context_label} has no base: {cause}"
        raise NoDiffBaseError(msg)

    msg = (
        f"{context_label} has no reliable diff base. "
        "Configure `base_ref` or explicitly choose "
        "`on_nonlinear_push_without_base: head_commit` or `warn`."
    )
    raise NoDiffBaseError(msg)


def diff_range_for_env(
    *,
    git: str,
    project_root: Path,
    git_diff: str | None = None,
    base_ref: str | None = None,
    on_nonlinear_push_without_base: str = "fail",
) -> DiffRange:
    """Resolve the Git range to analyze according to the C1 hierarchy.

    Return a ``DiffRange`` with the specification, strategy, and completeness.

    Parameters
    ----------
    git : str
        The git executable command or path used to invoke Git operations.
    project_root : Path
        Path to the root directory of the Git repository. It is used to run Git commands and to verify that the directory is a
        valid Git repository.
    git_diff : str | None = None
        Optional explicit Git revision range to use as the diff specification. When provided and non-empty, it takes precedence
        over environment-based resolution and must describe a valid range; an explicitly provided empty value is rejected.
    base_ref : str | None = None
        Optional explicit base ref to use as the diff base for CI push events, particularly when the push history is nonlinear and
        no reliable base can be inferred from the environment's before-sha. Defaults to None.
    on_nonlinear_push_without_base : str = 'fail'
        Controls the behavior when a detected push is nonlinear and no base reference is available; must be 'fail' (default),
        'head_commit', or 'warn'.

    Returns
    -------
    DiffRange
        Returns a DiffRange describing the resolved Git diff range, including its revision specification, strategy, and
        completeness.

    Raises
    ------
    NoDiffBaseError
        Explicitly raised.
    ValueError
        Explicitly raised.

    """
    if git_diff is not None:
        if not git_diff.strip():
            msg = "--git-diff was explicitly provided but is empty."
            raise NoDiffBaseError(msg)
        spec = git_diff.strip()
        base_rev, head_rev = _revisions_from_explicit_range(spec, git=git, project_root=project_root)
        return DiffRange(revision_spec=spec, strategy="explicit", completeness="complete", base_rev=base_rev, head_rev=head_rev)

    if on_nonlinear_push_without_base not in {"fail", "head_commit", "warn"}:
        msg = "on_nonlinear_push_without_base must be 'fail', 'head_commit', or 'warn'."
        raise ValueError(msg)

    _verify_git_repository(project_root, git=git)

    event_name = environ.get("GITHUB_EVENT_NAME")

    if event_name == "pull_request":
        base = environ.get("GITHUB_BASE_REF")
        if not base:
            msg = "GITHUB_BASE_REF is missing for a pull_request event."
            raise NoDiffBaseError(msg)
        # HEAD is the state checked out by actions/checkout (PR merge commit or branch head).
        # GITHUB_SHA is not used here because it can point to a GitHub-generated
        # merge commit rather than the PR's actual head.
        base_refspec = f"origin/{base}"
        if not _ref_exists(git, project_root, base_refspec):
            msg = f"Reference {base_refspec} not found locally. Check the actions/checkout configuration, especially fetch-depth."
            raise NoDiffBaseError(msg)
        merge = _merge_base(git, project_root, base_refspec, "HEAD")
        return DiffRange(
            revision_spec=f"{merge}..HEAD",
            strategy="pr_merge_base",
            completeness="complete",
            base_rev=merge,
            head_rev="HEAD",
        )

    current_sha = environ.get("GITHUB_SHA") if event_name == "push" else None
    if current_sha:
        return _resolve_ci_push(
            git=git,
            project_root=project_root,
            current_sha=current_sha,
            before_sha=_github_before_sha(),
            strategy_prefix="github_",
            sha_label="GITHUB_SHA",
            context_label="Push GitHub",
            pushed_branch=environ.get("GITHUB_REF_NAME"),
            base_ref=base_ref,
            on_nonlinear_push_without_base=on_nonlinear_push_without_base,
        )

    gitlab_before = environ.get("CI_COMMIT_BEFORE_SHA")
    gitlab_sha = environ.get("CI_COMMIT_SHA")
    if gitlab_before and gitlab_sha:
        return _resolve_ci_push(
            git=git,
            project_root=project_root,
            current_sha=gitlab_sha,
            before_sha=gitlab_before,
            strategy_prefix="gitlab_",
            sha_label="CI_COMMIT_SHA",
            context_label="Pipeline GitLab",
            pushed_branch=environ.get("CI_COMMIT_BRANCH"),
            base_ref=base_ref,
            on_nonlinear_push_without_base=on_nonlinear_push_without_base,
        )

    return DiffRange(
        revision_spec="",
        strategy="local_working_tree",
        completeness="partial",
        reason="no_ci_environment_detected",
        base_rev="HEAD",
    )


def _commit_exists(git: str, root: Path, rev: str) -> bool:
    """Check that a Git revision identifies a local commit.

    Parameters
    ----------
    git : str
        The Git executable command used to invoke Git operations.
    root : Path
        Root directory of the Git repository against which the revision is checked.
    rev : str
        The Git revision to check for existence as a local commit.

    Returns
    -------
    bool
        True if the given revision identifies a local commit; False otherwise.

    Raises
    ------
    NoDiffBaseError
        Explicitly raised.

    """
    result = subprocess.run(  # noqa: S603
        [git, "-C", str(root), "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    # With --quiet, 1 means "revision absent"; any other code is an unexpected Git error (usually 128).
    if result.returncode not in (0, 1):
        msg = f"git rev-parse failed unexpectedly for {rev!r}: {result.stderr.strip()}"
        raise NoDiffBaseError(msg)
    return result.returncode == 0


def _ref_exists(git: str, root: Path, ref: str) -> bool:
    """Check that a reference exists locally.

    Parameters
    ----------
    git : str
        The git command or path to the Git executable used to invoke Git operations.
    root : Path
        Path to the Git repository root directory.
    ref : str
        Reference to verify, such as a commit hash, branch name, or tag.

    Returns
    -------
    bool
        Return True if the specified Git reference exists locally, False otherwise. Raise NoDiffBaseError if git rev-parse fails
        for any reason other than the reference being absent.

    Raises
    ------
    NoDiffBaseError
        Explicitly raised.

    """
    result = subprocess.run(  # noqa: S603
        [git, "-C", str(root), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    # With --quiet, 1 means "reference absent"; any other code is an unexpected Git error (usually 128).
    if result.returncode not in (0, 1):
        msg = f"git rev-parse failed unexpectedly for {ref!r}: {result.stderr.strip()}"
        raise NoDiffBaseError(msg)
    return result.returncode == 0


def _is_ancestor(git: str, root: Path, ancestor: str, descendant: str) -> bool:
    """Check that ``ancestor`` is an ancestor of ``descendant``.

    Parameters
    ----------
    git : str
        The Git command or path to the Git executable used to run the merge-base check.
    root : Path
        Root directory of the Git repository; used as the working directory for the Git command.
    ancestor : str
        The commit or revision to test as the ancestor of `descendant`.
    descendant : str
        The commit or revision to check as the descendant.

    Returns
    -------
    bool
        True if the given ancestor commit is an ancestor of the descendant commit, False otherwise.

    Raises
    ------
    NoDiffBaseError
        Explicitly raised.

    """
    result = subprocess.run(  # noqa: S603
        [git, "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    # --is-ancestor returns 1 for "not an ancestor"; any other code is an unexpected Git error (usually 128).
    if result.returncode not in (0, 1):
        msg = f"git merge-base --is-ancestor failed unexpectedly ({ancestor!r}, {descendant!r}): {result.stderr.strip()}"
        raise NoDiffBaseError(msg)
    return result.returncode == 0


def _merge_base(git: str, root: Path, ref: str, sha: str) -> str:
    """Calculate the merge base between a reference and a SHA.

    Parameters
    ----------
    git : str
        The git command to execute, as a string.
    root : Path
        The root directory of the Git repository, used as the working directory for the Git command.
    ref : str
        The git reference (for example, a branch name or tag) to compare against the SHA when calculating the merge base.
    sha : str
        The commit SHA to compare against the reference.

    Returns
    -------
    str
        The merge base between the reference and the SHA as a string.

    Raises
    ------
    NoDiffBaseError
        Explicitly raised.

    """
    result = subprocess.run(  # noqa: S603
        [git, "-C", str(root), "merge-base", ref, sha],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        msg = f"Could not calculate the merge base between {ref} and {sha}: {result.stderr.strip()}"
        raise NoDiffBaseError(msg)
    return result.stdout.strip()


def _merge_base_or_none(git: str, root: Path, ref: str, sha: str) -> str | None:
    """Calculate a merge base, or return ``None`` for disjoint histories.

    Parameters
    ----------
    git : str
        The git executable or command to invoke for Git operations.
    root : Path
        Root directory of the Git repository.
    ref : str
        Git revision (e.g., branch name or commit hash) to use as the first reference when calculating the merge base against the
        given sha.
    sha : str
        The commit SHA (hash) of the commit to compare against ref when locating the merge base.

    Returns
    -------
    str | None
        The merge base commit SHA as a string, or None if the histories have no common ancestor.

    Raises
    ------
    NoDiffBaseError
        Explicitly raised.

    """
    result = subprocess.run(  # noqa: S603
        [git, "-C", str(root), "merge-base", ref, sha],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        msg = f"Could not calculate the merge base between {ref} and {sha}: {result.stderr.strip()}"
        raise NoDiffBaseError(msg)
    return result.stdout.strip() or None


def discover_changed_python_files(
    project_root: Path,
    git_diff: str | None = None,
    *,
    base_ref: str | None = None,
    on_nonlinear_push_without_base: str = "fail",
) -> tuple[list[ChangedFile], DiffRange]:
    """Return Python files and new lines introduced by the Git diff.

    Parameters
    ----------
    project_root : Path
        Root directory of the project used to resolve the Git diff and locate changed Python files.
    git_diff : str | None = None
        The precomputed Git diff text to inspect. When None, the diff is derived from the repository environment.
    base_ref : str | None = None
        Optional base Git reference used to compute the diff range. When None, the base is inferred from the environment.
    on_nonlinear_push_without_base : str = 'fail'
        Behavior to use when the Git history is non-linear and no base_ref is supplied. Controls whether discovery fails or
        proceeds under this condition.

    Returns
    -------
    tuple[list[ChangedFile], DiffRange]
        Returns a tuple containing the list of changed Python files discovered from the Git diff and the corresponding diff range.

    Raises
    ------
    GitDiffError
        Explicitly raised.

    """
    git = git_executable()
    diff_range = diff_range_for_env(
        git=git,
        project_root=project_root,
        git_diff=git_diff,
        base_ref=base_ref,
        on_nonlinear_push_without_base=on_nonlinear_push_without_base,
    )
    if diff_range.revision_spec is None:
        return [], diff_range

    diff_arg = diff_range.revision_spec or "HEAD"
    command = [
        git,
        "-C",
        str(project_root),
        "diff",
        "--unified=0",
        diff_arg,
        "--",
        "*.py",
        "*.pyw",
        "*.pyi",
    ]

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        msg = f"Could not calculate the Git diff ({diff_arg}). Detail: {detail}"
        raise GitDiffError(msg) from exc

    return _parse_unified_diff(result.stdout, project_root=project_root), diff_range


def git_executable() -> str:
    """Return the absolute path to the Git executable.

    Returns
    -------
    str
        The absolute path to the Git executable.

    Raises
    ------
    RuntimeError
        Explicitly raised.

    """
    git = shutil.which("git")
    if git is None:
        msg = "git was not found in PATH. Provide a valid Git environment for docmethis_check."
        raise RuntimeError(msg)
    return git


def _is_zero_sha(value: str) -> bool:
    """Return whether a CI SHA is the all-zero marker.

    Parameters
    ----------
    value : str
        The SHA string to check.

    Returns
    -------
    bool
        True if the value is the all-zero SHA marker (i.e., has the expected SHA length and consists only of zero characters);
        False otherwise.

    """
    return len(value) == _SHA_LENGTH and set(value) == {"0"}


def _revisions_from_explicit_range(spec: str, *, git: str, project_root: Path) -> tuple[str | None, str | None]:
    """Extract base/head revisions from an explicit range.

    Parameters
    ----------
    spec : str
        A string specifying the revision range to parse. It may use '...' to indicate a merge-base comparison, '..' for a two-dot
        range, or be a simple revision to compare against the working tree.
    git : str
        The Git executable or command to use when invoking Git operations for resolving revisions.
    project_root : Path
        Path to the root directory of the Git repository, used as the working directory when invoking Git commands.

    Returns
    -------
    tuple[str | None, str | None]
        A tuple containing the base revision and head revision extracted from the explicit range; either element may be None when
        the corresponding side is not specified.

    """
    if "..." in spec:
        base_ref, _, head_ref = spec.partition("...")
        base = base_ref.strip() or "HEAD"
        head = head_ref.strip() or "HEAD"
        return _merge_base(git, project_root, base, head), head

    if ".." in spec:
        base_ref, _, head_ref = spec.partition("..")
        return base_ref.strip() or None, head_ref.strip() or None

    # Simple spec (for example, "HEAD"): diff the working tree against this revision.
    return spec.strip(), None


def _verify_git_repository(project_root: Path, *, git: str) -> None:
    """Check that the project is in a Git repository when the range is inferred.

    Parameters
    ----------
    project_root : Path
        Path to the project root directory to verify as a Git repository.
    git : str
        The Git executable to use when running repository checks.

    Raises
    ------
    GitDiffError
        Explicitly raised.

    """
    result = subprocess.run(  # noqa: S603
        [git, "-C", str(project_root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode == 0:
        return

    msg = "Cannot infer a Git range outside a Git repository. Provide --git-diff explicitly."
    raise GitDiffError(msg)


def _parse_old_file_header(line: str, *, project_root: Path) -> Path | None:
    """Parse a git diff old-file header line to return the corresponding project-rooted path, or None if it does not match.

    Parameters
    ----------
    line : str
        The line from a git diff that contains the old file header, expected to start with '--- '.
    project_root : Path
        The root directory used to resolve the old file path from the diff header.

    Returns
    -------
    Path | None
        Returns a Path representing the old file path parsed from the diff header, or None if the header cannot be parsed or does
        not correspond to an old file.

    """
    return _parse_file_header(line, line_prefix="--- ", path_prefix="a/", project_root=project_root)


def _parse_unified_diff(text: str, *, project_root: Path) -> list[ChangedFile]:
    """Parse a unified diff text into a list of ChangedFile objects with changed and deleted line numbers.

    Parameters
    ----------
    text : str
        The unified diff text to parse.
    project_root : Path
        Base directory used to resolve file paths from the unified diff headers into absolute paths.

    Returns
    -------
    list[ChangedFile]
        A list of ChangedFile objects, one per file affected by the diff, each containing the file path, the set of changed line
        numbers, and the set of deleted line numbers.

    """
    lines_by_file: dict[Path, set[int]] = {}
    deletions_by_file: dict[Path, set[int]] = {}
    current_file: Path | None = None
    old_path: Path | None = None

    for line in text.splitlines():
        if line.startswith("--- "):
            old_path = _parse_old_file_header(line, project_root=project_root)
            continue

        if line.startswith("+++ "):
            current_file = _parse_new_file_header(line, project_root=project_root)
            if current_file is None:
                current_file = old_path
            continue

        if current_file is None or not line.startswith("@@ "):
            continue

        spans = _parse_hunk_spans(line)
        if spans is None:
            continue
        (old_start, old_count), (new_start, new_count) = spans
        if new_count > 0:
            lines_by_file.setdefault(current_file, set()).update(range(new_start, new_start + new_count))
        if old_count > 0:
            deletions_by_file.setdefault(current_file, set()).update(range(old_start, old_start + old_count))

    paths = set(lines_by_file) | set(deletions_by_file)
    return [
        ChangedFile(
            path=path,
            changed_lines=frozenset(lines_by_file.get(path, set())),
            deleted_lines=frozenset(deletions_by_file.get(path, set())),
        )
        for path in sorted(paths)
    ]


def _parse_new_file_header(line: str, *, project_root: Path) -> Path | None:
    """Parse a new-file header line and return the corresponding project-rooted path, or None if not applicable.

    Parameters
    ----------
    line : str
        The diff header line to parse, expected to begin with the '+++ ' prefix.
    project_root : Path
        The project root directory against which the new file path is resolved.

    Returns
    -------
    Path | None
        Returns a Path representing the parsed new file path resolved against the project root, or None if the header line cannot
        be parsed or does not match the expected new file format.

    """
    return _parse_file_header(line, line_prefix="+++ ", path_prefix="b/", project_root=project_root)


def _parse_file_header(line: str, *, line_prefix: str, path_prefix: str, project_root: Path) -> Path | None:
    """Parse a Git ---/+++ diff line, decoding quotePath when needed.

    Parameters
    ----------
    line : str
        The Git diff header line to parse, expected to begin with the configured line prefix such as '+++' or '---'.
    line_prefix : str
        Prefix that identifies the file header line type (e.g., '+++' or '---') and is removed from the beginning of the line to
        extract the diff path.
    path_prefix : str
        Prefix to remove from the decoded Git diff path before resolving it against the project root.
    project_root : Path
        Root directory of the project, used to resolve the parsed diff path into an absolute filesystem path.

    Returns
    -------
    Path | None
        Returns the resolved absolute path to the Python file identified by the parsed diff header, or None if the header refers
        to /dev/null or the resolved path does not have a .py, .pyw, or .pyi suffix.

    """
    diff_path = line.removeprefix(line_prefix).strip()
    if diff_path == "/dev/null":
        return None

    diff_path = _decode_git_quotepath(diff_path).removeprefix(path_prefix)
    path = (project_root / diff_path).resolve()
    if path.suffix not in {".py", ".pyw", ".pyi"}:
        return None
    return path


def _decode_git_quotepath(path: str) -> str:
    """Decode a Git quotePath, including UTF-8 bytes represented in octal.

    Parameters
    ----------
    path : str
        The Git quotePath string to decode.

    Returns
    -------
    str
        The decoded path string. If the input does not start with a double quote, it is returned unchanged; otherwise, Git
        quotePath escapes are interpreted, including octal UTF-8 byte sequences and standard backslash escapes, and the result is
        decoded as UTF-8 with surrogateescape.

    """
    if not path.startswith('"'):
        return path

    content = path.strip('"')
    bytes_value = bytearray()
    index = 0
    while index < len(content):
        character = content[index]
        if character != "\\":
            bytes_value.extend(character.encode("utf-8"))
            index += 1
            continue

        index += 1
        if index >= len(content):
            bytes_value.append(ord("\\"))
            break

        next_character = content[index]
        if next_character in "01234567":
            end = index
            limit = min(index + 3, len(content))
            while end < limit and content[end] in "01234567":
                end += 1
            bytes_value.append(int(content[index:end], 8))
            index = end
            continue

        escapes = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "\\": "\\", '"': '"'}
        bytes_value.extend(escapes.get(next_character, next_character).encode("utf-8"))
        index += 1

    return bytes_value.decode("utf-8", errors="surrogateescape")


def _parse_hunk_spans(line: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Return old/new hunk spans as ``((old_start, old_count), (new_start, new_count))``.

    Parameters
    ----------
    line : str
        The diff hunk header line to parse, expected as a string containing an @@ hunk header segment.

    Returns
    -------
    tuple[tuple[int, int], tuple[int, int]] | None
        Returns the parsed old and new hunk spans as a tuple of two tuples, each containing the start and count for the
        corresponding hunk, or None if either span cannot be parsed.

    """
    segment = line.split("@@", maxsplit=2)[1]
    old_part = next((part for part in segment.split() if part.startswith("-")), None)
    new_part = next((part for part in segment.split() if part.startswith("+")), None)
    if old_part is None or new_part is None:
        return None
    return _parse_hunk_span(old_part), _parse_hunk_span(new_part)


def _parse_hunk_span(part: str) -> tuple[int, int]:
    """Parse a hunk span ``+start,count`` or ``-start,count``.

    Parameters
    ----------
    part : str
        The hunk span string to parse, formatted as a sign followed by a start line number and an optional comma-separated count
        (e.g., '+10,5' or '-3').

    Returns
    -------
    tuple[int, int]
        Parse a hunk span string like '+start,count' or '-start,count' and return a tuple of (start, count), with count defaulting
        to 1 when omitted.

    """
    start_text, _, count_text = part[1:].partition(",")
    return int(start_text), int(count_text) if count_text else 1
