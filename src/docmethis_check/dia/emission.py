# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""DIA impact classification and emission."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docmethis_extract_python.api import ClassRecord, Confidence, FunctionRecord, Provenance
from docmethis_verify.dmt import DIA_CODE_BY_IMPACT_KIND
from docmethis_verify.docstring.numpy_parser import parse_numpy_docstring
from docmethis_verify.docstring.section_parser import find_section, parse_raises

from docmethis_check.diagnostics import DiagnosticKey, _compute_docstring_sha256, _relative_path_or_none, _simple_exception_name
from docmethis_check.models import CheckEntry, Severity, SymbolKind

from .behavior import _EXCEPTION_ROOTS, _exception_type
from .models import (
    DeltaBehavioral,
    DiaEmissionContext,
    DocumentationImpact,
    EmissionDelta,
    MetaDia,
    SymbolContext,
)

if TYPE_CHECKING:
    from pathlib import Path

    from docmethis_check.config import CheckConfig

logger = logging.getLogger(__name__)

# Default behavioral severities for DIA impact kinds.
_DEFAULT_SEVERITIES: dict[str, Severity] = {
    "exposed_exception": "error",
    "removed_exception": "warning",
    "io_effect": "warning",
    "observable_property": "warning",
    "override": "warning",
    "heritage": "warning",
}

# Fix support is independent of the confirmed/observed/potential status.
_DEFAULT_FIX_SUPPORT: dict[str, bool] = {
    "exposed_exception": True,
    "removed_exception": True,
    "io_effect": True,
    "observable_property": True,
    "override": True,
    "heritage": True,
}


def _emit_dia_delta(  # noqa: PLR0911
    emission: DiaEmissionContext,
    delta: DeltaBehavioral,
) -> EmissionDelta:
    """Emit one behavioral delta according to the DIA state model.

    Parameters
    ----------
    emission : DiaEmissionContext
        The DIA emission context holding base and head records, completeness, and configuration used to emit the delta.
    delta : DeltaBehavioral
        The behavioral delta to be processed and emitted, containing the symbol, value, direction, confidence, completeness,
        provenance, and base/head contexts for the DIA state model.

    Returns
    -------
    EmissionDelta
        An EmissionDelta representing the emitted DIA behavioral delta, including metadata and documentation impact, and
        optionally a generated entry when the delta is mapped and enabled.

    """
    record_head = emission.records_head.get(delta.symbol)
    if record_head is None:
        return EmissionDelta()

    impact_kind = _impact_kind(delta)
    code = DIA_CODE_BY_IMPACT_KIND.get(impact_kind) if impact_kind is not None else None

    if _local_exception_delta(delta) and not _exposed_exception(record_head, _exception_type(delta.value)):
        return EmissionDelta()

    record_base = emission.records_base.get(delta.symbol)
    status = _documentation_status(
        delta,
        record_head,
        record_base,
        impact_kind,
        analysis_completeness=emission.completeness,
    )

    provenance = _delta_provenance(delta)
    local_documentation = _local_target_documentation(record_head, delta.head_context)
    fixable = (
        _DEFAULT_FIX_SUPPORT.get(impact_kind, False) and local_documentation is not None if impact_kind is not None else False
    )
    confidence = delta.confidence
    completeness = delta.impact_completeness
    file = _report_file(record_head.file_path, emission.project_root)

    evidence = _evidence(delta)
    meta = MetaDia(
        impact_kind=impact_kind,
        code=code,
        value=delta.value,
        direction=delta.direction,
        confidence=confidence,
        provenance=provenance,
        impact_completeness=completeness,
        fixable=fixable,
        evidence=evidence,
        base_context=delta.base_context,
        head_context=delta.head_context,
    )
    impact = DocumentationImpact(
        symbol=delta.symbol,
        impact_kind=impact_kind,
        value=delta.value,
        direction=delta.direction,
        status=status,
        confidence=confidence,
        provenance=provenance,
        impact_completeness=completeness,
        fixable=fixable,
        code=code,
        base_context=delta.base_context,
        head_context=delta.head_context,
        file=file,
        line_start=record_head.line_start,
        symbol_kind=(
            "class" if isinstance(record_head, ClassRecord) else "method" if record_head.parent_class is not None else "function"
        ),
        docstring_sha256=(_compute_docstring_sha256(local_documentation) if local_documentation is not None else None),
        evidence=evidence,
    )

    if status == "treated":
        return EmissionDelta(meta=meta, impact=impact)
    if status == "changed_unverified":
        return EmissionDelta(meta=meta, impact=impact)

    if code is None:
        logger.warning("Unmapped DIA impact_kind: %s", delta.field)
        return EmissionDelta(meta=meta, impact=impact)

    severity = _resulting_severity(impact_kind, code, provenance, delta.direction, emission.configuration)
    if severity == "disabled":
        return EmissionDelta(meta=meta, impact=impact)

    entry = _build_entry(delta, record_head, code, status, severity, file=file)
    return EmissionDelta(entry=entry, meta=meta, impact=impact)


