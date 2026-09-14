# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Declarative API signatures and visibility filtering for DIA."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmethis_extract_python.api import MISSING_VALUE, Confidence, ModuleRecord, iter_functions

from docmethis_check.models import SymbolKind

from .context import _selected_symbol_visibility
from .models import DeclarativeChange, DeclarativeParameter, DeclarativeSignature

if TYPE_CHECKING:
    from docmethis_extract_python.api import ProjectRecord

    from docmethis_check.config import CheckConfig
    from docmethis_check.git_diff import ChangedFile


def _extract_declarative_signatures(
    modules: list[ModuleRecord],
    configuration: CheckConfig,
) -> dict[str, DeclarativeSignature]:
    """Return declarative signatures by selected symbol.

    Parameters
    ----------
    modules : list[ModuleRecord]
        A list of ModuleRecord objects representing the modules to inspect for declarative signature extraction.
    configuration : CheckConfig
        Configuration settings that control which symbols are selected based on visibility rules.

    Returns
    -------
    dict[str, DeclarativeSignature]
        Mapping of qualified symbol names to declarative signatures for symbols selected by configuration.

    """
    result: dict[str, DeclarativeSignature] = {}
    for module in modules:
        class_visibilities = {
            method.qualified_name: class_record.visibility for class_record in module.classes for method in class_record.methods
        }
        for fn in iter_functions([module]):
            kind = SymbolKind.METHOD if fn.parent_class is not None else SymbolKind.FUNCTION
            if (
                _selected_symbol_visibility(
                    fn,
                    kind=kind,
                    module_visibility=module.visibility,
                    configuration=configuration,
                    container_visibility=class_visibilities.get(fn.qualified_name),
                )
                is None
            ):
                continue
            if fn.signature is None or fn.signature_confidence is Confidence.ABSENT:
                continue
            parameters = frozenset(
                DeclarativeParameter(
                    name=param.name,
                    default=param.default_value,
                    kind=param.kind,
                )
                for param in fn.signature.parameters
            )
            return_type = fn.signature.return_annotation if fn.signature.return_annotation != MISSING_VALUE else ""
            result[fn.qualified_name] = DeclarativeSignature(
                parameters=parameters,
                return_type=return_type,
                is_async=fn.is_async,
            )
    return result


def _api_diff(
    base_signatures: dict[str, DeclarativeSignature],
    head_signatures: dict[str, DeclarativeSignature],
) -> list[DeclarativeChange]:
    """Return informational declarative API changes.

    Parameters
    ----------
    base_signatures : dict[str, DeclarativeSignature]
        Mapping of symbol names to their declarative signatures in the base version, used as the reference for detecting API
        changes.
    head_signatures : dict[str, DeclarativeSignature]
        A dictionary mapping API symbol names to their declarative signatures in the head (newer) version, used to compare against
        base_signatures to detect changes.

    Returns
    -------
    list[DeclarativeChange]
        A list of DeclarativeChange objects describing informational API differences detected between the base and head
        signatures, including added, modified, or removed parameters, return type changes, and async status changes.

    """
    changes: list[DeclarativeChange] = []
    for symbol in base_signatures.keys() & head_signatures.keys():
        base_sig = base_signatures[symbol]
        head_sig = head_signatures[symbol]

        base_params = {param.name: param for param in base_sig.parameters}
        head_params = {param.name: param for param in head_sig.parameters}

        for name, param in head_params.items():
            if name not in base_params:
                changes.append(DeclarativeChange(symbol, "parametre_ajoute", None, param.default, parameter_symbol=name))
                continue
            ancien = base_params[name]
            if ancien.default != param.default or ancien.kind != param.kind:
                changes.append(
                    DeclarativeChange(
                        symbol,
                        "default_modifie",
                        f"{ancien.kind.value}={ancien.default}",
                        f"{param.kind.value}={param.default}",
                        parameter_symbol=name,
                    )
                )
        changes.extend(
            DeclarativeChange(symbol, "parametre_retire", base_params[name].default, None, parameter_symbol=name)
            for name in base_params.keys() - head_params.keys()
        )

        if base_sig.return_type != head_sig.return_type:
            changes.append(
                DeclarativeChange(
                    symbol,
                    "retour_modifie",
                    base_sig.return_type or None,
                    head_sig.return_type or None,
                ),
            )
        if base_sig.is_async != head_sig.is_async:
            changes.append(DeclarativeChange(symbol, "async", str(base_sig.is_async), str(head_sig.is_async)))
    return changes


def _api_diff_visible(
    project_head: ProjectRecord,
    diff_files: list[ChangedFile],
    impacts: list[dict[str, object]],
    changes: list[DeclarativeChange],
) -> list[dict[str, object]]:
    """Return declarative changes in the diff or carrying an impact.

    Parameters
    ----------
    project_head : ProjectRecord
        The project record representing the head state of the project, used to resolve modules and symbols for diff visibility
        analysis.
    diff_files : list[ChangedFile]
        Files changed in the diff, used to determine which declared symbols are visible.
    impacts : list[dict[str, object]]
        List of impact records, each containing at least a symbol key identifying the affected API symbol.
    changes : list[DeclarativeChange]
        The declarative changes to filter, retaining only those whose symbol is present in the diff or in the impacted symbols.

    Returns
    -------
    list[dict[str, object]]
        A list of dictionaries, each representing a declarative change that is either present in the diff or associated with an
        impact.

    """
    diff_paths = {changed_file.path.resolve() for changed_file in diff_files}
    diff_symbols: set[str] = set()
    for module in project_head.modules:
        if module.file_path.resolve() not in diff_paths:
            continue
        diff_symbols.add(module.module_name)
        diff_symbols.update(class_record.qualified_name for class_record in module.classes)
        diff_symbols.update(fn.qualified_name for fn in iter_functions([module]))
    impacted_symbols = {impact["symbol"] for impact in impacts}

    visible_changes = [change for change in changes if change.symbol in diff_symbols or change.symbol in impacted_symbols]
    return [_change_to_dict(change) for change in visible_changes]


def _change_to_dict(change: DeclarativeChange) -> dict[str, object]:
    """Serialize a declarative change for ``impact_analysis.api_diff``.

    Parameters
    ----------
    change : DeclarativeChange
        The declarative change to serialize.

    Returns
    -------
    dict[str, object]
        A dictionary representing the serialized declarative change, including the symbol and aspect, and optionally the before
        and after values and the parameter symbol when present.

    """
    data: dict[str, object] = {
        "symbol": change.symbol,
        "aspect": {
            "parametre_ajoute": "parameter_added",
            "default_modifie": "default_changed",
            "parametre_retire": "parameter_removed",
            "retour_modifie": "return_changed",
        }.get(change.aspect, change.aspect),
    }
    if change.before is not None:
        data["before"] = change.before
    if change.after is not None:
        data["after"] = change.after
    if change.parameter_symbol is not None:
        data["parameter"] = change.parameter_symbol
    return data
