# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Orchestrate checks on a diff: M1 -> docstring parser -> Verify."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from docmethis_extract_python.api import (
    FunctionRecord,
    ModuleRecord,
    ProjectRecord,
    Visibility,
    analyze_project,
    effective_visibility,
    iter_functions,
)

from docmethis_check.config import CheckConfig, load_check_config
from docmethis_check.dia import _analyze_documentation_impacts
from docmethis_check.diagnostics import base_classes, base_diagnostics, base_functions, diagnostics_for_file
from docmethis_check.git_diff import ChangedFile, DiffRange, discover_changed_python_files
from docmethis_check.models import CheckEntry, CheckMode, CheckResult, ImpactAnalysis, Severity, SymbolKind

if TYPE_CHECKING:
    from collections import Counter

    from docmethis_check.diagnostics import BaseSnapshot, DiagnosticKey

logger = logging.getLogger(__name__)


class MissingBaseRevisionError(RuntimeError):
    """The requested base revision is unavailable."""


@dataclass
class _DiagnosticContext:
    """Context shared by file diagnostic functions.

    Attributes
    ----------
    configuration : CheckConfig
        Check configuration controlling diagnostic emission.
    diff_range : DiffRange
        Resolved Git range for the check run.
    root : Path
        Root directory of the project.
    base_snapshot_cache : dict[tuple[str, Path], BaseSnapshot | None]
        Cache mapping (revision, file path) to base snapshots.

    """

    configuration: CheckConfig
    diff_range: DiffRange
    root: Path
    base_snapshot_cache: dict[tuple[str, Path], BaseSnapshot | None] = field(default_factory=dict)


@dataclass
class _RegressionFilterSignal:
    """Signal for degraded regression filtering.

    Attributes
    ----------
    completeness : str
        Completeness indicator of the regression filter.
    reason : str
        Reason for the degraded filtering.

    """

    completeness: str
    reason: str


@dataclass
class _BaseKeysResult:
    """Result of resolving base keys for a file.

    Attributes
    ----------
    keys : Counter[DiagnosticKey] | None
        Base diagnostic keys, or None when the base could not be resolved.
    regression_filter_signal : _RegressionFilterSignal | None
        Signal of degraded regression filtering, when filtering was degraded.

    """

    keys: Counter[DiagnosticKey] | None = None
    regression_filter_signal: _RegressionFilterSignal | None = None


@dataclass(frozen=True, slots=True)
class _SymbolCheck:
    """Stable identity of a checked symbol.

    Attributes
    ----------
    kind : SymbolKind
        Kind of the checked symbol.
    symbol : str
        Qualified name of the checked symbol.
    accessor_kind : str | None
        Property accessor role, when the symbol is a property function.

    """

    kind: SymbolKind
    symbol: str
    accessor_kind: str | None = None


class _RecordWithSourceRange(Protocol):
    """M1 record exposing a source range usable by the diff.

    Attributes
    ----------
    line_start : int
        Starting line of the record's source range.
    line_end : int
        Ending line of the record's source range.

    """

    line_start: int
    line_end: int


class _RecordWithIdentity(Protocol):
    """M1 record exposing the identity needed for Check selection.

    Attributes
    ----------
    qualified_name : str
        Canonical qualified name of the record.
    visibility : Visibility
        Visibility of the record.

    """

    qualified_name: str
    visibility: Visibility


def _record_affected_by_lines(record: _RecordWithSourceRange, lines: frozenset[int]) -> bool:
    """Return whether an M1 record source range intersects the given lines.

    Parameters
    ----------
    record : _RecordWithSourceRange
        The M1 record whose source range is checked for intersection with the given lines.
    lines : frozenset[int]
        The set of line numbers to test for intersection with the record's source range.

    Returns
    -------
    bool
        True if the record's source range includes at least one of the given lines; False otherwise.

    """
    return any(record.line_start <= line <= record.line_end for line in lines)


def _module_affected_by_diff(changed_file: ChangedFile) -> bool:
    """Return whether a changed file triggers the module check.

    Parameters
    ----------
    changed_file : ChangedFile
        The changed file whose changed or deleted lines determine whether the module check is triggered.

    Returns
    -------
    bool
        True if the changed file contains any changed or deleted lines, otherwise False.

    """
    return bool(changed_file.changed_lines or changed_file.deleted_lines)