def _impact_kind(delta: DeltaBehavioral) -> str | None:  # noqa: PLR0911
    """Return the internal impact kind, or ``None`` for an unknown field.

    Parameters
    ----------
    delta : DeltaBehavioral
        The behavioral delta to classify into an impact kind.

    Returns
    -------
    str | None
        The internal impact kind as a string, or None if the field is not recognized.

    """
    if delta.field == "exception":
        if delta.direction == "retiré":
            return "removed_exception"
        return "exposed_exception"
    if delta.field == "io":
        return "io_effect"
    if delta.field == "observable":
        return "observable_property"
    if delta.field == "method_origin":
        return "override"
    if delta.field == "heritage":
        return "heritage"
    return None


def _local_exception_delta(delta: DeltaBehavioral) -> bool:
    """Return whether the delta adds a locally raised exception in head.

    Parameters
    ----------
    delta : DeltaBehavioral
        The DeltaBehavioral object representing the change to inspect for a locally raised exception addition.

    Returns
    -------
    bool
        True if the delta adds a locally raised exception in head; False otherwise.

    """
    return delta.field == "exception" and delta.value.endswith(":local") and delta.direction == "ajouté"


def _exposed_exception(
    record: FunctionRecord | ClassRecord,
    type_exception: str,
    *,
    include_propagated: bool = False,
) -> bool:
    """Return whether a raise of ``type_exception`` escapes the symbol.

    Parameters
    ----------
    record : FunctionRecord | ClassRecord
        The function or class record whose exception data is examined to determine whether a raise of the specified exception type
        escapes.
    type_exception : str
        The exception type name to check for exposure.
    include_propagated : bool = False
        If True, consider exceptions propagated from called functions when checking whether a raise of type_exception escapes the
        symbol. If False (the default), ignore propagated exceptions.

    Returns
    -------
    bool
        True if a raise of the given exception type escapes the symbol, meaning the exception is raised and not caught by any
        matching except block; False otherwise. When include_propagated is False, exceptions propagated from called functions are
        ignored.

    """
    handlers = getattr(record, "except_blocks", None) or []
    target = _simple_exception_name(type_exception)
    for exc in getattr(record, "exceptions", None) or []:
        if not include_propagated and exc.provenance is Provenance.CALLGRAPH_PROPAGATION:
            continue
        if _simple_exception_name(exc.exception_type) != target:
            continue
        if any(_handler_covers(handler, exc.raise_line, target) for handler in handlers):
            continue
        return True
    return False


def _handler_covers(handler: object, raise_line: int, target: str) -> bool:
    """Return whether a handler absorbs a raise.

    Parameters
    ----------
    handler : object
        The handler object to inspect for whether it absorbs the raise.
    raise_line : int
        The line number at which the raise occurs, used to determine whether the handler's try block covers that line.
    target : str
        The exception type name to check against the handler's caught types.

    Returns
    -------
    bool
        True if the handler absorbs the raise; False otherwise.

    """
    if handler.is_reraised:
        return False
    if not (handler.try_start <= raise_line <= handler.try_end):
        return False
    if not handler.caught_types:
        return True
    return any(
        _simple_exception_name(name) == target or _simple_exception_name(name) in _EXCEPTION_ROOTS
        for name in handler.caught_types
    )


def _delta_provenance(delta: DeltaBehavioral) -> str:
    """Return provenance derived from the behavioral fact.

    Parameters
    ----------
    delta : DeltaBehavioral
        The behavioral fact whose provenance is to be determined.

    Returns
    -------
    str
        A string indicating whether the provenance of the behavioral fact is 'propagated' or 'local'.

    """
    if delta.field == "exception":
        return "propagated" if _propagated_exception(delta) else "local"
    return "propagated" if delta.provenance == "propagated" else "local"


