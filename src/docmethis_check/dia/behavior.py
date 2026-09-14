# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Normalized behavioral keys and deltas for DIA."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmethis_extract_python.api import (
    ClassRecord,
    Confidence,
    FunctionRecord,
    ModuleRecord,
    Provenance,
    exception_escapes,
)

from docmethis_check.models import SymbolKind

from .context import (
    _build_symbol_contexts,
    _effective_class_methods,
    _method_origin_confidence,
    _selected_symbol_visibility,
)
from .models import BehavioralKey, DeltaBehavioral, SymbolContext

if TYPE_CHECKING:
    from docmethis_check.config import CheckConfig

_CATEGORIES_IO = ("writes_files", "reads_files", "network_access", "system_calls", "mutates_global", "has_output")
_EMPTY_FACT_VALUE = "<empty fact>"
_EXCEPTION_ROOTS = {"exception", "baseexception"}


def _extract_behavioral_keys(  # noqa: C901
    modules: list[ModuleRecord],
    configuration: CheckConfig,
    *,
    symbol_contexts: dict[str, SymbolContext] | None = None,
) -> dict[str, dict[str, frozenset[BehavioralKey]]]:
    """Build behavioral keys by selected symbol.

    Parameters
    ----------
    modules : list[ModuleRecord]
        A list of module records to process. Each record provides the classes, functions, and effective methods that are
        considered when extracting behavioral keys.
    configuration : CheckConfig
        Configuration used to determine symbol selection and visibility filtering while building behavioral keys.
    symbol_contexts : dict[str, SymbolContext] | None = None
        Optional mapping of fully qualified symbol names to their precomputed SymbolContext instances, used to supply contextual
        metadata when extracting behavioral keys. If not provided, the contexts are built automatically from the given modules.

    Returns
    -------
    dict[str, dict[str, frozenset[BehavioralKey]]]
        A dictionary mapping each selected symbol's qualified name to its behavioral keys, where each value is a dictionary from
        behavioral-key categories to frozensets of BehavioralKey entries for that symbol.

    """
    result: dict[str, dict[str, frozenset[BehavioralKey]]] = {}
    contexts = symbol_contexts or _build_symbol_contexts(modules)
    class_index = {class_record.qualified_name: class_record for module in modules for class_record in module.classes}
    for module in modules:
        for class_record in module.classes:
            if (
                _selected_symbol_visibility(
                    class_record,
                    kind=SymbolKind.CLASS,
                    module_visibility=module.visibility,
                    configuration=configuration,
                    container_visibility=None,
                )
                is None
            ):
                continue
            keys = _class_behavioral_keys(class_record, contexts.get(class_record.qualified_name))
            if keys:
                result[class_record.qualified_name] = keys

        for fn in module.functions:
            if (
                _selected_symbol_visibility(
                    fn,
                    kind=SymbolKind.FUNCTION,
                    module_visibility=module.visibility,
                    configuration=configuration,
                    container_visibility=None,
                )
                is None
            ):
                continue
            context = contexts.get(fn.qualified_name)
            keys = _function_behavioral_keys(
                fn,
                context.method_origin_confidence if context is not None else _method_origin_confidence(fn, module),
                symbol_context=context,
            )
            if keys:
                result[fn.qualified_name] = keys

        for class_record in module.classes:
            for symbol, fn in _effective_class_methods(class_record, class_index):
                if (
                    _selected_symbol_visibility(
                        fn,
                        kind=SymbolKind.METHOD,
                        module_visibility=module.visibility,
                        configuration=configuration,
                        container_visibility=class_record.visibility,
                    )
                    is None
                ):
                    continue
                context = contexts.get(symbol)
                keys = _function_behavioral_keys(
                    fn,
                    context.method_origin_confidence if context is not None else _method_origin_confidence(fn, module),
                    symbol_context=context,
                )
                if keys:
                    result[symbol] = keys
    return result


