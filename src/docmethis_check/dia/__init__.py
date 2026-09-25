# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""DIA compatibility facade and phase coordinator."""

# Private-module access below is intentional: this file preserves the legacy facade.
# ruff: noqa: SLF001

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from functools import wraps
from typing import TYPE_CHECKING

from docmethis_extract_python.api import (
    MISSING_VALUE,  # noqa: F401
    ConfigurationDocmethis,
    ModuleRecord,
    ProjectRecord,
    build_callgraph,
    iter_functions,
    load_configuration,
)
from docmethis_verify.dmt import DIA_CODE_BY_IMPACT_KIND  # noqa: F401

from docmethis_check.config import path_matches_filters
from docmethis_check.diagnostics import (
    DiagnosticKey,  # noqa: F401
    _base_content_or_none,
    _compute_docstring_sha256,
    _relative_path_or_none,
    _simple_exception_name,
)
from docmethis_check.models import CheckEntry, ImpactAnalysis

from . import api as _dia_api
from . import base as _dia_base
from . import behavior as _dia_behavior
from . import context as _dia_context
from . import emission as _dia_emission
from . import models as _dia_models
from . import propagation as _dia_propagation
from . import report as _dia_report

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from docmethis_extract_python.api import CallgraphEdge

    from docmethis_check.config import CheckConfig
    from docmethis_check.git_diff import ChangedFile


# Re-export the original DIA model surface.
DocumentationOrigin = _dia_models.DocumentationOrigin
DispatchRole = _dia_models.DispatchRole
ImplementationKind = _dia_models.ImplementationKind
SymbolContext = _dia_models.SymbolContext
BaseHeadModels = _dia_models.BaseHeadModels
BehavioralKey = _dia_models.BehavioralKey
DeltaBehavioral = _dia_models.DeltaBehavioral
DeclarativeParameter = _dia_models.DeclarativeParameter
DeclarativeSignature = _dia_models.DeclarativeSignature
DeclarativeChange = _dia_models.DeclarativeChange
MetaDia = _dia_models.MetaDia
DocumentationImpact = _dia_models.DocumentationImpact
EmissionDelta = _dia_models.EmissionDelta
DiaEmissionContext = _dia_models.DiaEmissionContext
DiaResult = _dia_models.DiaResult

_HASH_NON_APPLICABLE = _dia_base._HASH_NON_APPLICABLE
_CATEGORIES_IO = _dia_behavior._CATEGORIES_IO
_EMPTY_FACT_VALUE = _dia_behavior._EMPTY_FACT_VALUE
_EXCEPTION_ROOTS = _dia_behavior._EXCEPTION_ROOTS
_DEFAULT_SEVERITIES = _dia_emission._DEFAULT_SEVERITIES
_DEFAULT_FIX_SUPPORT = _dia_emission._DEFAULT_FIX_SUPPORT


@contextmanager
def _base_head_models(  # noqa: PLR0913, PLR0917
    project_head: ProjectRecord,
    raw_records: list[ModuleRecord] | None,
    diff_files: list[ChangedFile],
    base_rev: str | None,
    project_root: Path,
    configuration: ConfigurationDocmethis,
) -> Iterator[BaseHeadModels]:
    """Re-export BASE reconstruction while forwarding patched legacy hooks.

    Parameters
    ----------
    project_head : ProjectRecord
        The project record that identifies the head project whose base-head models are to be reconstructed.
    raw_records : list[ModuleRecord] | None
        Optional list of module records containing the raw record data to be processed.
    diff_files : list[ChangedFile]
        Sequence of ChangedFile objects representing the files that differ between revisions, used to scope the BASE
        reconstruction and legacy hook forwarding.
    base_rev : str | None
        The base revision identifier used as the comparison baseline, or None if no base revision is available.
    project_root : Path
        Root directory of the project; used to resolve relative paths when reconstructing base models.
    configuration : ConfigurationDocmethis
        The DocMeThis configuration object used during base head model reconstruction and legacy hook forwarding.

    Returns
    -------
    Iterator[BaseHeadModels]
        An iterator yielding BaseHeadModels instances representing the reconstructed base head models.

    """
    with _dia_base._base_head_models(
        project_head,
        raw_records,
        diff_files,
        base_rev,
        project_root,
        configuration,
        relative_path_or_none=_relative_path_or_none,
        base_content_or_none=_base_content_or_none,
    ) as outcome:
        yield outcome


