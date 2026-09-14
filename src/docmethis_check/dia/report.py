# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Serialization helpers for DIA report data."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docmethis_check.models import CheckEntry

    from .models import DocumentationImpact, MetaDia, SymbolContext


def _attach_dia(entry: CheckEntry, meta: MetaDia, status: str) -> CheckEntry:
    """Attach the serializable ``dia`` block to a DIA entry.

    Parameters
    ----------
    entry : CheckEntry
        The DIA entry to which the serializable dia block will be attached.
    meta : MetaDia
        The MetaDia instance whose attributes are used to build the serializable dia block.
    status : str
        The status string to attach to the DIA entry's data. When this parameter is a non-empty string, it is stored under the
        'status' key; otherwise the status key is omitted.

    Returns
    -------
    CheckEntry
        Returns a new CheckEntry with the serializable dia block attached to it.

    """
    data: dict[str, object] = {
        "impact_kind": meta.impact_kind,
        "code": meta.code,
        "value": meta.value,
        "direction": {"ajouté": "added", "retiré": "removed"}.get(meta.direction, meta.direction),
        "confidence": meta.confidence.value,
        "provenance": meta.provenance,
        "impact_completeness": meta.impact_completeness,
        "fixable": meta.fixable,
        "evidence": meta.evidence,
        "symbol_context": _symbol_context_to_dict(meta.base_context, meta.head_context),
    }
    if status:
        data["status"] = status
    return replace(entry, dia=data)


def _impact_to_dict(impact: DocumentationImpact) -> dict[str, object]:
    """Serialize a documentation impact for ``impact_analysis.impacts``.

    Parameters
    ----------
    impact : DocumentationImpact
        The documentation impact to serialize.

    Returns
    -------
    dict[str, object]
        Serialize a DocumentationImpact object into a dictionary representation for impact analysis reports, including all core
        impact fields and any optional fields that are present.

    """
    data: dict[str, object] = {
        "symbol": impact.symbol,
        "impact_kind": impact.impact_kind,
        "value": impact.value,
        "direction": {"ajouté": "added", "retiré": "removed"}.get(impact.direction, impact.direction),
        "status": impact.status,
        "confidence": impact.confidence.value,
        "provenance": impact.provenance,
        "impact_completeness": impact.impact_completeness,
        "fixable": impact.fixable,
        "symbol_context": _symbol_context_to_dict(impact.base_context, impact.head_context),
    }
    if impact.file is not None:
        data["file"] = impact.file
    if impact.line_start is not None:
        data["line_start"] = impact.line_start
    if impact.symbol_kind is not None:
        data["symbol_kind"] = impact.symbol_kind
    if impact.docstring_sha256 is not None:
        data["docstring_sha256"] = impact.docstring_sha256
    if impact.evidence:
        data["evidence"] = impact.evidence
    if impact.code is not None:
        data["code"] = impact.code
    if impact.path:
        data["path"] = list(impact.path)
    return data


def _symbol_context_to_dict(
    base_context: SymbolContext | None,
    head_context: SymbolContext | None,
) -> dict[str, dict[str, object]] | None:
    """Serialize normalized BASE and HEAD contexts.

    Parameters
    ----------
    base_context : SymbolContext | None
        The base-side symbol context to serialize; may be None when there is no base context to include.
    head_context : SymbolContext | None
        The normalized SymbolContext for the HEAD side to serialize. Pass None when there is no HEAD context.

    Returns
    -------
    dict[str, dict[str, object]] | None
        Returns a dictionary with the serialized base and head contexts stored under the keys 'base' and 'head', respectively, or
        None if both input contexts are None.

    """
    if base_context is None and head_context is None:
        return None
    return {
        "base": _context_to_dict(base_context),
        "head": _context_to_dict(head_context),
    }


def _context_to_dict(context: SymbolContext | None) -> dict[str, object]:
    """Serialize a context with explicit values for missing resolution.

    Parameters
    ----------
    context : SymbolContext | None
        The symbol context to serialize, or None to represent a missing resolution.

    Returns
    -------
    dict[str, object]
        Returns a dictionary with keys 'documentation_origin', 'dispatch_role', 'implementation_kind',
        'inherited_contract_source', and 'method_origin' representing the serialized context. If the input context is None, the
        dictionary contains explicit default values for missing resolution.

    """
    if context is None:
        return {
            "documentation_origin": "unknown",
            "dispatch_role": "regular",
            "implementation_kind": "concrete",
            "inherited_contract_source": None,
            "method_origin": None,
        }
    return {
        "documentation_origin": context.documentation_origin,
        "dispatch_role": context.dispatch_role,
        "implementation_kind": context.implementation_kind,
        "inherited_contract_source": context.inherited_contract_source,
        "method_origin": context.method_origin.value if context.method_origin is not None else None,
    }