def _propagated_exception(delta: DeltaBehavioral) -> bool:
    """Return whether an exception delta is propagated.

    Parameters
    ----------
    delta : DeltaBehavioral
        The exception delta whose propagation status is to be determined.

    Returns
    -------
    bool
        True if the exception delta is propagated, False otherwise.

    """
    return delta.value.endswith(":propagated") or delta.provenance == "propagated"


def _documented_raise_types(
    record: FunctionRecord | ClassRecord,
    documentation: str | None = None,
) -> set[str]:
    """Return exception types documented in the Raises section.

    Parameters
    ----------
    record : FunctionRecord | ClassRecord
        The function or class record whose existing docstring is inspected for documented raise types.
    documentation : str | None = None
        Optional documentation string to inspect for Raises sections. Defaults to the record's existing docstring when not
        provided.

    Returns
    -------
    set[str]
        A set of exception type names extracted from the Raises section of the documentation.

    """
    docstring = record.existing_docstring if documentation is None else documentation
    if not docstring:
        return set()
    parsed = parse_numpy_docstring(docstring)
    return {_simple_exception_name(raise_.exception) for raise_ in parse_raises(find_section(parsed, "Raises"))}


def _documentation_status(  # noqa: C901, PLR0911
    delta: DeltaBehavioral,
    record_head: FunctionRecord | ClassRecord,
    record_base: FunctionRecord | ClassRecord | None,
    impact_kind: str,
    *,
    analysis_completeness: str = "complete",
) -> str:
    """Return the delta's documentation status.

    Parameters
    ----------
    delta : DeltaBehavioral
        The behavioral delta whose documentation status is being assessed.
    record_head : FunctionRecord | ClassRecord
        The record whose documentation is compared against the base record to determine the delta's documentation status.
    record_base : FunctionRecord | ClassRecord | None
        The base version of the function or class record against which the head record is compared; may be None when no base
        record exists.
    impact_kind : str
        Kind of behavioral impact represented by the delta, used to determine which documentation-status logic applies.
    analysis_completeness : str = 'complete'
        Determines the completeness level of the analysis used when verifying exception removal; the default is 'complete'.

    Returns
    -------
    str
        Returns a string describing the documentation status of the delta. The status is one of 'documentation_unchanged' when the
        effective docstring content is unchanged, 'changed_unverified' when the documentation context is unknown or the docstring
        changed without verification, 'treated' when the documentation already reflects the exception impact, or 'contradiction'
        when the unchanged documentation conflicts with the detected exception impact.

    """
    documentation_head = _effective_documentation(record_head, delta.head_context)
    documentation_base = (
        _effective_documentation(record_base, delta.base_context) if record_base is not None else documentation_head
    )
    sha_head = _compute_docstring_sha256(documentation_head or "")
    sha_base = _compute_docstring_sha256(documentation_base or "")
    docstring_unchanged = sha_head == sha_base

    if _unknown_documentation_context(delta):
        return "changed_unverified"

    if impact_kind in {"exposed_exception", "removed_exception"}:
        documented_types = _documented_raise_types(record_head, documentation_head)
        target_type = _simple_exception_name(_exception_type(delta.value))
        documented = target_type in documented_types

        if impact_kind == "exposed_exception":
            if documented:
                return "treated"
            if docstring_unchanged:
                if _propagated_exception(delta):
                    return "documentation_unchanged"
                return "contradiction"
            return "changed_unverified"

        if documented:
            if docstring_unchanged:
                if _confirmed_exception_removal(
                    delta=delta,
                    record_head=record_head,
                    record_base=record_base,
                    analysis_completeness=analysis_completeness,
                ):
                    return "contradiction"
                return "documentation_unchanged"
            return "changed_unverified"
        return "treated"

    if docstring_unchanged:
        return "documentation_unchanged"
    return "changed_unverified"


def _effective_documentation(
    record: FunctionRecord | ClassRecord | None,
    context: SymbolContext | None,
) -> str | None:
    """Return a view's effective docstring, inherited when needed.

    Parameters
    ----------
    record : FunctionRecord | ClassRecord | None
        The record whose effective docstring is being resolved. If None, no documentation is available and the function returns
        None. Accepts either a FunctionRecord or ClassRecord.
    context : SymbolContext | None
        Optional symbol context used to determine whether documentation should be inherited; when it is None or its documentation
        origin is unknown, the record's existing docstring is used instead.

    Returns
    -------
    str | None
        The effective docstring as a string, or None when no record is provided or no docstring is available.

    """
    if record is None:
        return None
    if context is None or context.documentation_origin == "unknown":
        return record.existing_docstring
    return context.documentation


