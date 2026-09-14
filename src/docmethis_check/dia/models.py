# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Typed models shared by the DIA phases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from docmethis_extract_python.api import (
    ClassRecord,
    Confidence,
    FunctionRecord,
    MethodOrigin,
    ParameterType,
    ProjectRecord,
)

if TYPE_CHECKING:
    from pathlib import Path

    from docmethis_check.config import CheckConfig
    from docmethis_check.models import CheckEntry, ImpactAnalysis


type DocumentationOrigin = Literal["own", "inherited", "none", "unknown"]
type DispatchRole = Literal["regular", "override", "overload"]
type ImplementationKind = Literal["concrete", "abstract", "stub"]


@dataclass(frozen=True, slots=True)
class SymbolContext:
    """Documentation and implementation context for an effective C10 view.

    Attributes
    ----------
    symbol : str
        Qualified name of the symbol.
    implementation : FunctionRecord | None
        Implementation record of the symbol, when local.
    owner_class : str | None
        Qualified name of the owning class, for methods.
    documentation : str | None
        Effective documentation text of the symbol, when present.
    documentation_origin : DocumentationOrigin
        Origin of the effective documentation.
    dispatch_role : DispatchRole
        Dispatch role of the symbol.
    implementation_kind : ImplementationKind
        Kind of the effective implementation.
    inherited_contract_source : str | None
        Qualified name of the symbol the contract is inherited from, when inherited.
    method_origin : MethodOrigin | None
        Origin of the method, when known.
    method_origin_confidence : Confidence
        Confidence of the method origin detection.

    """

    symbol: str
    implementation: FunctionRecord | None = field(compare=False, hash=False, repr=False)
    owner_class: str | None
    documentation: str | None
    documentation_origin: DocumentationOrigin
    dispatch_role: DispatchRole
    implementation_kind: ImplementationKind
    inherited_contract_source: str | None = None
    method_origin: MethodOrigin | None = None
    method_origin_confidence: Confidence = Confidence.ABSENT


@dataclass(frozen=True, slots=True)
class BaseHeadModels:
    """Symmetric BASE and HEAD behavioral models (DIA, Phase 1).

    Attributes
    ----------
    base : ProjectRecord
        Project record of the BASE revision.
    completeness : str
        Completeness indicator of the BASE model.
    reason : str | None
        Reason for a partial or missing BASE model, when relevant.

    """

    base: ProjectRecord
    completeness: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class BehavioralKey:
    """Canonical behavioral key for a symbol, without line numbers.

    Attributes
    ----------
    field : str
        Behavioral field of the key.
    value : str
        Canonical value of the behavioral field.
    confidence : Confidence
        Confidence of the extracted value.
    symbol_context : SymbolContext | None
        Symbol context the key belongs to, when available.

    """

    field: str
    value: str
    confidence: Confidence
    symbol_context: SymbolContext | None = None


@dataclass(frozen=True, slots=True)
class DeltaBehavioral:
    """Behavioral delta added or removed in head relative to base.

    Attributes
    ----------
    symbol : str
        Qualified name of the affected symbol.
    field : str
        Behavioral field that changed.
    value : str
        Canonical value of the changed field.
    direction : str
        Direction of the change: added or removed.
    confidence : Confidence
        Confidence of the detected change.
    provenance : str
        Provenance of the change detection.
    impact_completeness : str
        Completeness indicator of the impact assessment.
    base_context : SymbolContext | None
        Symbol context at BASE, when available.
    head_context : SymbolContext | None
        Symbol context at HEAD, when available.

    """

    symbol: str
    field: str
    value: str
    direction: str
    confidence: Confidence
    provenance: str = "local"
    impact_completeness: str = "complete"
    base_context: SymbolContext | None = None
    head_context: SymbolContext | None = None


@dataclass(frozen=True, slots=True)
class DeclarativeParameter:
    """Minimal declarative parameter for the informational API diff.

    Attributes
    ----------
    name : str
        Parameter name.
    default : str
        String representation of the parameter default.
    kind : ParameterType
        Kind of the parameter.

    """

    name: str
    default: str
    kind: ParameterType


@dataclass(frozen=True, slots=True)
class DeclarativeSignature:
    """Canonical declarative signature for ``api_diff`` (informational).

    Attributes
    ----------
    parameters : frozenset[DeclarativeParameter]
        Declarative parameters of the signature.
    return_type : str
        String representation of the return type.
    is_async : bool
        Whether the symbol is an async function.

    """

    parameters: frozenset[DeclarativeParameter]
    return_type: str
    is_async: bool = False


