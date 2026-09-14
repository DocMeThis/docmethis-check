# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""BASE reconstruction for DIA."""

from __future__ import annotations

import logging
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from docmethis_extract_python.api import (
    ConfigurationDocmethis,
    ModuleRecord,
    ProjectRecord,
    apply_project_passes,
    extract_module_record,
    is_stub_file,
    is_test_file,
    resolve_module_name,
)

from docmethis_check.diagnostics import (
    _base_content_or_none,
    _relative_path_or_none,
)
from docmethis_check.git_diff import git_executable

from .models import BaseHeadModels

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from docmethis_check.git_diff import ChangedFile

logger = logging.getLogger(__name__)

_HASH_NON_APPLICABLE = "<non_applicable>"


@contextmanager
def _base_head_models(  # noqa: PLR0913, PLR0917
    project_head: ProjectRecord,
    raw_records: list[ModuleRecord] | None,
    diff_files: list[ChangedFile],
    base_rev: str | None,
    project_root: Path,
    configuration: ConfigurationDocmethis,
    *,
    relative_path_or_none: Callable[[Path, Path], str | None] | None = None,
    base_content_or_none: Callable[[str, str, Path], str | None] | None = None,
) -> Iterator[BaseHeadModels]:
    """Rebuild the symmetric BASE model for the lifetime of the context.

    Parameters
    ----------
    project_head : ProjectRecord
        The project record representing the head version of the project, used to identify the project name and provide context for
        building the corresponding BASE model.
    raw_records : list[ModuleRecord] | None
        The raw module records for the head revision, used to build the base model; may be None if head records are unavailable.
    diff_files : list[ChangedFile]
        Sequence of changed files between the base and head revisions, used to identify which files were modified when rebuilding
        the BASE model.
    base_rev : str | None
        The base revision identifier used to build the BASE model, or None if the base revision is unavailable.
    project_root : Path
        Root directory of the project being analyzed, used to resolve the base revision and build the base modules.
    configuration : ConfigurationDocmethis
        Configuration settings used when building the base modules from raw records and diff files.
    relative_path_or_none : Callable[[Path, Path], str | None] | None = None
        Optional callable that accepts two Path objects and returns the relative path between them as a string, or None when no
        relative path can be determined. It is used to customize how relative paths are computed while rebuilding base modules.
    base_content_or_none : Callable[[str, str, Path], str | None] | None = None
        Optional callback that attempts to retrieve the base content for a given module or file during base model reconstruction.
        It receives three arguments (typically identifying the module, revision, and path) and returns the content as a string if
        available, or None when no base content can be determined. If omitted, a default resolution strategy is used.

    Returns
    -------
    Iterator[BaseHeadModels]
        Returns an iterator of BaseHeadModels. It yields a BaseHeadModels instance representing the rebuilt symmetric BASE model
        for the lifetime of the context, or an inconclusive BaseHeadModels result when head records or the base revision are
        unavailable.

    """
    if raw_records is None or not raw_records:
        yield _inconclusive(project_head, "head_records_unavailable")
        return

    if base_rev is None or not _base_revision_available(base_rev, project_root):
        yield _inconclusive(project_head, "base_revision_unavailable")
        return

    with TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        base_modules, warnings = _build_base_modules(
            raw_records,
            diff_files,
            base_rev,
            project_root,
            configuration,
            tmp_root,
            relative_path_or_none=relative_path_or_none,
            base_content_or_none=base_content_or_none,
        )

        base_project = ProjectRecord(project_root=project_root, project_name=project_head.project_name)
        base_project.modules = base_modules
        _apply_project_passes(base_project)

        yield BaseHeadModels(
            base=base_project,
            completeness="partial" if warnings else "complete",
            reason=" | ".join(warnings) if warnings else None,
        )