def _function_symbol_kind(func: FunctionRecord) -> SymbolKind:
    """Return the Check kind for an M1 function.

    Parameters
    ----------
    func : FunctionRecord
        The FunctionRecord representing the M1 function whose symbol kind should be determined.

    Returns
    -------
    SymbolKind
        The SymbolKind indicating whether the function is a method (if it has a parent class) or a standalone function.

    """
    return SymbolKind.METHOD if func.parent_class is not None else SymbolKind.FUNCTION


def _record_accessor_kind(record: object) -> str | None:
    """Return a stable getter/setter role for a property record."""
    method_kind = getattr(record, "method_kind", None)
    if getattr(method_kind, "value", method_kind) != "property":
        return None

    accessor = getattr(record, "property_accessor", None)
    value = getattr(accessor, "value", accessor)
    return value if value in {"getter", "setter"} else "getter"


def _selected_record_symbol(
    record: _RecordWithIdentity,
    *,
    kind: SymbolKind,
    module_visibility: Visibility,
    config: CheckConfig,
    container_visibility: Visibility | None = None,
) -> _SymbolCheck | None:
    """Return an M1 record's Check identity when it is in the analyzed scope.

    Container visibility (a class) is composed with module and symbol visibility: a public method in a private class is private.

    Parameters
    ----------
    record : _RecordWithIdentity
        The record to evaluate for symbol checking and identity resolution.
    kind : SymbolKind
        The kind of symbol to use for the record being checked.
    module_visibility : Visibility
        Visibility of the module that contains the record; it is combined with the record's own visibility and any container
        visibility to determine the effective visibility used for scope filtering.
    config : CheckConfig
        CheckConfig instance that determines which symbol kinds and visibility levels are considered part of the analyzed scope;
        the record is only selected if its kind and effective visibility are included by this configuration.
    container_visibility : Visibility | None = None
        Optional visibility of the containing class or other container; when provided, it is composed with the module and symbol
        visibility to determine the effective visibility.

    Returns
    -------
    _SymbolCheck | None
        The symbol check for the record if it is in the analyzed scope, or None if the record's kind or effective visibility is
        not included in the configuration.

    """
    accessor_kind = _record_accessor_kind(record)
    if accessor_kind is not None and accessor_kind not in {accessor.value for accessor in config.property_accessors}:
        return None
    symbol = _SymbolCheck(kind=kind, symbol=record.qualified_name, accessor_kind=accessor_kind)
    if symbol.kind not in config.symbol_kinds:
        return None
    visibility = effective_visibility(module_visibility, record.visibility)
    if container_visibility is not None:
        visibility = effective_visibility(visibility, container_visibility)
    if visibility not in config.include_visibility:
        return None
    return symbol


def _entry_symbol(entry: CheckEntry) -> _SymbolCheck:
    """Build the Check identity for a diagnostic entry.

    Parameters
    ----------
    entry : CheckEntry
        The diagnostic entry whose symbol information is used to construct the Check identity.

    Returns
    -------
    _SymbolCheck
        A _SymbolCheck containing the symbol kind and symbol from the given entry.

    """
    return _SymbolCheck(kind=entry.symbol_kind, symbol=entry.symbol, accessor_kind=entry.accessor_kind)


def _module_visibility_from_name(module_name: str) -> Visibility:
    """Determine the effective visibility of a module name.

    Parameters
    ----------
    module_name : str
        Name of the module whose visibility is to be determined, as a dot-separated string.

    Returns
    -------
    Visibility
        The effective visibility of the module name, either Visibility.PUBLIC or Visibility.PROTECTED.

    """
    if any(segment != "__init__" and segment.startswith("_") for segment in module_name.split(".")):
        return Visibility.PROTECTED
    return Visibility.PUBLIC