def _local_target_documentation(
    record: FunctionRecord | ClassRecord,
    context: SymbolContext | None,
) -> str | None:
    """Return only local documentation editable by Fix.

    Parameters
    ----------
    record : FunctionRecord | ClassRecord
        The function or class record whose local documentation should be returned.
    context : SymbolContext | None
        Optional symbol context used to determine whether the documentation origin is 'own'; if provided and the origin is not
        'own', the function returns None.

    Returns
    -------
    str | None
        Return the existing docstring for the given record only when it is locally editable by Fix, or None otherwise.

    """
    if context is not None and context.documentation_origin != "own":
        return None
    return record.existing_docstring if isinstance(record.existing_docstring, str) else None


def _unknown_documentation_context(delta: DeltaBehavioral) -> bool:
    """Return whether either view has an unknown documentation origin.

    Parameters
    ----------
    delta : DeltaBehavioral
        The DeltaBehavioral object whose base and head contexts are checked for an unknown documentation origin.

    Returns
    -------
    bool
        Return True if either the base context or the head context has a documentation origin of 'unknown'; otherwise return
        False.

    """
    return any(
        context is not None and context.documentation_origin == "unknown" for context in (delta.base_context, delta.head_context)
    )


def _confirmed_exception_removal(
    *,
    delta: DeltaBehavioral,
    record_head: FunctionRecord | ClassRecord,
    record_base: FunctionRecord | ClassRecord | None,
    analysis_completeness: str,
) -> bool:
    """Confirm complete exception removal before allowing an automatic fix.

    Parameters
    ----------
    delta : DeltaBehavioral
        Behavioral change record describing the exception-related modification to be validated for complete removal.
    record_head : FunctionRecord | ClassRecord
        The record representing the new or changed version of the callable whose exception removal is being confirmed. It is used
        to verify that the exception type is no longer exposed by the updated implementation.
    record_base : FunctionRecord | ClassRecord | None
        The base version of the record to compare against, used to confirm that the exception was originally exposed; may be None
        when no base record is available.
    analysis_completeness : str
        Indicates whether the analysis performed is complete. Must be the string 'complete' for the confirmation to proceed; any
        other value causes the function to return False.

    Returns
    -------
    bool
        Confirm that an exception removal is complete and safe to apply automatically. Returns True only when the analysis and
        impact are complete, the delta is an explicit local removal, the base record explicitly exposes the exception, and the
        head record no longer exposes it; otherwise returns False.

    """
    if analysis_completeness != "complete" or delta.impact_completeness != "complete":
        return False
    if delta.direction != "retiré" or delta.confidence is not Confidence.EXPLICIT or not delta.value.endswith(":local"):
        return False
    if record_base is None or getattr(record_base, "exceptions_confidence", Confidence.ABSENT) is not Confidence.EXPLICIT:
        return False
    if getattr(record_head, "exceptions_confidence", Confidence.ABSENT) is Confidence.ABSENT:
        return False

    exception_type = _exception_type(delta.value)
    return _exposed_exception(record_base, exception_type) and not _exposed_exception(
        record_head,
        exception_type,
        include_propagated=True,
    )


def _resulting_severity(
    impact_kind: str,
    code: str,
    provenance: str,
    direction: str,
    configuration: CheckConfig,
) -> Severity:
    """Return the configured severity after provenance downgrade.

    Parameters
    ----------
    impact_kind : str
        A string that identifies the kind of impact, used to determine the default severity when no specific configuration exists
        for the given code.
    code : str
        The code identifying the diagnostic check for which the configured severity is retrieved.
    provenance : str
        The provenance of the finding, as a string. When this value is 'propagated', the resulting severity is downgraded to
        'warning'.
    direction : str
        The direction of the emission, used to decide whether the severity should be downgraded; when set to 'retiré', the
        function always returns 'warning'.
    configuration : CheckConfig
        The CheckConfig instance providing the severity configuration for the check, used to resolve the severity for the given
        code.

    Returns
    -------
    Severity
        Returns the configured severity, downgraded to 'warning' when provenance is 'propagated' or direction is 'retiré', and
        'disabled' if the configured severity is 'disabled'.

    """
    default = _DEFAULT_SEVERITIES.get(impact_kind, "warning")
    severity = configuration.severity_for(code, default)
    if severity == "disabled":
        return "disabled"
    if provenance == "propagated" or direction == "retiré":
        return "warning"
    return severity