def _build_base_modules(  # noqa: C901, PLR0913, PLR0917
    raw_records: list[ModuleRecord],
    diff_files: list[ChangedFile],
    base_rev: str,
    project_root: Path,
    configuration: ConfigurationDocmethis,
    tmp_root: Path,
    *,
    relative_path_or_none: Callable[[Path, Path], str | None] | None = None,
    base_content_or_none: Callable[[str, str, Path], str | None] | None = None,
) -> tuple[list[ModuleRecord], list[str]]:
    """Build BASE modules from head pre-pass records and ``git show``.

    Parameters
    ----------
    raw_records : list[ModuleRecord]
        Raw module records produced by the head pre-pass, used as the starting set for building BASE modules and matched against
        changed files.
    diff_files : list[ChangedFile]
        List of changed files between the base and head revisions, used to determine which module records need to be rebuilt from
        base content and which can be kept from the head pre-pass records.
    base_rev : str
        Revision identifier of the base commit or branch used to retrieve and inspect the original versions of changed files via
        git.
    project_root : Path
        Root directory of the project, used to resolve file paths and to locate repository content when reading BASE revision
        files.
    configuration : ConfigurationDocmethis
        Configuration settings used when extracting BASE module records from temporary files.
    tmp_root : Path
        Temporary root directory used when extracting base module records from git show content.
    relative_path_or_none : Callable[[Path, Path], str | None] | None = None
        Optional callable used to derive a file's project-relative path from its absolute path and the project root. It is called
        with two Path objects (the file path and the project root) and must return the relative path as a string, or None to
        indicate that the file is outside the project root. When omitted, a default implementation is used.
    base_content_or_none : Callable[[str, str, Path], str | None] | None = None
        A callable that retrieves the base version of a file's content. It is invoked with the base revision, the file's
        repository-relative path, and the project root, and should return the file content as a string, or None if the content
        cannot be obtained. When omitted, a default implementation based on git show is used.

    Returns
    -------
    tuple[list[ModuleRecord], list[str]]
        A tuple containing the list of base ModuleRecord objects that were successfully built and a list of warning strings for
        any files that could not be processed.

    """
    relative_path_hook = relative_path_or_none or _relative_path_or_none
    base_content_hook = base_content_or_none or _base_content_or_none
    modules_by_file = {module.file_path.resolve(): module for module in raw_records}
    diff_paths = {changed_file.path.resolve(): changed_file for changed_file in diff_files}
    warnings: list[str] = []
    base_modules: list[ModuleRecord] = []

    for path, module in modules_by_file.items():
        if path not in diff_paths:
            base_modules.append(module)
            continue

        relative_path = relative_path_hook(path, project_root)
        if relative_path is None:
            warnings.append(f"BASE file outside project root ignored: {path}")
            continue

        exists, status_error = _base_file_status(base_rev, relative_path, project_root)
        if not exists and status_error is None:
            logger.info("File absent from base (added or renamed): %s", relative_path)
            continue

        content = base_content_hook(base_rev, relative_path, project_root)
        if content is None:
            warnings.append(f"BASE read failed for {relative_path} ({status_error or 'git show'})")
            continue

        record = _extract_module_in_tmp(tmp_root, relative_path, content, configuration)
        if record is not None:
            base_modules.append(record)

    for path in diff_paths:
        if path in modules_by_file:
            continue

        relative_path = relative_path_hook(path, project_root)
        if relative_path is None:
            continue

        exists, status_error = _base_file_status(base_rev, relative_path, project_root)
        if not exists and status_error is None:
            logger.info("File absent from both base and head (rename): %s", relative_path)
            continue

        content = base_content_hook(base_rev, relative_path, project_root)
        if content is None:
            warnings.append(f"BASE read failed for {relative_path} ({status_error or 'git show'})")
            continue

        record = _extract_module_in_tmp(tmp_root, relative_path, content, configuration)
        if record is not None:
            base_modules.append(record)

    return base_modules, warnings


def _inconclusive(project_head: ProjectRecord, reason: str) -> BaseHeadModels:
    """Build a degraded DIA result.

    Parameters
    ----------
    project_head : ProjectRecord
        The original project record used as the basis for the degraded DIA result.
    reason : str
        A human-readable string describing why the DIA result is inconclusive.

    Returns
    -------
    BaseHeadModels
        Constructs a BaseHeadModels instance marked as inconclusive, using the given project head as the base project and the
        provided reason.

    """
    base = ProjectRecord(
        project_root=project_head.project_root,
        project_name=project_head.project_name,
    )
    return BaseHeadModels(base=base, completeness="inconclusive", reason=reason)


def _apply_project_passes(project: ProjectRecord) -> None:
    """Apply the same project passes as the head analysis.

    Parameters
    ----------
    project : ProjectRecord
        The project record to which the passes are applied.

    """
    apply_project_passes(project)


def _base_revision_available(base_rev: str, project_root: Path) -> bool:
    """Return whether ``base_rev`` resolves to an existing Git commit.

    Parameters
    ----------
    base_rev : str
        The Git revision to check for existence as a commit.
    project_root : Path
        Root directory of the Git repository where the base revision is checked.

    Returns
    -------
    bool
        Return True if ``base_rev`` resolves to an existing Git commit in the repository at ``project_root``; otherwise return
        False.

    """
    command = [
        git_executable(),
        "-C",
        str(project_root),
        "rev-parse",
        "--verify",
        "--quiet",
        f"{base_rev}^{{commit}}",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)  # noqa: S603
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _base_file_status(base_rev: str, relative_path: str, project_root: Path) -> tuple[bool, str | None]:
    """Return a base file's existence and any read error.

    Parameters
    ----------
    base_rev : str
        Revision used to locate the base file in the repository, such as a commit hash or branch name.
    relative_path : str
        Relative path of the file whose status is being checked, interpreted as a path within the repository.
    project_root : Path
        Root directory of the project, used as the context for the git cat-file command.

    Returns
    -------
    tuple[bool, str | None]
        Returns a tuple containing a boolean indicating whether the base file exists at the given revision and path, and an
        optional string describing an error if the existence check could not be completed; the string is None when no error
        occurred.

    """
    command = [
        git_executable(),
        "-C",
        str(project_root),
        "cat-file",
        "-e",
        f"{base_rev}:{relative_path}",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)  # noqa: S603
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"cat-file failed: {type(exc).__name__}"
    return result.returncode == 0, None


def _extract_module_in_tmp(
    tmp_root: Path,
    relative_path: str,
    content: str,
    configuration: ConfigurationDocmethis,
) -> ModuleRecord | None:
    """Extract a module record from materialized base content.

    Parameters
    ----------
    tmp_root : Path
        Root directory under which the temporary module file is created from the provided content and relative path.
    relative_path : str
        Relative path of the module file within the temporary root, used to construct the destination path for the materialized
        content.
    content : str
        The text content to be written into the temporary module file before extraction.
    configuration : ConfigurationDocmethis
        Configuration settings that control module extraction behavior, including the maximum number of methods allowed for mixin
        detection.

    Returns
    -------
    ModuleRecord | None
        Returns the extracted module record, or None when no module record can be produced from the materialized content.

    """
    tmp_path = tmp_root / relative_path
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(content, encoding="utf-8")

    module_name = resolve_module_name(tmp_path, tmp_root)
    return extract_module_record(
        tmp_path,
        module_name,
        is_stub=is_stub_file(tmp_path),
        is_test=is_test_file(tmp_path, tmp_root),
        file_hash=_HASH_NON_APPLICABLE,
        mixin_max_methods=configuration.mixin_max_methods,
    )
