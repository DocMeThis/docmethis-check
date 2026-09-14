# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Effective symbol contexts for DIA."""

from __future__ import annotations

import ast
import textwrap
from dataclasses import replace
from typing import TYPE_CHECKING

from docmethis_extract_python.api import (
    ClassRecord,
    Confidence,
    FunctionRecord,
    MethodOrigin,
    ModuleRecord,
    ProjectRecord,
    Visibility,
    effective_visibility,
)

from docmethis_check.models import SymbolKind  # noqa: TC001

from .models import ImplementationKind, SymbolContext

if TYPE_CHECKING:
    from collections.abc import Iterator

    from docmethis_check.config import CheckConfig


def _effective_class_methods(
    class_record: ClassRecord,
    class_index: dict[str, ClassRecord],
) -> Iterator[tuple[str, FunctionRecord]]:
    """Return own methods and the first implementation of each inherited method.

    Parameters
    ----------
    class_record : ClassRecord
        The class record whose own and inherited methods are to be collected.
    class_index : dict[str, ClassRecord]
        Mapping of qualified class names to ClassRecord instances, used to resolve parent classes when walking the MRO.

    Returns
    -------
    Iterator[tuple[str, FunctionRecord]]
        Yield each own method as a (qualified name, function record) pair, then yield the first encountered implementation of each
        inherited method from the MRO, rewriting its qualified name to the current class and marking it as inherited.

    """
    seen_names: set[str] = set()
    for fn in class_record.methods:
        seen_names.add(fn.qualified_name.rsplit(".", 1)[-1])
        yield fn.qualified_name, fn

    if class_record.hierarchy is None or not class_record.hierarchy.mro_list:
        return

    for parent_qname in class_record.hierarchy.mro_list[1:]:
        parent = class_index.get(parent_qname)
        if parent is None:
            continue
        for fn in parent.methods:
            name = fn.qualified_name.rsplit(".", 1)[-1]
            if name in seen_names:
                continue
            symbol = f"{class_record.qualified_name}.{name}"
            seen_names.add(name)
            yield (
                symbol,
                replace(
                    fn,
                    qualified_name=symbol,
                    parent_class=class_record.qualified_name,
                    method_origin=MethodOrigin.INHERITED,
                ),
            )


def _build_symbol_contexts(modules: list[ModuleRecord]) -> dict[str, SymbolContext]:
    """Build effective contexts without duplicating inherited methods.

    Parameters
    ----------
    modules : list[ModuleRecord]
        A list of module records whose functions, classes, and inherited methods are used to build symbol contexts.

    Returns
    -------
    dict[str, SymbolContext]
        Returns a dictionary mapping qualified symbol names to their corresponding SymbolContext objects, covering functions,
        classes, and inherited methods with their effective documentation contexts.

    """
    class_index = {class_record.qualified_name: class_record for module in modules for class_record in module.classes}
    modules_by_name = {module.module_name: module for module in modules}
    contexts: dict[str, SymbolContext] = {}
    for module in modules:
        for fn in module.functions:
            contexts[fn.qualified_name] = _function_context(fn, module, None, class_index)
        for class_record in module.classes:
            contexts[class_record.qualified_name] = SymbolContext(
                symbol=class_record.qualified_name,
                implementation=None,
                owner_class=None,
                documentation=class_record.existing_docstring,
                documentation_origin="own" if class_record.existing_docstring else "none",
                dispatch_role="regular",
                implementation_kind="concrete",
            )
            for fn in class_record.methods:
                contexts[fn.qualified_name] = _function_context(fn, module, class_record, class_index)
            for symbol, fn in _effective_class_methods(class_record, class_index):
                if fn.method_origin is not MethodOrigin.INHERITED:
                    continue
                source_module = modules_by_name.get(fn.parent_module, module)
                parent_documentation, parent_unknown = _parent_documentation(fn, class_record, class_index)
                documentation_origin = "inherited" if parent_documentation is not None else "none"
                if parent_unknown:
                    documentation_origin = "unknown"
                contexts[symbol] = SymbolContext(
                    symbol=symbol,
                    implementation=fn,
                    owner_class=class_record.qualified_name,
                    documentation=parent_documentation.existing_docstring if parent_documentation is not None else None,
                    documentation_origin=documentation_origin,
                    dispatch_role="overload" if fn.has_overloads else "regular",
                    implementation_kind=_type_implementation(fn, source_module),
                    inherited_contract_source=parent_documentation.qualified_name if parent_documentation is not None else None,
                    method_origin=MethodOrigin.INHERITED,
                    method_origin_confidence=class_record.hierarchy_confidence,
                )
    return contexts