def _build_base_modules(  # noqa: PLR0913, PLR0917
    raw_records: list[ModuleRecord],
    diff_files: list[ChangedFile],
    base_rev: str,
    project_root: Path,
    configuration: ConfigurationDocmethis,
    tmp_root: Path,
) -> tuple[list[ModuleRecord], list[str]]:
    """Re-export BASE module construction while forwarding patched hooks.

    Parameters
    ----------
    raw_records : list[ModuleRecord]
        The raw module records to be used as input for constructing the base modules.
    diff_files : list[ChangedFile]
        The list of changed files to consider when rebuilding base modules.
    base_rev : str
        The revision identifier of the base version to compare against.
    project_root : Path
        Root directory of the project, used to resolve relative paths for module records and changed files.
    configuration : ConfigurationDocmethis
        Configuration settings used to control the BASE module construction process.
    tmp_root : Path
        Temporary root directory used during base module construction.

    Returns
    -------
    tuple[list[ModuleRecord], list[str]]
        Constructs BASE modules by delegating to the underlying implementation while forwarding patched hooks for relative path
        and base content resolution. Returns a tuple containing the list of constructed ModuleRecord objects and a list of
        strings, typically warnings or error messages.

    """
    return _dia_base._build_base_modules(
        raw_records,
        diff_files,
        base_rev,
        project_root,
        configuration,
        tmp_root,
        relative_path_or_none=_relative_path_or_none,
        base_content_or_none=_base_content_or_none,
    )


_inconclusive = _dia_base._inconclusive
_apply_project_passes = _dia_base._apply_project_passes
_base_revision_available = _dia_base._base_revision_available
_base_file_status = _dia_base._base_file_status
_extract_module_in_tmp = _dia_base._extract_module_in_tmp

_effective_class_methods = _dia_context._effective_class_methods
_build_symbol_contexts = _dia_context._build_symbol_contexts
_function_context = _dia_context._function_context
_parent_documentation = _dia_context._parent_documentation
_type_implementation = _dia_context._type_implementation
_inert_body = _dia_context._inert_body
_is_ast_docstring = _dia_context._is_ast_docstring
_selected_symbol_visibility = _dia_context._selected_symbol_visibility
_method_origin_confidence = _dia_context._method_origin_confidence
_index_symbols = _dia_context._index_symbols

_extract_behavioral_keys = _dia_behavior._extract_behavioral_keys
_function_behavioral_keys = _dia_behavior._function_behavioral_keys
_fact_set = _dia_behavior._fact_set
_class_behavioral_keys = _dia_behavior._class_behavioral_keys
_exception_provenance = _dia_behavior._exception_provenance
_exception_confidence = _dia_behavior._exception_confidence
_behavioral_delta = _dia_behavior._behavioral_delta
_minimum_confidence = _dia_behavior._minimum_confidence
_exception_type = _dia_behavior._exception_type

_extract_declarative_signatures = _dia_api._extract_declarative_signatures
_api_diff = _dia_api._api_diff
_api_diff_visible = _dia_api._api_diff_visible
_change_to_dict = _dia_api._change_to_dict

_caller_index = _dia_propagation._caller_index
_affected_callers = _dia_propagation._affected_callers
_mark_affected_callers = _dia_propagation._mark_affected_callers

_impact_kind = _dia_emission._impact_kind
_local_exception_delta = _dia_emission._local_exception_delta
_exposed_exception = _dia_emission._exposed_exception
_handler_covers = _dia_emission._handler_covers
_delta_provenance = _dia_emission._delta_provenance
_propagated_exception = _dia_emission._propagated_exception
_documented_raise_types = _dia_emission._documented_raise_types
_documentation_status = _dia_emission._documentation_status
_effective_documentation = _dia_emission._effective_documentation
_local_target_documentation = _dia_emission._local_target_documentation
_unknown_documentation_context = _dia_emission._unknown_documentation_context
_confirmed_exception_removal = _dia_emission._confirmed_exception_removal
_resulting_severity = _dia_emission._resulting_severity
_evidence = _dia_emission._evidence
_build_entry = _dia_emission._build_entry
_observed_dia = _dia_emission._observed_dia
_dia_message = _dia_emission._dia_message

_attach_dia = _dia_report._attach_dia
_impact_to_dict = _dia_report._impact_to_dict
_symbol_context_to_dict = _dia_report._symbol_context_to_dict
_context_to_dict = _dia_report._context_to_dict