def run_check(  # noqa: PLR0913 - public API; the parameter count is intentional.
    project: str | Path,
    git_diff: str | None = None,
    *,
    write_cache: bool = True,
    base_ref: str | None = None,
    on_nonlinear_push_without_base: str | None = None,
    config: CheckConfig | None = None,
) -> CheckResult:
    """Run the check on Python files changed by the Git diff.

    Parameters
    ----------
    project : str | Path
        Project root path to run the check against. May be provided as a string or Path object.
    git_diff : str | None = None
        Optional Git diff string used to determine which Python files have changed. If not provided, the diff is derived from the
        configured base reference.
    write_cache : bool = True
        Whether to write the cache used during project analysis.
    base_ref : str | None = None
        Base ref to use for determining changed Python files; overrides the configured base ref when provided. If None, the
        configured base ref is used.
    on_nonlinear_push_without_base : str | None = None
        Specifies the behavior to use when a nonlinear Git push is detected and no base reference is available. When provided,
        this value overrides the corresponding configuration setting; otherwise the configured value is used.
    config : CheckConfig | None = None
        Keyword-only parameter that supplies an optional CheckConfig object to override the project's loaded check configuration.
        When omitted or None, the configuration is loaded automatically from the project root.

    Returns
    -------
    CheckResult
        A CheckResult containing the outcome of the check, including diff metadata, the Python files that were checked, any
        diagnostics collected, and, when dependency impact analysis is enabled, the impact analysis.

    """
    root = Path(project).resolve()
    configuration = config or load_check_config(root)

    effective_base_ref = base_ref or configuration.base_ref
    effective_on_nonlinear = (
        on_nonlinear_push_without_base
        if on_nonlinear_push_without_base is not None
        else configuration.on_nonlinear_push_without_base
    )

    changed_files_list, diff_range = discover_changed_python_files(
        root,
        git_diff=git_diff,
        base_ref=effective_base_ref,
        on_nonlinear_push_without_base=effective_on_nonlinear,
    )
    changed_files = {changed_file.path: changed_file for changed_file in changed_files_list}

    result = CheckResult()
    result.diff_strategy = diff_range.strategy
    result.diff_completeness = diff_range.completeness
    result.diff_reason = diff_range.reason
    result.diff_files = [_changed_file_to_dict(changed_file) for changed_file in changed_files_list]

    if diff_range.revision_spec is None:
        if configuration.dia:
            result.impact_analysis = ImpactAnalysis(completeness="inconclusive", reason="missing_diff_base")
        return result

    raw_records: list[ModuleRecord] = []
    parser_logger = logging.getLogger("docmethis_extract_python.static_extraction.ast_parser")
    previous_parser_level = parser_logger.level
    parser_logger.setLevel(logging.ERROR)
    try:
        project_record = analyze_project(
            root,
            skip_dynamic=True,
            write_cache=write_cache,
            raw_records=raw_records if configuration.dia else None,
        )
    finally:
        # Parse failures are rendered in the report instead of leaking as a separate warning line.
        parser_logger.setLevel(previous_parser_level)

    ctx = _DiagnosticContext(configuration=configuration, diff_range=diff_range, root=root)
    affected_symbols, files_by_module = _collect_affected_symbols(project_record, changed_files, ctx)
    _execute_file_diagnostics(files_by_module, affected_symbols, result, ctx)

    if configuration.dia:
        _execute_dia(
            result,
            project=project_record,
            raw_records=raw_records,
            diff_files=changed_files_list,
            base_rev=diff_range.base_rev,
            root=root,
            configuration=configuration,
        )

    result.analysis_errors = _analysis_errors(project_record, changed_files, root)
    result.checked_files = sorted(str(path) for path in files_by_module)
    return result


def _changed_file_to_dict(changed_file: ChangedFile) -> dict[str, object]:
    """Serialize diff membership for Fix consumption.

    Parameters
    ----------
    changed_file : ChangedFile
        The changed file whose path, changed lines, and deleted lines are serialized into a dictionary.

    Returns
    -------
    dict[str, object]
        A dictionary containing the changed file's path, the sorted changed line numbers, and the sorted deleted line numbers.

    """
    return {
        "path": str(changed_file.path),
        "changed_lines": sorted(changed_file.changed_lines),
        "deleted_lines": sorted(changed_file.deleted_lines),
    }


def _analysis_errors(project: ProjectRecord, changed_files: dict[Path, ChangedFile], root: Path) -> list[dict[str, str]]:
    """Return analysis failures belonging to the requested check scope."""
    if project.quality_report is None:
        return []
    changed_paths = set(changed_files)
    errors: list[dict[str, str]] = []
    for error in project.quality_report.errors:
        path = Path(error.source_file)
        if not path.is_absolute():
            path = root / path
        if path.resolve() not in changed_paths:
            continue
        errors.append({"file": str(path.resolve()), "type": error.type_error, "message": error.message})
    return errors