def _function_context(
    fn: FunctionRecord,
    module: ModuleRecord,
    class_record: ClassRecord | None,
    class_index: dict[str, ClassRecord],
) -> SymbolContext:
    """Build the normalized context of an own function or method.

    Parameters
    ----------
    fn : FunctionRecord
        The function or method record whose normalized context is to be built.
    module : ModuleRecord
        ModuleRecord representing the module in which the function is defined; used to resolve implementation details.
    class_record : ClassRecord | None
        The optional ClassRecord representing the class that owns the function or method, or None if the function is not a method.
    class_index : dict[str, ClassRecord]
        Mapping of class names to ClassRecord instances, used to resolve inherited documentation and class hierarchy information.

    Returns
    -------
    SymbolContext
        A SymbolContext containing the normalized context for the function or method, including its qualified name, implementation
        record, owning class (if any), resolved documentation text and origin, dispatch role, implementation kind, inherited
        contract source when applicable, method origin, and method origin confidence.

    """
    parent_documentation, documentation_unknown = _parent_documentation(fn, class_record, class_index)
    if fn.existing_docstring:
        documentation_origin = "own"
    elif parent_documentation is not None and not documentation_unknown:
        documentation_origin = "inherited"
    elif documentation_unknown:
        documentation_origin = "unknown"
    else:
        documentation_origin = "none"

    method_origin = fn.method_origin
    if fn.has_overloads:
        dispatch_role = "overload"
    elif method_origin is MethodOrigin.OVERRIDDEN:
        dispatch_role = "override"
    else:
        dispatch_role = "regular"

    return SymbolContext(
        symbol=fn.qualified_name,
        implementation=fn,
        owner_class=class_record.qualified_name if class_record is not None else None,
        documentation=fn.existing_docstring
        or (parent_documentation.existing_docstring if parent_documentation is not None else None),
        documentation_origin=documentation_origin,
        dispatch_role=dispatch_role,
        implementation_kind=_type_implementation(fn, module),
        inherited_contract_source=parent_documentation.qualified_name if documentation_origin == "inherited" else None,
        method_origin=method_origin,
        method_origin_confidence=class_record.hierarchy_confidence if class_record is not None else Confidence.ABSENT,
    )


def _parent_documentation(
    fn: FunctionRecord,
    class_record: ClassRecord | None,
    class_index: dict[str, ClassRecord],
) -> tuple[FunctionRecord | None, bool]:
    """Find the first available method docstring in the MRO.

    Parameters
    ----------
    fn : FunctionRecord
        The function record whose method documentation is being searched for in parent classes.
    class_record : ClassRecord | None
        The class record whose MRO hierarchy is searched for an inherited method docstring; may be None, in which case no parent
        documentation is available.
    class_index : dict[str, ClassRecord]
        Mapping of fully qualified class names to ClassRecord instances, used to look up parent classes while traversing the MRO.

    Returns
    -------
    tuple[FunctionRecord | None, bool]
        Returns a tuple containing the first ancestor method implementation with an existing docstring found by walking the MRO,
        or None if no such implementation is found, and a boolean indicating whether any parent class could not be resolved during
        the search.

    """
    if class_record is None:
        return None, False
    if class_record.hierarchy is None:
        return None, fn.method_origin is MethodOrigin.OVERRIDDEN
    if not class_record.hierarchy.mro_list:
        return None, bool(class_record.hierarchy.direct_parents)

    parent_unknown = False
    method_name = fn.qualified_name.rsplit(".", 1)[-1]
    for parent_qname in class_record.hierarchy.mro_list[1:]:
        if parent_qname == "object":
            continue
        parent = class_index.get(parent_qname)
        if parent is None:
            parent_unknown = True
            continue
        implementation = next(
            (method for method in parent.methods if method.qualified_name.rsplit(".", 1)[-1] == method_name),
            None,
        )
        if implementation is not None and implementation.existing_docstring:
            return implementation, parent_unknown
    return None, parent_unknown


def _type_implementation(fn: FunctionRecord, module: ModuleRecord) -> ImplementationKind:
    """Classify an implementation, prioritizing ``@abstractmethod``.

    Parameters
    ----------
    fn : FunctionRecord
        The FunctionRecord representing the function to classify.
    module : ModuleRecord
        The module record to use when determining whether the implementation is a stub.

    Returns
    -------
    ImplementationKind
        The implementation kind for the function: 'abstract' when an abstractmethod decorator is present, 'stub' when the module
        is a stub or the body is inert, and 'concrete' otherwise.

    """
    if fn.signature is not None and any(
        decorator.full_decorator.rsplit(".", 1)[-1] == "abstractmethod" for decorator in fn.signature.decorators
    ):
        return "abstract"
    if module.is_stub or _inert_body(fn.source_code):
        return "stub"
    return "concrete"