def _function_behavioral_keys(
    fn: FunctionRecord,
    method_origin_confidence: Confidence,
    *,
    symbol_context: SymbolContext | None = None,
) -> dict[str, frozenset[BehavioralKey]]:
    """Build exception, I/O, observable, and method-origin keys for a function.

    Parameters
    ----------
    fn : FunctionRecord
        The function record for which to build behavioral keys.
    method_origin_confidence : Confidence
        Confidence value indicating how certain the method-origin information is; when this is Confidence.ABSENT, no method_origin
        behavioral key is produced.
    symbol_context : SymbolContext | None = None
        Optional symbol context used to determine whether the function's behavior is concrete and to attach contextual information
        to the generated behavioral keys. When None, the function is treated as concrete.

    Returns
    -------
    dict[str, frozenset[BehavioralKey]]
        A dictionary mapping behavioral key category names to frozensets of BehavioralKey instances. The dictionary may contain
        entries for 'exception', 'io', 'observable', and/or 'method_origin' only when the corresponding information is present and
        sufficiently confident; absent categories are omitted.

    """
    keys: dict[str, frozenset[BehavioralKey]] = {}
    concrete_behavior = symbol_context is None or symbol_context.implementation_kind == "concrete"

    if concrete_behavior and fn.exceptions_confidence is not Confidence.ABSENT:
        keys["exception"] = _fact_set(
            "exception",
            [
                BehavioralKey(
                    "exception",
                    f"{exc.exception_type}:{_exception_provenance(exc.provenance)}",
                    _exception_confidence(exc.provenance),
                    symbol_context,
                )
                for exc in fn.exceptions or []
                if exception_escapes(fn, exc)
            ],
            fn.exceptions_confidence,
            symbol_context,
        )

    if concrete_behavior and fn.io_effects is not None and fn.io_effects_confidence is not Confidence.ABSENT:
        categories = [category for category in _CATEGORIES_IO if getattr(fn.io_effects, category)]
        keys["io"] = _fact_set(
            "io",
            [BehavioralKey("io", f"io.{category}", fn.io_effects_confidence, symbol_context) for category in categories],
            fn.io_effects_confidence,
            symbol_context,
        )

    if concrete_behavior and fn.observable_properties_confidence is not Confidence.ABSENT:
        keys["observable"] = _fact_set(
            "observable",
            [
                BehavioralKey(
                    "observable",
                    f"{property_.category}:{property_.raw_expression.strip()}",
                    fn.observable_properties_confidence,
                    symbol_context,
                )
                for property_ in fn.observable_properties or []
            ],
            fn.observable_properties_confidence,
            symbol_context,
        )

    if fn.method_origin is not None and method_origin_confidence is not Confidence.ABSENT:
        keys["method_origin"] = frozenset(
            {BehavioralKey("method_origin", fn.method_origin.value, method_origin_confidence, symbol_context)},
        )

    return keys


def _fact_set(
    field: str,
    facts: list[BehavioralKey],
    confidence: Confidence,
    symbol_context: SymbolContext | None = None,
) -> frozenset[BehavioralKey]:
    """Return facts, or one sentinel when an analyzed field is empty.

    Parameters
    ----------
    field : str
        Name of the analyzed field.
    facts : list[BehavioralKey]
        A list of behavioral keys that represent the facts to be processed. If the list is non-empty, the function returns these
        facts as a frozenset; if empty, a sentinel fact is generated.
    confidence : Confidence
        Confidence value assigned to the sentinel behavioral key that is returned when the analyzed field is empty.
    symbol_context : SymbolContext | None = None
        Optional SymbolContext to include in the sentinel BehavioralKey created when facts is empty.

    Returns
    -------
    frozenset[BehavioralKey]
        A frozenset of BehavioralKey objects. If facts is non-empty, returns a frozenset containing those facts. If facts is
        empty, returns a frozenset containing a single sentinel BehavioralKey constructed from field, _EMPTY_FACT_VALUE,
        confidence, and symbol_context.

    """
    if facts:
        return frozenset(facts)
    return frozenset({BehavioralKey(field, _EMPTY_FACT_VALUE, confidence, symbol_context)})


def _class_behavioral_keys(
    class_record: ClassRecord,
    symbol_context: SymbolContext | None = None,
) -> dict[str, frozenset[BehavioralKey]]:
    """Build the inheritance key for a class.

    Parameters
    ----------
    class_record : ClassRecord
        The class record whose inheritance hierarchy and abstract status are used to build the behavioral heritage key.
    symbol_context : SymbolContext | None = None
        Optional symbol context associated with the class record; used to provide additional location or naming information when
        building behavioral keys. Defaults to None.

    Returns
    -------
    dict[str, frozenset[BehavioralKey]]
        Returns a dictionary mapping behavioral key names to frozensets of BehavioralKey objects. For a class with a known
        hierarchy, the dictionary contains a single 'heritage' entry whose frozenset holds one BehavioralKey encoding the direct
        parents and abstract status of the class. If the class hierarchy is absent or its confidence is ABSENT, an empty
        dictionary is returned.

    """
    if class_record.hierarchy is None or class_record.hierarchy_confidence is Confidence.ABSENT:
        return {}
    parents = tuple(sorted(class_record.hierarchy.direct_parents))
    value = f"parents={','.join(parents)};abstract={class_record.hierarchy.is_abstract}"
    return {
        "heritage": frozenset(
            {BehavioralKey("heritage", value, class_record.hierarchy_confidence, symbol_context)},
        ),
    }


