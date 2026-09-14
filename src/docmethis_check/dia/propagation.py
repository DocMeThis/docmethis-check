# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Caller propagation and evidence paths for DIA."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from docmethis_check.diagnostics import _simple_exception_name

from .behavior import _exception_type

if TYPE_CHECKING:
    from docmethis_extract_python.api import CallgraphEdge

    from .models import DeltaBehavioral


def _caller_index(callgraph: dict[str, list[CallgraphEdge]]) -> dict[str, set[str]]:
    """Build a target-to-local-caller index from a source-oriented call graph.

    Parameters
    ----------
    callgraph : dict[str, list[CallgraphEdge]]
        Mapping from a source qualified name to the list of CallgraphEdge objects representing calls originating from that source,
        used to build the target-to-local-caller index.

    Returns
    -------
    dict[str, set[str]]
        A dictionary mapping each called target's qualified name to the set of local caller qualified names that reference it;
        external call edges are excluded.

    """
    index: dict[str, set[str]] = {}
    for source, edges in callgraph.items():
        for edge in edges:
            if edge.is_external:
                continue
            index.setdefault(edge.target_qname, set()).add(source)
    return index


def _affected_callers(
    callgraph_base: dict[str, list[CallgraphEdge]],
    callgraph_head: dict[str, list[CallgraphEdge]],
    changed_symbols: set[str],
    depth: int = 2,
) -> set[str]:
    """Return direct and transitive comparable callers up to ``depth``.

    Parameters
    ----------
    callgraph_base : dict[str, list[CallgraphEdge]]
        A mapping from each caller symbol to the list of call edges originating from that caller in the base version of the code.
    callgraph_head : dict[str, list[CallgraphEdge]]
        Callgraph representing the state after changes, mapping each symbol to a list of its outgoing call edges.
    changed_symbols : set[str]
        Set of symbols whose callers are considered as starting points for the affected-caller search.
    depth : int = 2
        Maximum number of caller levels to traverse; limits how many levels of direct and transitive callers are included.
        Defaults to 2.

    Returns
    -------
    set[str]
        The set of direct and transitive comparable callers up to the specified depth.

    """
    index_base = _caller_index(callgraph_base)
    index_head = _caller_index(callgraph_head)
    affected: set[str] = set()
    frontier = set(changed_symbols)
    for _ in range(depth):
        next_callers: set[str] = set()
        for symbol in frontier:
            callers = index_base.get(symbol, set()) & index_head.get(symbol, set())
            for caller in callers - affected:
                affected.add(caller)
                next_callers.add(caller)
        frontier = next_callers
        if not frontier:
            break
    return affected


def _mark_affected_callers(
    deltas: list[DeltaBehavioral],
    affected_callers: set[str],
    partial_symbols: frozenset[str] = frozenset(),
) -> list[DeltaBehavioral]:
    """Mark propagated exception deltas of affected callers.

    Parameters
    ----------
    deltas : list[DeltaBehavioral]
        The deltas to process; each DeltaBehavioral represents a delta that may be updated with propagated provenance and impact
        completeness if its symbol is in affected_callers and its field/value match the propagation criteria.
    affected_callers : set[str]
        Set of caller symbols whose propagated exception deltas should be marked.
    partial_symbols : frozenset[str]
        A frozenset of symbol names that should be considered partial when marking propagated exception deltas; deltas whose
        symbol is in this set receive an impact completeness of 'partial' instead of 'complete'.

    Returns
    -------
    list[DeltaBehavioral]
        Returns the updated list of DeltaBehavioral objects, where exception deltas belonging to affected callers have been marked
        with propagated provenance and an impact completeness of either partial or complete based on whether the symbol is in
        partial_symbols; all other deltas are returned unchanged.

    """
    result: list[DeltaBehavioral] = []
    for delta in deltas:
        if delta.symbol in affected_callers and delta.field == "exception" and delta.value.endswith(":propagated"):
            completeness = "partial" if delta.symbol in partial_symbols else "complete"
            result.append(replace(delta, provenance="propagated", impact_completeness=completeness))
        else:
            result.append(delta)
    return result


def _propagation_path(  # noqa: PLR0913, PLR0917
    callgraph: dict[str, list[CallgraphEdge]],
    symbol: str,
    causes: set[str],
    exception_type: str,
    exception_types: dict[str, set[str]],
    depth: int = 2,
) -> list[str]:
    """Return the call chain from an affected symbol to its diff cause.

    Parameters
    ----------
    callgraph : dict[str, list[CallgraphEdge]]
        A dictionary mapping each function's qualified name to a list of CallgraphEdge objects representing outgoing calls from
        that function.
    symbol : str
        The qualified name of the affected symbol from which to start tracing the call chain.
    causes : set[str]
        Set of qualified symbol names that are considered diff causes; used to stop the propagation path when a candidate is found
        among them.
    exception_type : str
        The exception type used to filter call-graph edges when building the propagation path; only edges whose target is
        associated with this exception type are considered as candidates.
    exception_types : dict[str, set[str]]
        Mapping from qualified symbol names to the sets of exception type names that each symbol may raise, used to filter
        call-graph edges to those whose target can raise the relevant exception.
    depth : int = 2
        Maximum number of call-graph levels to traverse when building the propagation path. The search stops after this many steps
        even if no diff cause has been reached.

    Returns
    -------
    list[str]
        The call chain as a list of fully qualified symbol names, starting with the affected symbol and ending at the first diff
        cause reached, or at the last reachable symbol if no cause is found within the depth limit.

    """
    target = _simple_exception_name(_exception_type(exception_type))
    path = [symbol]
    current = symbol
    for _ in range(depth):
        candidates = sorted(
            edge.target_qname
            for edge in callgraph.get(current, [])
            if not edge.is_external and target in exception_types.get(edge.target_qname, set())
        )
        if not candidates:
            break
        next_symbol = next((candidate for candidate in candidates if candidate in causes), candidates[0])
        path.append(next_symbol)
        if next_symbol in causes:
            break
        current = next_symbol
    return path