def _execute_dia(  # noqa: PLR0913
    result: CheckResult,
    *,
    project: ProjectRecord,
    raw_records: list[ModuleRecord],
    diff_files: list[ChangedFile],
    base_rev: str | None,
    root: Path,
    configuration: CheckConfig,
) -> None:
    """Run documentation impact analysis and merge its results.

    Parameters
    ----------
    result : CheckResult
        The CheckResult instance that will be updated with the impact analysis outcome and appended check entries.
    project : ProjectRecord
        The ProjectRecord instance that identifies the project for which documentation impact analysis is performed.
    raw_records : list[ModuleRecord]
        A list of module records representing the raw project modules to be used as input for the documentation impact analysis.
    diff_files : list[ChangedFile]
        A list of changed files to be used as input for the documentation impact analysis.
    base_rev : str | None
        The base revision against which documentation impact analysis is performed; may be None to indicate no base revision.
    root : Path
        Root directory of the project, used as the base path for resolving locations during documentation impact analysis.
    configuration : CheckConfig
        Configuration settings for the documentation impact analysis, used to control how the check is performed.

    """
    analysis = _analyze_documentation_impacts(
        project_head=project,
        raw_records=raw_records,
        diff_files=diff_files,
        base_rev=base_rev,
        project_root=root,
        check_config=configuration,
    )
    result.impact_analysis = analysis.impact_analysis
    for entry in analysis.entries:
        result.checks.append(entry)
        _increment_summary(result, entry.severity)


def _collect_affected_symbols(  # noqa: C901, PLR0912
    project: ProjectRecord,
    changed_files: dict[Path, ChangedFile],
    ctx: _DiagnosticContext,
) -> tuple[set[_SymbolCheck], dict[Path, list[_SymbolCheck]]]:
    """Identify symbols affected by the diff, grouped by file.

    The visibility filter duplicates ``diagnostics_for_function``. Applying it here avoids collecting symbols that would be
    ignored later and skewing ``pass_count``.

    Parameters
    ----------
    project : ProjectRecord
        The project record whose modules are scanned to identify symbols affected by the diff.
    changed_files : dict[Path, ChangedFile]
        Mapping of file paths to their corresponding changed-file records, used to determine which modules and symbols are
        affected by the diff.
    ctx : _DiagnosticContext
        Diagnostic context providing configuration and shared state used to determine symbol kinds, visibility filters, and
        base-deletion analysis.

    Returns
    -------
    tuple[set[_SymbolCheck], dict[Path, list[_SymbolCheck]]]
        Returns a tuple where the first element is a set of affected _SymbolCheck objects, and the second element is a dictionary
        mapping file paths to lists of affected _SymbolCheck objects grouped by file.

    """
    affected_symbols: set[_SymbolCheck] = set()
    files_by_module: dict[Path, list[_SymbolCheck]] = {}
    deleted_symbols_cache: dict[Path, set[_SymbolCheck]] = {}
    affected_modules = [
        module
        for module in project.modules
        if module.file_path.resolve() in changed_files
        # These conditions intentionally overlap: ``module_name.startswith("test_")``
        # covers test directories, while ``is_test`` covers M1-marked files.
        and not module.module_name.startswith("test_")
        and not module.is_test
    ]
    for module in affected_modules:
        path = module.file_path.resolve()
        changed_file = changed_files.get(path)
        if changed_file is None:
            continue

        if SymbolKind.MODULE in ctx.configuration.symbol_kinds:
            symbol = _SymbolCheck(kind=SymbolKind.MODULE, symbol=module.module_name)
            if module.visibility in ctx.configuration.include_visibility and _module_affected_by_diff(changed_file):
                affected_symbols.add(symbol)
                files_by_module.setdefault(path, []).append(symbol)

        for class_record in module.classes:
            symbol = _selected_record_symbol(
                class_record,
                kind=SymbolKind.CLASS,
                module_visibility=module.visibility,
                config=ctx.configuration,
            )
            if symbol is None or symbol in affected_symbols:
                continue
            if not _record_affected_by_lines(class_record, changed_file.changed_lines):
                if path not in deleted_symbols_cache:
                    deleted_symbols_cache[path] = _symbols_affected_by_base_deletions(changed_file, ctx)
                if symbol not in deleted_symbols_cache[path]:
                    continue
            affected_symbols.add(symbol)
            files_by_module.setdefault(path, []).append(symbol)

        class_visibilities = {
            method.qualified_name: class_record.visibility for class_record in module.classes for method in class_record.methods
        }
        for func in iter_functions([module]):
            symbol = _selected_record_symbol(
                func,
                kind=_function_symbol_kind(func),
                module_visibility=module.visibility,
                config=ctx.configuration,
                container_visibility=class_visibilities.get(func.qualified_name),
            )
            if symbol is None:
                continue
            if not _record_affected_by_lines(func, changed_file.changed_lines):
                if path not in deleted_symbols_cache:
                    deleted_symbols_cache[path] = _symbols_affected_by_base_deletions(changed_file, ctx)
                if symbol not in deleted_symbols_cache[path]:
                    continue
            if symbol not in affected_symbols:
                affected_symbols.add(symbol)
                files_by_module.setdefault(path, []).append(symbol)
    return affected_symbols, files_by_module