def _exception_provenance(provenance: Provenance) -> str:
    """Reduce exception provenance to ``local`` or ``propagated``.

    Parameters
    ----------
    provenance : Provenance
        Provenance value indicating how the exception originated; it is reduced to either 'local' or 'propagated'.

    Returns
    -------
    str
        Returns 'propagated' if the provenance is CALLGRAPH_PROPAGATION; otherwise returns 'local'.

    """
    if provenance is Provenance.CALLGRAPH_PROPAGATION:
        return "propagated"
    return "local"


def _exception_confidence(provenance: Provenance) -> Confidence:
    """Return confidence for local and propagated exception facts.

    Parameters
    ----------
    provenance : Provenance
        The provenance of the exception fact, which determines whether the returned confidence is inferred-high for call graph
        propagation or explicit for local facts.

    Returns
    -------
    Confidence
        A Confidence value representing the confidence level for local and propagated exception facts. For callgraph-propagated
        provenance, this is INFERRED_HIGH; otherwise, it is EXPLICIT.

    """
    if provenance is Provenance.CALLGRAPH_PROPAGATION:
        return Confidence.INFERRED_HIGH
    return Confidence.EXPLICIT


def _behavioral_delta(
    base_keys: dict[str, dict[str, frozenset[BehavioralKey]]],
    head_keys: dict[str, dict[str, frozenset[BehavioralKey]]],
) -> list[DeltaBehavioral]:
    """Compare comparable BASE and HEAD behavioral keys.

    Parameters
    ----------
    base_keys : dict[str, dict[str, frozenset[BehavioralKey]]]
        A mapping whose keys are API symbol names and whose values are mappings from behavioral field names to frozensets of
        BehavioralKey objects, representing the BASE side's behavioral facts for comparison.
    head_keys : dict[str, dict[str, frozenset[BehavioralKey]]]
        Behavioral keys from the HEAD version, organized by symbol and behavioral field, used as the comparison target against
        base_keys.

    Returns
    -------
    list[DeltaBehavioral]
        A list of DeltaBehavioral objects describing added or removed behavioral facts between the base and head versions, sorted
        by symbol, field, value, and direction.

    """
    deltas: list[DeltaBehavioral] = []
    for symbol in base_keys.keys() & head_keys.keys():
        for behavioral_field in base_keys[symbol].keys() | head_keys[symbol].keys():
            base_facts = base_keys[symbol].get(behavioral_field)
            head_facts = head_keys[symbol].get(behavioral_field)
            if base_facts is None or head_facts is None:
                continue

            base_values = {key.value for key in base_facts} - {_EMPTY_FACT_VALUE}
            head_values = {key.value for key in head_facts} - {_EMPTY_FACT_VALUE}
            if base_values == head_values:
                continue

            confidence = _minimum_confidence(base_facts, head_facts)
            base_context = next(iter(base_facts)).symbol_context
            head_context = next(iter(head_facts)).symbol_context
            deltas.extend(
                DeltaBehavioral(
                    symbol,
                    behavioral_field,
                    value,
                    "ajouté",
                    confidence,
                    base_context=base_context,
                    head_context=head_context,
                )
                for value in head_values - base_values
            )
            deltas.extend(
                DeltaBehavioral(
                    symbol,
                    behavioral_field,
                    value,
                    "retiré",
                    confidence,
                    base_context=base_context,
                    head_context=head_context,
                )
                for value in base_values - head_values
            )
    return sorted(deltas, key=lambda delta: (delta.symbol, delta.field, delta.value, delta.direction))


def _minimum_confidence(*key_sets: frozenset[BehavioralKey]) -> Confidence:
    """Return the lowest confidence among key sets.

    Parameters
    ----------
    key_sets : frozenset[BehavioralKey]
        One or more sets of behavioral keys whose confidence values are compared to determine the minimum.

    Returns
    -------
    Confidence
        The lowest confidence level found among all keys in the supplied key sets.

    """
    score = {
        Confidence.EXPLICIT: 4,
        Confidence.INFERRED_HIGH: 3,
        Confidence.INFERRED_LOW: 2,
        Confidence.ABSENT: 1,
    }
    minimum = min(score[key.confidence] for key_set in key_sets for key in key_set)
    return next(confidence for confidence, level in score.items() if level == minimum)


def _exception_type(value: str) -> str:
    """Return the exception type from a normalized behavioral key.

    Parameters
    ----------
    value : str
        The normalized behavioral key from which to extract the exception type.

    Returns
    -------
    str
        Return the exception type from a normalized behavioral key by taking the portion before the final colon.

    """
    return value.rsplit(":", 1)[0]