def _inert_body(source_code: str | None) -> bool:
    """Return true for a body limited to a docstring, ``pass``, or ``...``.

    Parameters
    ----------
    source_code : str | None
        The source code of the function body to inspect, or None if no source code is available.

    Returns
    -------
    bool
        Return True if the body consists only of a docstring, a pass statement, or an ellipsis; otherwise return False.

    """
    if not source_code:
        return False
    try:
        tree = ast.parse(textwrap.dedent(source_code))
    except SyntaxError:
        return False
    function = next(
        (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )
    if function is None:
        return False
    body = list(function.body)
    if body and _is_ast_docstring(body[0]):
        body.pop(0)
    return not body or all(
        isinstance(statement, ast.Pass)
        or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and statement.value.value is Ellipsis)
        for statement in body
    )


def _is_ast_docstring(statement: ast.stmt) -> bool:
    """Return true for a text constant expression in first position.

    Parameters
    ----------
    statement : ast.stmt
        The AST statement to inspect for a docstring.

    Returns
    -------
    bool
        True if the statement is an AST expression containing a string constant; False otherwise.

    """
    return (
        isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str)
    )


def _selected_symbol_visibility(
    record: FunctionRecord,
    *,
    kind: SymbolKind,
    module_visibility: Visibility,
    configuration: CheckConfig,
    container_visibility: Visibility | None,
) -> Visibility | None:
    """Return a selected symbol's effective visibility.

    Parameters
    ----------
    record : FunctionRecord
        The function record representing the symbol whose effective visibility is to be determined.
    kind : SymbolKind
        The kind of symbol whose effective visibility is being determined. Only symbol kinds included in the configuration are
        considered.
    module_visibility : Visibility
        Visibility of the module that contains the symbol, used as the base when computing the effective visibility.
    configuration : CheckConfig
        The check configuration specifying the symbol kinds and visibility levels to include; the function returns None if the
        kind or effective visibility is not allowed by this configuration.
    container_visibility : Visibility | None
        The visibility of the container that encloses the selected symbol, if any. When provided, it is combined with the symbol's
        effective visibility to determine the final visibility; a value of None means no container-level restriction is applied.

    Returns
    -------
    Visibility | None
        The effective visibility of the selected symbol, or None if the symbol kind is not configured or the effective visibility
        is not included in the configured visibility set.

    """
    if kind not in configuration.symbol_kinds:
        return None
    visibility = effective_visibility(module_visibility, record.visibility)
    if container_visibility is not None:
        visibility = effective_visibility(visibility, container_visibility)
    if visibility not in configuration.include_visibility:
        return None
    return visibility


def _method_origin_confidence(fn: FunctionRecord, module: ModuleRecord) -> Confidence:
    """Return method-origin confidence from the class hierarchy.

    Parameters
    ----------
    fn : FunctionRecord
        The function record whose method-origin confidence is to be determined.
    module : ModuleRecord
        The module record whose class hierarchy is searched to determine the method-origin confidence for the function's parent
        class.

    Returns
    -------
    Confidence
        A Confidence value indicating the method-origin confidence derived from the class hierarchy. Returns Confidence.ABSENT if
        the function has no parent class or if the parent class is not found in the module.

    """
    if fn.parent_class is None:
        return Confidence.ABSENT
    for class_record in module.classes:
        if class_record.qualified_name == fn.parent_class:
            return class_record.hierarchy_confidence
    return Confidence.ABSENT


def _index_symbols(project: ProjectRecord) -> dict[str, FunctionRecord | ClassRecord]:
    """Index functions, classes, and effective class methods for DIA.

    Parameters
    ----------
    project : ProjectRecord
        The project containing the modules whose functions, classes, and effective class methods are to be indexed.

    Returns
    -------
    dict[str, FunctionRecord | ClassRecord]
        Indexes functions, classes, and effective class methods from the project and returns a dictionary mapping their qualified
        names to FunctionRecord or ClassRecord instances.

    """
    index: dict[str, FunctionRecord | ClassRecord] = {}
    class_index = {class_record.qualified_name: class_record for module in project.modules for class_record in module.classes}
    for module in project.modules:
        for fn in module.functions:
            index[fn.qualified_name] = fn
        for class_record in module.classes:
            index[class_record.qualified_name] = class_record
            index.update(dict(_effective_class_methods(class_record, class_index)))
    return index