def _symbols_affected_by_base_deletions(changed_file: ChangedFile, ctx: _DiagnosticContext) -> set[_SymbolCheck]:
    """Identify base functions and classes affected by deleted lines.

    Parameters
    ----------
    changed_file : ChangedFile
        The changed file to inspect, providing the file path and the deleted line numbers used to determine which base functions
        and classes are affected.
    ctx : _DiagnosticContext
        Diagnostic context that supplies the base revision, project root, base snapshot cache, and configuration.

    Returns
    -------
    set[_SymbolCheck]
        Returns the set of _SymbolCheck objects for base functions and classes whose recorded line ranges overlap the deleted
        lines in the changed file.

    """
    if not changed_file.deleted_lines or not ctx.diff_range.base_rev:
        return set()

    symbols: set[_SymbolCheck] = set()
    functions = base_functions(
        ctx.diff_range.base_rev,
        changed_file.path,
        ctx.root,
        snapshot_cache=ctx.base_snapshot_cache,
    )
    if functions is None:
        return set()

    for func in functions:
        module_visibility = _module_visibility_from_name(func.parent_module)
        symbol = _selected_record_symbol(
            func,
            kind=_function_symbol_kind(func),
            module_visibility=module_visibility,
            config=ctx.configuration,
        )
        if symbol is None:
            continue
        if _record_affected_by_lines(func, changed_file.deleted_lines):
            symbols.add(symbol)

    classes = base_classes(
        ctx.diff_range.base_rev,
        changed_file.path,
        ctx.root,
        snapshot_cache=ctx.base_snapshot_cache,
    )
    if classes is None:
        return symbols

    for class_record in classes:
        module_visibility = _module_visibility_from_name(class_record.parent_module)
        symbol = _selected_record_symbol(
            class_record,
            kind=SymbolKind.CLASS,
            module_visibility=module_visibility,
            config=ctx.configuration,
        )
        if symbol is None:
            continue
        if _record_affected_by_lines(class_record, changed_file.deleted_lines):
            symbols.add(symbol)

    return symbols


def _execute_file_diagnostics(
    files_by_module: dict[Path, list[_SymbolCheck]],
    affected_symbols: set[_SymbolCheck],
    result: CheckResult,
    ctx: _DiagnosticContext,
) -> None:
    """Run file-by-file diagnostics with regression filtering.

    Parameters
    ----------
    files_by_module : dict[Path, list[_SymbolCheck]]
        A mapping from file paths to the lists of symbol-check objects that should be diagnosed for those files.
    affected_symbols : set[_SymbolCheck]
        The set of symbols that should be considered when emitting diagnostics; only entries whose symbol is present in this set
        are included in the result.
    result : CheckResult
        The CheckResult instance to be populated with the diagnostics emitted for the affected symbols.
    ctx : _DiagnosticContext
        Diagnostic context providing the project root and configuration used when running file diagnostics.

    """
    base_keys_cache: dict[Path, Counter[DiagnosticKey]] = {}
    for path, symbols in files_by_module.items():
        try:
            head_content = path.read_text(encoding="utf-8")
        except OSError:
            continue

        head_entries, head_keys = diagnostics_for_file(head_content, path, ctx.root, ctx.configuration)
        base_result = _get_base_keys(path, ctx, base_keys_cache)
        if base_result.regression_filter_signal is not None and result.regression_filter_completeness is None:
            result.regression_filter_completeness = base_result.regression_filter_signal.completeness
            result.regression_filter_reason = base_result.regression_filter_signal.reason
        emit_entries = (
            _filter_regression(head_entries, head_keys, base_result.keys) if base_result.keys is not None else head_entries
        )

        head_diagnostic_symbols = {_entry_symbol(entry) for entry in head_entries}
        for entry in emit_entries:
            if _entry_symbol(entry) not in affected_symbols:
                continue
            result.checks.append(entry)
            _increment_summary(result, entry.severity)

        for symbol in symbols:
            if symbol not in head_diagnostic_symbols:
                result.summary.pass_count += 1