@contextmanager
def _legacy_dia_hooks() -> Iterator[None]:
    """Forward legacy ``dia`` helper monkeypatches to split phase modules.

    Returns
    -------
    Iterator[None]
        Temporarily forwards legacy ``dia`` helper monkeypatches to the split phase modules, yielding ``None`` while the patches
        are active and restoring the original helper functions when iteration completes.

    """
    old_hash = _dia_emission._compute_docstring_sha256
    old_simple = _dia_emission._simple_exception_name
    old_exception_type = _dia_emission._exception_type
    old_propagation_simple = _dia_propagation._simple_exception_name
    old_propagation_exception_type = _dia_propagation._exception_type
    _dia_emission._compute_docstring_sha256 = _compute_docstring_sha256
    _dia_emission._simple_exception_name = _simple_exception_name
    _dia_emission._exception_type = _exception_type
    _dia_propagation._simple_exception_name = _simple_exception_name
    _dia_propagation._exception_type = _exception_type
    try:
        yield
    finally:
        _dia_emission._compute_docstring_sha256 = old_hash
        _dia_emission._simple_exception_name = old_simple
        _dia_emission._exception_type = old_exception_type
        _dia_propagation._simple_exception_name = old_propagation_simple
        _dia_propagation._exception_type = old_propagation_exception_type


def _with_legacy_dia_hooks(function: Callable[..., object]) -> Callable[..., object]:
    """Wrap a re-exported helper so legacy DIA monkeypatches remain visible.

    Parameters
    ----------
    function : Callable[..., object]
        The callable to wrap with legacy DIA hooks.

    Returns
    -------
    Callable[..., object]
        Returns a wrapped version of the given callable that executes it within the legacy DIA hooks context and returns the
        original function's result.

    """

    @wraps(function)
    def wrapped(*args: object, **kwargs: object) -> object:
        with _legacy_dia_hooks():
            return function(*args, **kwargs)

    return wrapped


_exposed_exception = _with_legacy_dia_hooks(_dia_emission._exposed_exception)
_handler_covers = _with_legacy_dia_hooks(_dia_emission._handler_covers)
_documented_raise_types = _with_legacy_dia_hooks(_dia_emission._documented_raise_types)
_documentation_status = _with_legacy_dia_hooks(_dia_emission._documentation_status)
_confirmed_exception_removal = _with_legacy_dia_hooks(_dia_emission._confirmed_exception_removal)
_build_entry = _with_legacy_dia_hooks(_dia_emission._build_entry)
_observed_dia = _with_legacy_dia_hooks(_dia_emission._observed_dia)
_dia_message = _with_legacy_dia_hooks(_dia_emission._dia_message)


def _emit_dia_delta(emission: DiaEmissionContext, delta: DeltaBehavioral) -> EmissionDelta:
    """Emit a delta while honoring legacy monkeypatches on DIA helpers.

    Parameters
    ----------
    emission : DiaEmissionContext
        The DIA emission context that supplies the configuration and state needed to emit the delta.
    delta : DeltaBehavioral
        The behavioral delta to emit, carrying the changes to be applied through the emission pipeline.

    Returns
    -------
    EmissionDelta
        The emitted delta resulting from the emission operation.

    """
    with _legacy_dia_hooks():
        return _dia_emission._emit_dia_delta(emission, delta)


def _propagation_path(  # noqa: PLR0913, PLR0917
    callgraph: dict[str, list[CallgraphEdge]],
    symbol: str,
    causes: set[str],
    exception_type: str,
    exception_types: dict[str, set[str]],
    depth: int = 2,
) -> list[str]:
    """Return a propagation path while honoring the legacy exception-name hook.

    Parameters
    ----------
    callgraph : dict[str, list[CallgraphEdge]]
        Mapping from a function or call-site identifier to a list of CallgraphEdge instances describing the outgoing calls from
        that node.
    symbol : str
        The symbol whose propagation path is to be computed.
    causes : set[str]
        A set of exception names that define the causes to consider when computing the propagation path.
    exception_type : str
        The exception type name to use when computing the propagation path.
    exception_types : dict[str, set[str]]
        Maps exception type names to the set of legacy exception names associated with each type, used when resolving the legacy
        exception-name hook during propagation path construction.
    depth : int = 2
        Maximum depth of the propagation path to compute; defaults to 2.

    Returns
    -------
    list[str]
        The propagation path as a list of strings, computed while honoring the legacy exception-name hook.

    """
    with _legacy_dia_hooks():
        return _dia_propagation._propagation_path(callgraph, symbol, causes, exception_type, exception_types, depth)