def _evidence(delta: DeltaBehavioral) -> dict[str, object]:
    """Return the delta's before/after behavioral fact.

    Parameters
    ----------
    delta : DeltaBehavioral
        The behavioral delta whose direction and value determine the before/after fact.

    Returns
    -------
    dict[str, object]
        A dictionary with the keys 'before' and 'after', mapping to the delta's prior and current behavioral values. Exactly one
        of the two values is None depending on whether the delta was added or removed.

    """
    if delta.direction == "ajouté":
        return {"before": None, "after": delta.value}
    return {"before": delta.value, "after": None}


def _build_entry(  # noqa: PLR0913
    delta: DeltaBehavioral,
    record_head: FunctionRecord | ClassRecord,
    code: str,
    status: str,
    severity: Severity,
    *,
    file: str | None = None,
) -> CheckEntry:
    """Build the DIA ``CheckEntry``.

    Parameters
    ----------
    delta : DeltaBehavioral
        The DeltaBehavioral instance describing the behavioral change to process.
    record_head : FunctionRecord | ClassRecord
        The function or class record that serves as the head of the documentation entry, providing file path, line numbers, and
        symbol kind information.
    code : str
        Diagnostic code identifying the rule or check that produced this entry.
    status : str
        Status string indicating the DIA state for the entry; it is used to build the diagnostic message and to derive the public
        direction label.
    severity : Severity
        Severity assigned to the emitted check entry, indicating the importance of the diagnostic result.
    file : str | None = None
        Project-relative report path when the emission context provides a project root.

    Returns
    -------
    CheckEntry
        A CheckEntry instance constructed from the supplied delta, record head, code, status, and severity, containing the
        diagnostic message, location, symbol kind, expected/observed values, and related metadata.

    """
    target_file = file if file is not None else str(record_head.file_path)
    message = _dia_message(delta, status)
    public_direction = {"ajouté": "added", "retiré": "removed"}.get(delta.direction, delta.direction)
    accessor = getattr(getattr(record_head, "property_accessor", None), "value", None)
    visibility = getattr(getattr(record_head, "visibility", None), "value", None)
    key = DiagnosticKey(
        code=code,
        file=target_file,
        symbol=delta.symbol,
        section=delta.field,
        expected=delta.value,
        accessor_kind=accessor,
        visibility=visibility,
    )
    return CheckEntry(
        severity=severity,
        code=code,
        symbol=delta.symbol,
        file=target_file,
        line_start=record_head.line_start,
        col_start=1,
        message=message,
        symbol_kind=SymbolKind.CLASS if getattr(record_head, "methods", None) is not None else SymbolKind.FUNCTION,
        accessor_kind=accessor,
        visibility=visibility,
        line_end=getattr(record_head, "line_end", None),
        col_end=getattr(record_head, "col_end", None),
        docstring_line=record_head.docstring_line_start,
        annotation_line=record_head.line_start,
        section=delta.field,
        expected=f"{public_direction} : {delta.value}",
        observed=_observed_dia(delta),
        docstring_sha256=(
            _compute_docstring_sha256(local_documentation)
            if (local_documentation := _local_target_documentation(record_head, delta.head_context)) is not None
            else None
        ),
        diagnostic_key=key,
    )


def _report_file(file: Path, project_root: Path | None) -> str:
    """Return the project-relative report path when a project root is known."""
    if project_root is None:
        return str(file)
    return _relative_path_or_none(file, project_root) or str(file)


def _observed_dia(delta: DeltaBehavioral) -> str:
    """Return the observed fact for a DIA delta.

    Parameters
    ----------
    delta : DeltaBehavioral
        The DIA behavioral delta to convert into an observed fact.

    Returns
    -------
    str
        A string describing the observed fact. If the delta's field is 'exception', the string is formatted as 'raises <exception
        type>'; otherwise, it is the delta's value.

    """
    if delta.field == "exception":
        return f"raises {_exception_type(delta.value)}"
    return delta.value


def _dia_message(delta: DeltaBehavioral, status: str) -> str:
    """Return the neutral English message for a DIA entry.

    Parameters
    ----------
    delta : DeltaBehavioral
        The behavioral delta representing the DIA entry to be described in the message.
    status : str
        Status indicating the kind of DIA entry; when 'contradiction', the message describes an exception absent from the Raises
        section, otherwise it describes an observable contract change.

    Returns
    -------
    str
        A string containing the neutral English message for the DIA entry.

    """
    if status == "contradiction":
        return f"Exception {_exception_type(delta.value)} is raised but absent from the Raises section."
    return f"Observable contract change; documentation unchanged ({_observed_dia(delta)})."