@dataclass(frozen=True, slots=True)
class DeclarativeChange:
    """Declarative signature change - informational only.

    Attributes
    ----------
    symbol : str
        Qualified name of the changed symbol.
    aspect : str
        Changed signature aspect.
    before : str | None
        Previous value of the aspect, when present.
    after : str | None
        New value of the aspect, when present.
    parameter_symbol : str | None
        Affected parameter symbol, when the change is parameter-specific.

    """

    symbol: str
    aspect: str
    before: str | None
    after: str | None
    parameter_symbol: str | None = None


@dataclass(frozen=True, slots=True)
class MetaDia:
    """``dia`` block attached to a DIA CheckEntry.

    Attributes
    ----------
    impact_kind : str | None
        Kind of the documentation impact, when known.
    code : str | None
        DMT code of the impact, when known.
    value : str
        Canonical value of the impacted behavioral field.
    direction : str
        Direction of the change: added or removed.
    confidence : Confidence
        Confidence of the impact detection.
    provenance : str
        Provenance of the impact detection.
    impact_completeness : str
        Completeness indicator of the impact assessment.
    fixable : bool
        Whether the impact is fixable.
    evidence : dict[str, object]
        Structured evidence supporting the impact.
    base_context : SymbolContext | None
        Symbol context at BASE, when available.
    head_context : SymbolContext | None
        Symbol context at HEAD, when available.

    """

    impact_kind: str | None
    code: str | None
    value: str
    direction: str
    confidence: Confidence
    provenance: str
    impact_completeness: str
    fixable: bool
    evidence: dict[str, object]
    base_context: SymbolContext | None = None
    head_context: SymbolContext | None = None


@dataclass(frozen=True, slots=True)
class DocumentationImpact:
    """Documentation impact for ``impact_analysis.impacts``.

    Attributes
    ----------
    symbol : str
        Qualified name of the affected symbol.
    impact_kind : str
        Kind of the documentation impact.
    value : str
        Canonical value of the impacted behavioral field.
    direction : str
        Direction of the change: added or removed.
    status : str
        Status of the impact.
    confidence : Confidence
        Confidence of the impact detection.
    provenance : str
        Provenance of the impact detection.
    impact_completeness : str
        Completeness indicator of the impact assessment.
    fixable : bool
        Whether the impact is fixable.
    path : tuple[str, ...]
        Path of the impacted behavioral field.
    code : str | None
        DMT code of the impact, when known.
    base_context : SymbolContext | None
        Symbol context at BASE, when available.
    head_context : SymbolContext | None
        Symbol context at HEAD, when available.
    file : str | None
        Project-relative file of the impacted symbol, when known.
    line_start : int | None
        Starting line of the impacted symbol, when known.
    symbol_kind : str | None
        Kind of the impacted symbol, when known.
    docstring_sha256 : str | None
        SHA-256 digest of the docstring body, when known.
    evidence : dict[str, object]
        Structured evidence supporting the impact.

    """

    symbol: str
    impact_kind: str
    value: str
    direction: str
    status: str
    confidence: Confidence
    provenance: str
    impact_completeness: str
    fixable: bool
    path: tuple[str, ...] = ()
    code: str | None = None
    base_context: SymbolContext | None = None
    head_context: SymbolContext | None = None
    file: str | None = None
    line_start: int | None = None
    symbol_kind: str | None = None
    docstring_sha256: str | None = None
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmissionDelta:
    """Delta emission result: at most one CheckEntry and one impact.

    Attributes
    ----------
    entry : CheckEntry | None
        Check entry emitted for the delta, when any.
    meta : MetaDia | None
        DIA metadata attached to the entry, when any.
    impact : DocumentationImpact | None
        Documentation impact for the report, when any.

    """

    entry: CheckEntry | None = None
    meta: MetaDia | None = None
    impact: DocumentationImpact | None = None


@dataclass(frozen=True, slots=True)
class DiaEmissionContext:
    """Emission context: configuration and head/base records by symbol.

    Attributes
    ----------
    configuration : CheckConfig
        Check configuration controlling emission.
    records_head : dict[str, FunctionRecord | ClassRecord]
        HEAD records by qualified symbol name.
    records_base : dict[str, FunctionRecord | ClassRecord]
        BASE records by qualified symbol name.
    completeness : str
        Completeness indicator of the emission context.
    project_root : Path | None
        Project root used to normalize public report paths. It is optional for
        low-level callers that only need to exercise emission classification.

    """

    configuration: CheckConfig
    records_head: dict[str, FunctionRecord | ClassRecord]
    records_base: dict[str, FunctionRecord | ClassRecord]
    completeness: str = "complete"
    project_root: Path | None = None


@dataclass(frozen=True, slots=True)
class DiaResult:
    """Documentation impact analysis output for a check run.

    Attributes
    ----------
    impact_analysis : ImpactAnalysis
        Top-level DIA report block.
    entries : list[CheckEntry]
        DIA check entries emitted for the run.

    """

    impact_analysis: ImpactAnalysis
    entries: list[CheckEntry] = field(default_factory=list)