def _analyze_documentation_impacts(  # noqa: PLR0913, PLR0917
    project_head: ProjectRecord,
    raw_records: list[ModuleRecord],
    diff_files: list[ChangedFile],
    base_rev: str | None,
    project_root: Path,
    check_config: CheckConfig,
) -> DiaResult:
    """Run DIA phases and produce the ``impact_analysis`` block.

    Parameters
    ----------
    project_head : ProjectRecord
        The project record representing the head version of the project to be analyzed.
    raw_records : list[ModuleRecord]
        A list of module records representing the raw documentation modules for the project head.
    diff_files : list[ChangedFile]
        Files changed between the base and head revisions, used to identify which modified files may explain or localize detected
        behavioral impacts.
    base_rev : str | None
        The base revision to use for comparison, or None if no base revision is available.
    project_root : Path
        The root directory of the project being analyzed, used to locate configuration files and resolve paths.
    check_config : CheckConfig
        Configuration object that controls DIA analysis behavior, including which behavioral keys and declarative signatures are
        extracted from the module records.

    Returns
    -------
    DiaResult
        Run DIA phases and produce the impact_analysis block, returning a DiaResult containing the computed impacts, API diff, and
        check entries.

    """
    configuration_m1 = load_configuration(project_root)

    with _base_head_models(
        project_head,
        raw_records,
        diff_files,
        base_rev,
        project_root,
        configuration_m1,
    ) as models:
        if models.completeness == "inconclusive":
            return DiaResult(
                impact_analysis=ImpactAnalysis(completeness="inconclusive", reason=models.reason),
            )

        base = models.base
        base_contexts = _build_symbol_contexts(base.modules)
        head_contexts = _build_symbol_contexts(project_head.modules)
        base_keys = _extract_behavioral_keys(base.modules, check_config, symbol_contexts=base_contexts)
        head_keys = _extract_behavioral_keys(
            project_head.modules,
            check_config,
            symbol_contexts=head_contexts,
        )
        deltas = _behavioral_delta(base_keys, head_keys)
        head_index = _index_symbols(project_head)
        diff_paths = {changed_file.path.resolve() for changed_file in diff_files}
        dia_filters = check_config.exclude_paths + check_config.dia_exclude_paths

        callgraph_base = build_callgraph(base)
        callgraph_head = build_callgraph(project_head)

        exception_symbols = {
            delta.symbol
            for delta in deltas
            if delta.field == "exception"
            and (record := head_index.get(delta.symbol)) is not None
            and record.file_path.resolve() in diff_paths
            and not path_matches_filters(record.file_path, project_root, dia_filters)
        }
        affected_callers = _affected_callers(callgraph_base, callgraph_head, exception_symbols)
        deltas = _mark_affected_callers(deltas, affected_callers)

        cause_symbols = {
            symbol
            for symbol, record in head_index.items()
            if symbol in exception_symbols and record.file_path.resolve() in diff_paths
        }

        emission = DiaEmissionContext(
            configuration=check_config,
            records_head=head_index,
            records_base=_index_symbols(base),
            completeness=models.completeness,
            project_root=project_root,
        )

        exception_types = {
            fn.qualified_name: {_simple_exception_name(exc.exception_type) for exc in fn.exceptions or []}
            for fn in iter_functions(project_head.modules)
        }

        entries: list[CheckEntry] = []
        impacts: list[dict[str, object]] = []
        for delta in deltas:
            record_head = head_index.get(delta.symbol)
            if record_head is None:
                continue
            if path_matches_filters(record_head.file_path, project_root, dia_filters):
                continue
            emission_result = _emit_dia_delta(emission, delta)
            if emission_result.impact is not None:
                impact = emission_result.impact
                if impact.provenance == "propagated":
                    propagation_path = _propagation_path(
                        callgraph_head,
                        impact.symbol,
                        cause_symbols,
                        impact.value,
                        exception_types,
                    )
                    impact = replace(
                        impact,
                        path=tuple(propagation_path),
                        evidence={**impact.evidence, "path": list(propagation_path)},
                    )
                    if emission_result.meta is not None:
                        emission_result = replace(
                            emission_result,
                            meta=replace(
                                emission_result.meta, evidence={**emission_result.meta.evidence, "path": propagation_path}
                            ),
                        )
                impacts.append(_impact_to_dict(impact))
            if emission_result.entry is not None and emission_result.meta is not None:
                status = emission_result.impact.status if emission_result.impact is not None else ""
                entries.append(_attach_dia(emission_result.entry, emission_result.meta, status))

        base_signatures = _extract_declarative_signatures(base.modules, check_config)
        head_signatures = _extract_declarative_signatures(project_head.modules, check_config)
        visible_api_diff = _api_diff_visible(
            project_head,
            diff_files,
            impacts,
            _api_diff(base_signatures, head_signatures),
        )

        return DiaResult(
            impact_analysis=ImpactAnalysis(
                completeness=models.completeness,
                reason=models.reason,
                impacts=impacts,
                api_diff=visible_api_diff,
            ),
            entries=entries,
        )