def _get_base_keys(
    path: Path,
    ctx: _DiagnosticContext,
    base_keys_cache: dict[Path, Counter[DiagnosticKey]],
) -> _BaseKeysResult:
    """Return base keys for regression filtering.

    The ``keys`` field is ``None`` when regression does not apply or the base is unavailable.

    Parameters
    ----------
    path : Path
        Path to the source file for which base keys are retrieved.
    ctx : _DiagnosticContext
        Diagnostic context that supplies configuration, diff range, repository root, and related caches used to determine base
        keys for regression filtering.
    base_keys_cache : dict[Path, Counter[DiagnosticKey]]
        Cache mapping file paths to counters of diagnostic keys, used to store and reuse base keys for regression filtering across
        calls.

    Returns
    -------
    _BaseKeysResult
        Returns a _BaseKeysResult containing the base diagnostic keys for regression filtering. The keys field is None when
        regression does not apply or the base revision is unavailable; when the base is missing and on_missing_base is 'fail', a
        MissingBaseRevisionError is raised instead.

    Raises
    ------
    MissingBaseRevisionError
        Explicitly raised.

    """
    if ctx.configuration.check_mode != CheckMode.REGRESSION or not ctx.diff_range.base_rev:
        return _BaseKeysResult()
    if path in base_keys_cache:
        return _BaseKeysResult(keys=base_keys_cache[path])
    base_result = base_diagnostics(
        ctx.diff_range.base_rev,
        path,
        ctx.root,
        ctx.configuration,
        snapshot_cache=ctx.base_snapshot_cache,
    )
    if base_result is not None:
        _, base_keys = base_result
        base_keys_cache[path] = base_keys
        return _BaseKeysResult(keys=base_keys)
    if ctx.configuration.on_missing_base == "fail":
        msg = f"Base revision {ctx.diff_range.base_rev} is unavailable for {path}"
        raise MissingBaseRevisionError(msg)
    return _BaseKeysResult(
        regression_filter_signal=_RegressionFilterSignal(
            completeness="catchup_without_base",
            reason="missing_base_revision",
        ),
    )


def _filter_regression(
    entries: list[CheckEntry],
    keys: Counter[DiagnosticKey],
    base_keys: Counter[DiagnosticKey] | None,
) -> list[CheckEntry]:
    """Keep only entries whose key represents a new regression.

    Preserve Counter cardinality: if HEAD has two identical occurrences and the
    base has one, emit only one (regression = head - base).

    Parameters
    ----------
    entries : list[CheckEntry]
        The check entries to filter; only entries whose diagnostic key represents a new regression are retained.
    keys : Counter[DiagnosticKey]
        Counter of diagnostic keys for the current run, used together with base_keys to determine which entries represent new
        regressions.
    base_keys : Counter[DiagnosticKey] | None
        Optional Counter of diagnostic keys from the base revision, used to subtract pre-existing occurrences from the head keys;
        if None, no regression filtering is applied and all entries are returned.

    Returns
    -------
    list[CheckEntry]
        A list of CheckEntry objects containing only the entries whose diagnostic key represents a new regression, with
        cardinality preserved so that each occurrence beyond the base count is emitted once.

    """
    if base_keys is None:
        return list(entries)
    regression_keys = keys - base_keys
    result: list[CheckEntry] = []
    for entry in entries:
        k = entry.diagnostic_key  # Internal field set by diagnostics_for_function.
        if k is None:
            logger.warning("Entry without diagnostic_key ignored by regression filter: %s", entry.symbol)
            continue
        if regression_keys.get(k, 0) > 0:
            result.append(entry)
            regression_keys[k] -= 1
    return result


def _increment_summary(result: CheckResult, severity: Severity) -> None:
    """Increment summary counters according to severity.

    Parameters
    ----------
    result : CheckResult
        The check result whose summary counters are incremented.
    severity : Severity
        The severity level of the check result, used to determine which summary counter to increment.

    """
    if severity == "error":
        result.summary.error_count += 1
    elif severity == "warning":
        result.summary.warning_count += 1
    elif severity == "disabled":
        return
    else:
        logger.warning("Unknown severity in _increment_summary: %s", severity)
