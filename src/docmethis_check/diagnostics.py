# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Diagnostic keys and base/head extraction for regression mode."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from docmethis_extract_python.api import ClassRecord, ExceptionRecord, ModuleRecord
    from docmethis_verify import FormatWarning, VerificationError

    from docmethis_check.config import CheckConfig

from docmethis_extract_python.api import (
    Confidence,
    FunctionRecord,
    MethodType,
    Provenance,
    TypeInfo,
    Visibility,
    effective_visibility,
    exception_escapes,
    extract_module_record,
    is_stub_file,
    is_test_file,
    iter_functions,
    resolve_module_name,
)
from docmethis_verify import VerificationErrorType, VerificationRequest
from docmethis_verify.dmt import (
    CODE_BY_WARNING_KIND,
    DMT_1101,
    DMT_1110,
    DMT_1120,
    DMT_1130,
    DMT_2001,
    DMT_2002,
    DMT_2003,
    DMT_2120,
    DMT_3001,
    DMT_3010,
    DMT_4001,
    DMT_4301,
    DMT_7001,
    KNOWN_CODES,
    technical_severity,
)
from docmethis_verify.docstring.models import GeneratedDocstring
from docmethis_verify.docstring.numpy_parser import parse_numpy_docstring
from docmethis_verify.docstring.section_parser import find_section, parse_parameters, parse_raises, parse_return
from docmethis_verify.verification.format_validator import section_canonical_probable
from docmethis_verify.verification.verifier import verify as verify_docstring

from docmethis_check.git_diff import git_executable
from docmethis_check.models import CheckEntry, MethodExceptionContract, Severity, SymbolKind

logger = logging.getLogger(__name__)

__all__ = [
    "BaseSnapshot",
    "DiagnosticKey",
    "base_classes",
    "base_diagnostics",
    "base_functions",
    "diagnostics_for_class",
    "diagnostics_for_file",
    "diagnostics_for_function",
    "diagnostics_for_module",
    "produce_diagnostics",
]

_GIT_SHOW_TIMEOUT_SECONDS = 10
_HASH_NOT_APPLICABLE: Final[str] = "<non_applicable>"
_INTERNAL_TYPES = {VerificationErrorType.FORBIDDEN_TERM}

_MESSAGE_FUNCTION_WITHOUT_DOCSTRING = "Function '{qualified_name}' has no docstring"
_MESSAGE_METHOD_WITHOUT_DOCSTRING = "Method '{qualified_name}' has no docstring"
_MESSAGE_CLASS_WITHOUT_DOCSTRING = "Class '{qualified_name}' has no docstring"
_MESSAGE_MODULE_WITHOUT_DOCSTRING = "Module '{module_name}' has no docstring"
_MESSAGE_CLASS_WITHOUT_SUMMARY = "Class docstring '{qualified_name}' has no usable summary"
_MESSAGE_MODULE_WITHOUT_SUMMARY = "Module docstring '{module_name}' has no usable summary"
_MESSAGE_UNDOCUMENTED_CLASS_EXCEPTION = "Exception exposed by class methods is not documented in class Raises: {exception_type}"
_MESSAGE_MISSING_RETURNS_SECTION = "Return is not documented (type '{type_str}')"
_MESSAGE_IGNORED_INTERNAL_VERIFY = "Internal verification diagnostic ignored by docmethis_check: %s"

_DOWNSTREAM_DIAGNOSTICS_BY_SECTION: dict[str, frozenset[str]] = {
    "Parameters": frozenset({DMT_2001, DMT_2002, DMT_2003, DMT_2120}),
    "Returns": frozenset({DMT_3001, DMT_3010}),
}


def _default_severity(code: str) -> Severity:
    """Return the default technical severity for a code from the DMT registry.

    Parameters
    ----------
    code : str
        The DMT registry code for which to retrieve the default technical severity.

    Returns
    -------
    Severity
        The default technical severity for the given code from the DMT registry.

    """
    return technical_severity(code)


_MISSING_DOCSTRING_CODES: dict[SymbolKind, str] = {
    SymbolKind.MODULE: DMT_1101,
    SymbolKind.CLASS: DMT_1110,
    SymbolKind.FUNCTION: DMT_1120,
    SymbolKind.METHOD: DMT_1130,
}

_VISIBILITY_OFFSETS: dict[Visibility, int] = {
    Visibility.PUBLIC: 0,
    Visibility.PROTECTED: 100,
    Visibility.PRIVATE: 200,
}


def _missing_docstring_code(*, kind: SymbolKind, visibility: Visibility, method_type: MethodType | None = None) -> str:
    """Return the DMT-1xx code for a symbol without a docstring.

    Properties (``@property``, MethodType.PROPERTY) use the property block (120-124).

    Parameters
    ----------
    kind : SymbolKind
        The kind of symbol for which the missing docstring code is computed.
    visibility : Visibility
        Visibility of the symbol, used to select the offset applied to the base DMT-1xx code.
    method_type : MethodType | None = None
        Method type of the symbol, if applicable. Pass MethodType.PROPERTY for property methods; otherwise omit or use None.

    Returns
    -------
    str
        The DMT-1xx diagnostic code for the symbol, adjusted for visibility; property methods use the property block codes
        120-124.

    """
    base = "DMT-1140" if method_type is MethodType.PROPERTY else _MISSING_DOCSTRING_CODES[kind]
    return f"DMT-{int(base[4:]) + _VISIBILITY_OFFSETS[visibility]}"


@dataclass(frozen=True)
class DiagnosticKey:
    """Stable diagnostic key independent of the human-readable message.

    ``expected`` and ``observed`` are normalization fields whose meaning depends
    on the DMT code: expected type, expected parameter, observed value, and so
    on. They do not have one shared meaning across all codes.

    Attributes
    ----------
    code : str
        DMT diagnostic code.
    file : str
        Path of the file the diagnostic applies to.
    symbol : str
        Qualified name of the diagnosed symbol.
    section : str | None
        Docstring section the diagnostic applies to, when relevant.
    parameter_name : str | None
        Parameter the diagnostic applies to, when relevant.
    expected : str | None
        Normalized expected value, when relevant.
    observed : str | None
        Normalized observed value, when relevant.
    accessor_kind : str | None
        Property accessor role, when relevant.
    visibility : str | None
        Effective visibility of the diagnosed symbol, when relevant.
    owner_symbol : str | None
        Qualified owner symbol for member diagnostics, when relevant.
    attribute_name : str | None
        Exact attribute name for class-attribute diagnostics, when relevant.
    discriminator : str | None
        Discriminator used to deduplicate identical diagnostics.

    """

    code: str
    file: str
    symbol: str
    section: str | None = None
    parameter_name: str | None = None
    expected: str | None = None
    observed: str | None = None
    accessor_kind: str | None = None
    visibility: str | None = None
    owner_symbol: str | None = None
    attribute_name: str | None = None
    discriminator: str | None = None


@dataclass(frozen=True)
class BaseSnapshot:
    """AST module extracted from a file at the base revision.

    Attributes
    ----------
    relative_path : str
        Path of the file relative to the project root.
    record : ModuleRecord | None
        Extracted module record, or None when the file is absent from the base or extraction failed.

    """

    relative_path: str
    record: ModuleRecord | None


@dataclass(frozen=True, slots=True)
class _DiagnosticEmissionContext:
    """Mutable target context for adding diagnostics to a function.

    Attributes
    ----------
    config : CheckConfig
        Check configuration used for severity filtering.
    file : str
        Target file path for the emitted diagnostics.
    func : FunctionRecord
        Function record being diagnosed.
    docstring_sha256 : str
        SHA-256 digest of the function docstring body.
    entries : list[CheckEntry]
        List to which new check entries are appended.
    keys : Counter[DiagnosticKey]
        Counter to which new diagnostic keys are recorded.

    """

    config: CheckConfig
    file: str
    func: FunctionRecord
    visibility: Visibility
    docstring_sha256: str
    entries: list[CheckEntry]
    keys: Counter[DiagnosticKey]


@dataclass(frozen=True, slots=True)
class _ModuleDiagnosticEmissionContext:
    """Target context for adding diagnostics to a module.

    Attributes
    ----------
    config : CheckConfig
        Check configuration used for severity filtering.
    file : str
        Target file path for the emitted diagnostics.
    module : ModuleRecord
        Module record being diagnosed.
    docstring_sha256 : str
        SHA-256 digest of the module docstring body.
    entries : list[CheckEntry]
        List to which new check entries are appended.
    keys : Counter[DiagnosticKey]
        Counter to which new diagnostic keys are recorded.

    """

    config: CheckConfig
    file: str
    module: ModuleRecord
    docstring_sha256: str
    entries: list[CheckEntry]
    keys: Counter[DiagnosticKey]


@dataclass(frozen=True, slots=True)
class _ClassDiagnosticEmissionContext:
    """Target context for adding diagnostics to a class.

    Attributes
    ----------
    config : CheckConfig
        Check configuration used for severity filtering.
    file : str
        Target file path for the emitted diagnostics.
    class_record : ClassRecord
        Class record being diagnosed.
    visibility : Visibility
        Effective visibility of the class, used for attribute codes.
    docstring_sha256 : str
        SHA-256 digest of the class docstring body.
    entries : list[CheckEntry]
        List to which new check entries are appended.
    keys : Counter[DiagnosticKey]
        Counter to which new diagnostic keys are recorded.

    """

    config: CheckConfig
    file: str
    class_record: ClassRecord
    visibility: Visibility
    docstring_sha256: str
    entries: list[CheckEntry]
    keys: Counter[DiagnosticKey]


def produce_diagnostics(
    module_record: ModuleRecord,
    config: CheckConfig,
    *,
    file: str | None = None,
) -> tuple[list[CheckEntry], Counter[DiagnosticKey]]:
    """Run checks on a ModuleRecord and return diagnostics and keys.

    Return a tuple ``(entries, keys)`` where ``entries`` is the list of generated CheckEntry objects (possibly empty) and ``keys``
    is the DiagnosticKey Counter.

    Parameters
    ----------
    module_record : ModuleRecord
        The ModuleRecord instance to analyze and generate diagnostics for.
    config : CheckConfig
        Configuration object that controls which checks are performed and how; it is passed to the underlying diagnostic
        functions, and its symbol_kinds attribute determines whether module- and class-level checks are included.
    file : str | None = None
        Optional source file path to associate with the diagnostics. When supplied, it is passed to the underlying diagnostic
        helpers for each checked symbol; when omitted, the helpers use their default file handling.

    Returns
    -------
    tuple[list[CheckEntry], Counter[DiagnosticKey]]
        A tuple containing a list of CheckEntry objects (possibly empty) and a Counter of DiagnosticKey counts.

    """
    entries: list[CheckEntry] = []
    keys: Counter[DiagnosticKey] = Counter()

    if SymbolKind.MODULE in config.symbol_kinds:
        e, k = diagnostics_for_module(module_record, config, file=file)
        entries.extend(e)
        keys.update(k)

    module_visibility = module_record.visibility
    class_visibilities = {
        method.qualified_name: class_record.visibility
        for class_record in module_record.classes
        for method in class_record.methods
    }
    for func in iter_functions([module_record]):
        class_visibility = class_visibilities.get(func.qualified_name, Visibility.PUBLIC)
        visibility = effective_visibility(module_visibility, effective_visibility(class_visibility, func.visibility))
        e, k = diagnostics_for_function(func, config, file=file, effective_visibility=visibility)
        entries.extend(e)
        keys.update(k)

    if SymbolKind.CLASS in config.symbol_kinds:
        for class_record in module_record.classes:
            visibility = effective_visibility(module_visibility, class_record.visibility)
            e, k = diagnostics_for_class(class_record, config, file=file, effective_visibility=visibility)
            entries.extend(e)
            keys.update(k)

    return entries, keys


def diagnostics_for_module(
    module: ModuleRecord,
    config: CheckConfig,
    *,
    file: str | None = None,
) -> tuple[list[CheckEntry], Counter[DiagnosticKey]]:
    """Run direct C7 checks for a module.

    Parameters
    ----------
    module : ModuleRecord
        The module record to run direct C7 checks on.
    config : CheckConfig
        Configuration for the diagnostic checks, specifying which symbol kinds and visibilities to include and how to determine
        severity for diagnostic codes.
    file : str | None = None
        Optional file path to use as the target file for diagnostics. When provided, this path overrides the module's own file
        path; when omitted, the module's file path is used.

    Returns
    -------
    tuple[list[CheckEntry], Counter[DiagnosticKey]]
        Returns a tuple containing the list of CheckEntry objects produced for the module and a Counter that maps each
        DiagnosticKey to the number of times it was emitted.

    """
    entries: list[CheckEntry] = []
    keys: Counter[DiagnosticKey] = Counter()

    if SymbolKind.MODULE not in config.symbol_kinds:
        return entries, keys
    if module.visibility not in config.include_visibility:
        return entries, keys

    target_file = file or str(module.file_path)
    docstring = (module.existing_docstring or "").strip()
    if docstring:
        parsed = parse_numpy_docstring(docstring)
        emission = _ModuleDiagnosticEmissionContext(
            config=config,
            file=target_file,
            module=module,
            docstring_sha256=_compute_docstring_sha256(module.existing_docstring or ""),
            entries=entries,
            keys=keys,
        )
        _add_missing_module_summary(parsed, emission)
        return entries, keys

    code = _missing_docstring_code(kind=SymbolKind.MODULE, visibility=module.visibility)
    severity = config.severity_for(code, _default_severity(code))
    if _is_severity_disabled(severity):
        return entries, keys

    key = DiagnosticKey(
        code=code,
        file=target_file,
        symbol=module.module_name,
        visibility=module.visibility.value,
    )
    entries.append(
        CheckEntry(
            severity=severity,
            code=code,
            symbol=module.module_name,
            file=target_file,
            line_start=module.line_start,
            col_start=1,
            message=_MESSAGE_MODULE_WITHOUT_DOCSTRING.format(module_name=module.module_name),
            symbol_kind=SymbolKind.MODULE,
            visibility=module.visibility.value,
            line_end=module.line_end or None,
            diagnostic_key=key,
        ),
    )
    keys[key] += 1
    return entries, keys


def _add_missing_module_summary(
    parsed: object,
    emission: _ModuleDiagnosticEmissionContext,
) -> None:
    """Add DMT-7001 when a module docstring has no usable summary.

    Parameters
    ----------
    parsed : object
        The parsed module docstring object whose summary attribute is checked to determine whether a usable summary exists.
    emission : _ModuleDiagnosticEmissionContext
        The diagnostic emission context for the module being checked, providing access to module metadata, configuration,
        docstring hash, and the diagnostic entries list to which the DMT-7001 check entry is appended.

    """
    module = emission.module
    if getattr(parsed, "summary", "").strip():
        return

    severity = emission.config.severity_for(DMT_7001, _default_severity(DMT_7001))
    if _is_severity_disabled(severity):
        return

    key = DiagnosticKey(
        code=DMT_7001,
        file=emission.file,
        symbol=module.module_name,
        section="Summary",
        visibility=module.visibility.value,
    )
    emission.entries.append(
        CheckEntry(
            severity=severity,
            code=DMT_7001,
            symbol=module.module_name,
            file=emission.file,
            line_start=module.line_start,
            col_start=1,
            message=_MESSAGE_MODULE_WITHOUT_SUMMARY.format(module_name=module.module_name),
            symbol_kind=SymbolKind.MODULE,
            docstring_line=module.docstring_line_start,
            annotation_line=module.docstring_line_start,
            docstring_sha256=emission.docstring_sha256,
            visibility=module.visibility.value,
            line_end=module.line_end or None,
            diagnostic_key=key,
        ),
    )
    emission.keys[key] += 1


def _add_missing_class_init_parameters(
    parsed: object,
    emission: _ClassDiagnosticEmissionContext,
) -> None:
    """Add DMT-2001 for __init__ parameters missing from the class docstring.

    Parameters
    ----------
    parsed : object
        The parsed docstring object from which the Parameters section is read.
    emission : _ClassDiagnosticEmissionContext
        The emission context used to accumulate diagnostics for the class. It provides access to the class record, configuration,
        and the output collections to which missing __init__ parameter findings are appended.

    """
    class_record = emission.class_record
    init_method = next((method for method in class_record.methods if method.qualified_name.endswith(".__init__")), None)
    if init_method is None or init_method.signature is None:
        return

    documented = {param.name for param in parse_parameters(find_section(parsed, "Parameters"))}
    for parameter in init_method.signature.parameters:
        if parameter.name in {"self", "cls"} or parameter.name in documented:
            continue

        severity = emission.config.severity_for(DMT_2001, _default_severity(DMT_2001))
        if _is_severity_disabled(severity):
            continue

        key = DiagnosticKey(
            code=DMT_2001,
            file=emission.file,
            symbol=class_record.qualified_name,
            section="Parameters",
            parameter_name=parameter.name,
            expected=parameter.name,
            visibility=emission.visibility.value,
        )
        emission.entries.append(
            CheckEntry(
                severity=severity,
                code=DMT_2001,
                symbol=class_record.qualified_name,
                file=emission.file,
                line_start=class_record.line_start,
                col_start=1,
                message=f"__init__ parameter is not documented: {parameter.name}",
                symbol_kind=SymbolKind.CLASS,
                visibility=emission.visibility.value,
                line_end=class_record.line_end,
                col_end=class_record.col_end,
                docstring_line=class_record.docstring_line_start,
                annotation_line=parameter.line_start,
                docstring_sha256=emission.docstring_sha256,
                diagnostic_key=key,
            ),
        )
        emission.keys[key] += 1


def _add_missing_class_attributes(
    parsed: object,
    emission: _ClassDiagnosticEmissionContext,
) -> None:
    """Add the field code (125 plus class visibility) for public attributes missing from Attributes.

    Parameters
    ----------
    parsed : object
        An object representing the parsed docstring content from which the Attributes section is extracted.
    emission : _ClassDiagnosticEmissionContext
        The diagnostic emission context for the current class, providing access to the class record, visibility, configuration,
        and the output entries and keys being accumulated.

    """
    class_record = emission.class_record
    documented = _documented_attribute_names(find_section(parsed, "Attributes"))
    code = f"DMT-{1150 + _VISIBILITY_OFFSETS[emission.visibility]}"
    for attribute in _deduplicated_public_attributes(class_record):
        if attribute in documented:
            continue

        severity = emission.config.severity_for(code, _default_severity(code))
        if _is_severity_disabled(severity):
            continue

        key = DiagnosticKey(
            code=code,
            file=emission.file,
            symbol=class_record.qualified_name,
            section="Attributes",
            expected=attribute,
            visibility=emission.visibility.value,
            owner_symbol=class_record.qualified_name,
            attribute_name=attribute,
        )
        emission.entries.append(
            CheckEntry(
                severity=severity,
                code=code,
                symbol=class_record.qualified_name,
                file=emission.file,
                line_start=class_record.line_start,
                col_start=1,
                message=f"Public attribute is not documented in Attributes: {attribute}",
                symbol_kind=SymbolKind.CLASS,
                visibility=emission.visibility.value,
                owner_symbol=class_record.qualified_name,
                attribute_name=attribute,
                line_end=class_record.line_end,
                col_end=class_record.col_end,
                docstring_line=class_record.docstring_line_start,
                annotation_line=class_record.line_start,
                section="Attributes",
                docstring_sha256=emission.docstring_sha256,
                diagnostic_key=key,
            ),
        )
        emission.keys[key] += 1


def _add_missing_class_summary(
    parsed: object,
    emission: _ClassDiagnosticEmissionContext,
) -> None:
    """Add DMT-7001 when a class docstring has no usable summary.

    Parameters
    ----------
    parsed : object
        The parsed docstring object to inspect for a usable summary. It is expected to expose a summary attribute; when that
        attribute is missing or blank, the diagnostic is emitted.
    emission : _ClassDiagnosticEmissionContext
        Emission context for the class being checked; supplies the class record, configuration, file path, docstring hash, and the
        collections to which the DMT-7001 diagnostic entry and key are appended.

    """
    class_record = emission.class_record
    if getattr(parsed, "summary", "").strip():
        return

    severity = emission.config.severity_for(DMT_7001, _default_severity(DMT_7001))
    if _is_severity_disabled(severity):
        return

    key = DiagnosticKey(
        code=DMT_7001,
        file=emission.file,
        symbol=class_record.qualified_name,
        section="Summary",
        visibility=emission.visibility.value,
    )
    emission.entries.append(
        CheckEntry(
            severity=severity,
            code=DMT_7001,
            symbol=class_record.qualified_name,
            file=emission.file,
            line_start=class_record.line_start,
            col_start=1,
            message=_MESSAGE_CLASS_WITHOUT_SUMMARY.format(qualified_name=class_record.qualified_name),
            symbol_kind=SymbolKind.CLASS,
            docstring_line=class_record.docstring_line_start,
            annotation_line=class_record.docstring_line_start,
            docstring_sha256=emission.docstring_sha256,
            visibility=emission.visibility.value,
            line_end=class_record.line_end,
            col_end=class_record.col_end,
            diagnostic_key=key,
        ),
    )
    emission.keys[key] += 1


def _add_missing_class_method_exceptions(
    parsed: object,
    emission: _ClassDiagnosticEmissionContext,
) -> None:
    """Add DMT-4301 for exposed method exceptions missing from class Raises.

    Parameters
    ----------
    parsed : object
        The parsed docstring object to inspect for documented exceptions in the Raises section.
    emission : _ClassDiagnosticEmissionContext
        The diagnostic emission context for the class being checked, providing configuration, class record, output entries, and
        key tracking needed to emit DMT-4301 diagnostics.

    """
    if emission.config.method_exception_contract is not MethodExceptionContract.CLASS_AGGREGATE:
        return

    documented = {_simple_exception_name(raise_.exception) for raise_ in parse_raises(find_section(parsed, "Raises"))}
    for exception in _class_exceptions_to_document(emission):
        normalized_name = _simple_exception_name(exception.exception_type)
        if normalized_name in documented:
            continue

        severity = emission.config.severity_for(DMT_4301, _default_severity(DMT_4301))
        if _is_severity_disabled(severity):
            continue

        key = DiagnosticKey(
            code=DMT_4301,
            file=emission.file,
            symbol=emission.class_record.qualified_name,
            section="Raises",
            expected=normalized_name,
            visibility=emission.visibility.value,
        )
        emission.entries.append(
            CheckEntry(
                severity=severity,
                code=DMT_4301,
                symbol=emission.class_record.qualified_name,
                file=emission.file,
                line_start=emission.class_record.line_start,
                col_start=1,
                message=_MESSAGE_UNDOCUMENTED_CLASS_EXCEPTION.format(exception_type=exception.exception_type),
                symbol_kind=SymbolKind.CLASS,
                docstring_line=emission.class_record.docstring_line_start,
                annotation_line=exception.raise_line,
                section="Raises",
                expected=exception.exception_type,
                granularity="entry",
                error_type=VerificationErrorType.RAISES_MISSING.value,
                docstring_sha256=emission.docstring_sha256,
                visibility=emission.visibility.value,
                line_end=emission.class_record.line_end,
                col_end=emission.class_record.col_end,
                diagnostic_key=key,
            ),
        )
        emission.keys[key] += 1


def _class_exceptions_to_document(emission: _ClassDiagnosticEmissionContext) -> list[ExceptionRecord]:
    """Return unique exposed local exceptions from covered class methods.

    Parameters
    ----------
    emission : _ClassDiagnosticEmissionContext
        Diagnostic emission context that supplies the class record and configuration used to collect local exceptions from visible
        class methods.

    Returns
    -------
    list[ExceptionRecord]
        Return a list of ExceptionRecord objects for exceptions raised locally by visible methods of the class, excluding
        exceptions propagated through callgraph analysis.

    """
    if SymbolKind.METHOD not in emission.config.symbol_kinds:
        return []

    exceptions: list[ExceptionRecord] = []
    seen: set[str] = set()
    for method in emission.class_record.methods:
        visibility = effective_visibility(emission.visibility, method.visibility)
        if visibility not in emission.config.include_visibility:
            continue
        accessor_kind = _property_accessor_kind(method)
        if accessor_kind is not None and accessor_kind not in {accessor.value for accessor in emission.config.property_accessors}:
            continue
        for exception in method.exceptions or []:
            if exception.provenance is Provenance.CALLGRAPH_PROPAGATION:
                continue
            if not exception_escapes(method, exception):
                continue
            normalized_name = _simple_exception_name(exception.exception_type)
            if normalized_name in seen:
                continue
            seen.add(normalized_name)
            exceptions.append(exception)
    return exceptions


def _simple_exception_name(name: str) -> str:
    """Return an exception's simple name without its module qualification.

    Parameters
    ----------
    name : str
        The exception name, possibly module-qualified, from which to extract the simple name.

    Returns
    -------
    str
        The simple name of the exception, with any module qualification removed and converted to lowercase.

    """
    return name.rsplit(".", maxsplit=1)[-1].lower()


def _documented_attribute_names(lines: tuple[str, ...]) -> set[str]:
    """Extract documented names from a NumPy Attributes section.

    Parameters
    ----------
    lines : tuple[str, ...]
        Lines of the NumPy Attributes section to scan for documented attribute names.

    Returns
    -------
    set[str]
        Return a set containing the documented attribute names found in the provided lines of a NumPy Attributes section.
        Non-indented lines are treated as attribute entries, with each name taken from the text before the first colon and
        stripped of surrounding whitespace; blank and indented lines are ignored.

    """
    names: set[str] = set()
    for line in lines:
        if not line or line[0] == " ":
            continue
        name = line.split(":", 1)[0].strip()
        if name:
            names.add(name)
    return names


def _deduplicated_public_attributes(class_record: ClassRecord) -> list[str]:
    """Return public class attributes while preserving M1 order.

    Parameters
    ----------
    class_record : ClassRecord
        The class record whose public attributes are to be extracted and deduplicated, preserving M1 order.

    Returns
    -------
    list[str]
        Returns a list of public attribute names, deduplicated and preserving the original M1 order.

    """
    attributes: list[str] = []
    seen: set[str] = set()
    for attribute in [*class_record.class_attributes, *class_record.instance_attributes]:
        if attribute.startswith("_") or attribute in seen:
            continue
        seen.add(attribute)
        attributes.append(attribute)
    return attributes


def diagnostics_for_class(
    class_record: ClassRecord,
    config: CheckConfig,
    *,
    file: str | None = None,
    effective_visibility: Visibility | None = None,
) -> tuple[list[CheckEntry], Counter[DiagnosticKey]]:
    """Run direct C7 checks for a class.

    Parameters
    ----------
    class_record : ClassRecord
        The class record for the class being diagnosed, providing metadata such as its qualified name, file path, visibility,
        existing docstring, and source line information.
    config : CheckConfig
        Configuration for the diagnostic checks, specifying which symbol kinds and visibilities to include and how severities are
        assigned.
    file : str | None = None
        Optional path to the file to associate with diagnostics. When provided, it overrides the class record's file path as the
        reported file for generated check entries and diagnostic keys.
    effective_visibility : Visibility | None = None
        An optional visibility value that overrides the visibility derived from the class record when running checks. If not
        provided, the class record's own visibility is used.

    Returns
    -------
    tuple[list[CheckEntry], Counter[DiagnosticKey]]
        A tuple containing a list of CheckEntry objects for each diagnostic emitted for the class, and a Counter that maps
        DiagnosticKey instances to the number of times each diagnostic was recorded.

    """
    entries: list[CheckEntry] = []
    keys: Counter[DiagnosticKey] = Counter()

    if SymbolKind.CLASS not in config.symbol_kinds:
        return entries, keys
    visibility = effective_visibility or class_record.visibility
    if visibility not in config.include_visibility:
        return entries, keys

    target_file = file or str(class_record.file_path)
    docstring = (class_record.existing_docstring or "").strip()
    emission = _ClassDiagnosticEmissionContext(
        config=config,
        file=target_file,
        class_record=class_record,
        visibility=visibility,
        docstring_sha256=_compute_docstring_sha256(class_record.existing_docstring or ""),
        entries=entries,
        keys=keys,
    )
    if docstring:
        parsed = parse_numpy_docstring(docstring)
        _add_missing_class_init_parameters(parsed, emission)
        _add_missing_class_attributes(parsed, emission)
        _add_missing_class_summary(parsed, emission)
        _add_missing_class_method_exceptions(parsed, emission)
        return entries, keys

    code = _missing_docstring_code(kind=SymbolKind.CLASS, visibility=visibility)
    severity = config.severity_for(code, _default_severity(code))
    if _is_severity_disabled(severity):
        _add_missing_class_method_exceptions(parse_numpy_docstring(""), emission)
        return entries, keys

    key = DiagnosticKey(
        code=code,
        file=target_file,
        symbol=class_record.qualified_name,
        visibility=visibility.value,
    )
    entries.append(
        CheckEntry(
            severity=severity,
            code=code,
            symbol=class_record.qualified_name,
            file=target_file,
            line_start=class_record.line_start,
            col_start=1,
            message=_MESSAGE_CLASS_WITHOUT_DOCSTRING.format(qualified_name=class_record.qualified_name),
            symbol_kind=SymbolKind.CLASS,
            visibility=visibility.value,
            line_end=class_record.line_end,
            col_end=class_record.col_end,
            docstring_line=class_record.docstring_line_start,
            diagnostic_key=key,
        ),
    )
    keys[key] += 1
    _add_missing_class_method_exceptions(parse_numpy_docstring(""), emission)
    return entries, keys


def diagnostics_for_function(
    func: FunctionRecord,
    config: CheckConfig,
    *,
    file: str | None = None,
    effective_visibility: Visibility | None = None,
) -> tuple[list[CheckEntry], Counter[DiagnosticKey]]:
    """Run checks on one function and return diagnostics and keys.

    Parameters
    ----------
    func : FunctionRecord
        The FunctionRecord representing the function whose documentation is to be checked.
    config : CheckConfig
        Configuration for the diagnostic checks, controlling which symbol kinds and visibilities are included, severity mapping,
        and forbidden terms.
    file : str | None = None
        Optional keyword-only argument that overrides the file path used for diagnostics and diagnostic keys. When omitted, the
        function's own file path is used. Must be a string or None.
    effective_visibility : Visibility | None = None
        Optional override for the function's visibility. When provided, this value is used instead of the function's own
        visibility when checking whether the function should be included based on the configured visibility filters. Defaults to
        None, meaning the function's actual visibility is used.

    Returns
    -------
    tuple[list[CheckEntry], Counter[DiagnosticKey]]
        A tuple containing a list of CheckEntry objects representing the diagnostics produced for the function, and a Counter that
        maps each DiagnosticKey to the number of times it was recorded.

    """
    entries: list[CheckEntry] = []
    keys: Counter[DiagnosticKey] = Counter()

    symbol_kind = _function_symbol_kind(func)
    if symbol_kind not in config.symbol_kinds:
        return entries, keys

    visibility = effective_visibility or func.visibility
    if visibility not in config.include_visibility:
        return entries, keys

    accessor_kind = _property_accessor_kind(func)
    if accessor_kind is not None and accessor_kind not in {accessor.value for accessor in config.property_accessors}:
        return entries, keys

    target_file = file or str(func.file_path)

    docstring = (func.existing_docstring or "").strip()
    if not docstring:
        code = _missing_docstring_code(kind=symbol_kind, visibility=visibility, method_type=func.method_kind)
        severity = config.severity_for(code, _default_severity(code))
        if not _is_severity_disabled(severity):
            key = DiagnosticKey(
                code=code,
                file=target_file,
                symbol=func.qualified_name,
                accessor_kind=accessor_kind,
                visibility=visibility.value,
            )
            message = (
                _MESSAGE_METHOD_WITHOUT_DOCSTRING.format(qualified_name=func.qualified_name)
                if symbol_kind is SymbolKind.METHOD
                else _MESSAGE_FUNCTION_WITHOUT_DOCSTRING.format(qualified_name=func.qualified_name)
            )
            entries.append(
                CheckEntry(
                    severity=severity,
                    code=code,
                    symbol=func.qualified_name,
                    file=target_file,
                    line_start=func.line_start,
                    col_start=1,
                    message=message,
                    symbol_kind=symbol_kind,
                    accessor_kind=accessor_kind,
                    visibility=visibility.value,
                    line_end=func.line_end,
                    col_end=func.col_end,
                    diagnostic_key=key,
                ),
            )
            keys[key] += 1

    parsed = parse_numpy_docstring(docstring)
    parameters = parse_parameters(find_section(parsed, "Parameters"))
    returns = parse_return(find_section(parsed, "Returns"))
    raises = parse_raises(find_section(parsed, "Raises"))

    generated = GeneratedDocstring(
        summary=parsed.summary.strip() or "(no summary)",
        parameters=parameters,
        returns=returns,
        raises=raises,
    )

    request = VerificationRequest(
        generated_docstring=generated,
        function_record=func,
        forbidden_terms=config.forbidden_terms,
    )
    response = verify_docstring(request)
    emission = _DiagnosticEmissionContext(
        config=config,
        file=target_file,
        func=func,
        visibility=visibility,
        docstring_sha256=_compute_docstring_sha256(func.existing_docstring or ""),
        entries=entries,
        keys=keys,
    )
    corrected_sections = _corrected_sections_from_warnings(response.warnings)
    _add_missing_return_section(
        _return_type_to_document(func),
        returns_absent=returns is None,
        emission=emission,
        corrected_sections=corrected_sections,
    )
    verification_errors = response.errors
    if _class_aggregate_exception_contract(func, config):
        verification_errors = tuple(
            error
            for error in verification_errors
            if not (error.error_type == VerificationErrorType.RAISES_MISSING and error.dmt_code == DMT_4001)
        )
    _add_verification_errors(verification_errors, emission, corrected_sections)
    _add_format_warnings(response.warnings, emission)

    return entries, keys


def _add_missing_return_section(
    return_type: TypeInfo | None,
    *,
    returns_absent: bool,
    emission: _DiagnosticEmissionContext,
    corrected_sections: frozenset[str],
) -> None:
    """Add DMT-3001 when a missing Returns section is not caused by a title typo.

    Parameters
    ----------
    return_type : TypeInfo | None
        The resolved return type annotation of the function, or None if the function has no annotated return type. Used to
        determine whether a missing Returns section should be reported and to populate the expected type in the diagnostic.
    returns_absent : bool
        Indicates whether the docstring lacks a Returns section.
    emission : _DiagnosticEmissionContext
        Diagnostic emission context providing access to configuration, file and function metadata, docstring hash, and the
        collections used to record emitted diagnostics.
    corrected_sections : frozenset[str]
        A frozenset of section names that were corrected in the docstring, used to determine whether the missing Returns
        diagnostic should be masked by a prior title correction.

    """
    if not returns_absent or return_type is None:
        return

    if _downstream_diagnostic_masked(DMT_3001, "Returns", corrected_sections):
        return

    severity = emission.config.severity_for(DMT_3001, _default_severity(DMT_3001))
    if _is_severity_disabled(severity):
        return

    key = DiagnosticKey(
        code=DMT_3001,
        file=emission.file,
        symbol=emission.func.qualified_name,
        section="Returns",
        expected=return_type.type_str,
        accessor_kind=_property_accessor_kind(emission.func),
        visibility=emission.visibility.value,
    )
    emission.entries.append(
        CheckEntry(
            severity=severity,
            code=DMT_3001,
            symbol=emission.func.qualified_name,
            file=emission.file,
            line_start=emission.func.line_start,
            col_start=1,
            message=_MESSAGE_MISSING_RETURNS_SECTION.format(type_str=return_type.type_str),
            symbol_kind=_function_symbol_kind(emission.func),
            accessor_kind=_property_accessor_kind(emission.func),
            visibility=emission.visibility.value,
            line_end=emission.func.line_end,
            col_end=emission.func.col_end,
            docstring_line=emission.func.docstring_line_start,
            annotation_line=emission.func.line_start,
            section="Returns",
            expected=return_type.type_str,
            granularity="section",
            error_type="ReturnsMissing",
            docstring_sha256=emission.docstring_sha256,
            diagnostic_key=key,
        ),
    )
    emission.keys[key] += 1


def _add_verification_errors(
    errors: tuple[VerificationError, ...],
    emission: _DiagnosticEmissionContext,
    corrected_sections: frozenset[str],
) -> None:
    """Add enabled Verify structural errors.

    Parameters
    ----------
    errors : tuple[VerificationError, ...]
        Tuple of verification errors to process. Each error is examined for internal types, missing or unknown dmt_code,
        propagated raises, severity, and downstream masking; eligible errors are converted into diagnostic entries.
    emission : _DiagnosticEmissionContext
        Diagnostic emission context that supplies the configuration, target file, function metadata, and output containers used to
        record generated check entries and diagnostic keys.
    corrected_sections : frozenset[str]
        A frozenset of section names that have been corrected, used to determine whether a downstream diagnostic should be masked.

    """
    for error in errors:
        if error.error_type in _INTERNAL_TYPES:
            logger.warning(_MESSAGE_IGNORED_INTERNAL_VERIFY, error.error_type.value)
            continue

        if error.dmt_code is None:
            logger.error("Verify returned no dmt_code: %s - ignored", error.error_type.value)
            continue
        if _is_propagated_raises_missing(error):
            continue
        if error.dmt_code not in KNOWN_CODES:
            logger.error("Verify returned a dmt_code unknown to the registry: %s - ignored", error.dmt_code)
            continue
        default_severity = _default_severity(error.dmt_code)
        severity = emission.config.severity_for(error.dmt_code, default_severity)
        if _is_severity_disabled(severity):
            continue

        if _downstream_diagnostic_masked(error.dmt_code, error.section or None, corrected_sections):
            continue

        key = DiagnosticKey(
            code=error.dmt_code,
            file=emission.file,
            symbol=emission.func.qualified_name,
            section=error.section or None,
            parameter_name=error.parameter_name or None,
            expected=error.expected or None,
            observed=error.observed or None,
            accessor_kind=_property_accessor_kind(emission.func),
            visibility=emission.visibility.value,
        )
        annotation_line = _parameter_line(emission.func, error.parameter_name) if error.parameter_name else None
        emission.entries.append(
            CheckEntry(
                severity=severity,
                code=error.dmt_code,
                symbol=emission.func.qualified_name,
                file=emission.file,
                line_start=emission.func.line_start,
                col_start=1,
                message=error.detail,
                symbol_kind=_function_symbol_kind(emission.func),
                accessor_kind=_property_accessor_kind(emission.func),
                visibility=emission.visibility.value,
                line_end=emission.func.line_end,
                col_end=emission.func.col_end,
                docstring_line=emission.func.docstring_line_start,
                annotation_line=annotation_line,
                section=error.section,
                parameter_name=error.parameter_name,
                expected=error.expected,
                observed=error.observed,
                granularity=error.granularity.value,
                error_type=error.error_type.value,
                docstring_sha256=emission.docstring_sha256,
                diagnostic_key=key,
            ),
        )
        emission.keys[key] += 1


def _add_format_warnings(
    warnings: tuple[FormatWarning, ...],
    emission: _DiagnosticEmissionContext,
) -> None:
    """Add enabled verification format warnings.

    Parameters
    ----------
    warnings : tuple[FormatWarning, ...]
        Tuple of FormatWarning objects representing verification format warnings to consider adding to the diagnostic emission.
        Only warnings whose severity is enabled are actually added.
    emission : _DiagnosticEmissionContext
        The emission context that supplies configuration for severity filtering, receives the generated check entries, and tracks
        diagnostic keys.

    """
    for warning in warnings:
        mapping = CODE_BY_WARNING_KIND.get(warning.kind)
        if mapping is None:
            continue

        code = mapping
        severity = emission.config.severity_for(code, _default_severity(code))
        if _is_severity_disabled(severity):
            continue

        key = DiagnosticKey(
            code=code,
            file=emission.file,
            symbol=emission.func.qualified_name,
            section=warning.section,
            discriminator=getattr(warning, "discriminator", None),
            accessor_kind=_property_accessor_kind(emission.func),
            visibility=emission.visibility.value,
        )
        docstring_line = _format_warning_line(emission.func, warning)
        emission.entries.append(
            CheckEntry(
                severity=severity,
                code=code,
                symbol=emission.func.qualified_name,
                file=emission.file,
                line_start=emission.func.line_start,
                col_start=1,
                message=warning.detail,
                symbol_kind=_function_symbol_kind(emission.func),
                accessor_kind=_property_accessor_kind(emission.func),
                visibility=emission.visibility.value,
                line_end=emission.func.line_end,
                col_end=emission.func.col_end,
                docstring_line=docstring_line,
                annotation_line=docstring_line,
                section=warning.section,
                expected=getattr(warning, "expected", None),
                granularity="span" if _format_warning_span(warning) is not None else None,
                discriminator=getattr(warning, "discriminator", None),
                docstring_span=_format_warning_span(warning),
                docstring_sha256=emission.docstring_sha256,
                diagnostic_key=key,
            ),
        )
        emission.keys[key] += 1


def _compute_docstring_sha256(docstring: str) -> str:
    """Compute a docstring body SHA-256 with normalized line endings.

    Parameters
    ----------
    docstring : str
        The docstring body to compute the SHA-256 digest for.

    Returns
    -------
    str
        The SHA-256 hexadecimal digest of the normalized docstring.

    """
    normalized = docstring.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _is_severity_disabled(severity: Severity) -> bool:
    """Return whether a configured severity disables the diagnostic.

    Parameters
    ----------
    severity : Severity
        The severity value to evaluate to determine if it disables the diagnostic.

    Returns
    -------
    bool
        True if the severity is 'disabled', meaning the diagnostic is disabled; False otherwise.

    """
    return severity == "disabled"


def _function_symbol_kind(func: FunctionRecord) -> SymbolKind:
    """Return the Check kind for an M1 function.

    Parameters
    ----------
    func : FunctionRecord
        The function record to inspect; its parent_class attribute determines whether it is classified as a method or a function.

    Returns
    -------
    SymbolKind
        The SymbolKind for the function: METHOD if it belongs to a class, otherwise FUNCTION.

    """
    return SymbolKind.METHOD if func.parent_class is not None else SymbolKind.FUNCTION


def _class_aggregate_exception_contract(func: FunctionRecord, config: CheckConfig) -> bool:
    """Return whether this method's Raises contract belongs to its class."""
    return (
        func.parent_class is not None
        and SymbolKind.CLASS in config.symbol_kinds
        and config.method_exception_contract is MethodExceptionContract.CLASS_AGGREGATE
    )


def _property_accessor_kind(func: FunctionRecord) -> str | None:
    """Return a stable getter/setter role for a property function."""
    if func.method_kind is not MethodType.PROPERTY:
        return None

    accessor = getattr(func, "property_accessor", None)
    value = getattr(accessor, "value", accessor)
    return value if value in {"getter", "setter"} else "getter"


def _is_propagated_raises_missing(error: VerificationError) -> bool:
    """Return True when the error is a DMT-4001 propagated through the call graph.

    Verify carries provenance so propagated exceptions can be distinguished from local exceptions with the same name.

    Parameters
    ----------
    error : VerificationError
        The VerificationError to inspect for a propagated DMT-4001 raises-missing condition.

    Returns
    -------
    bool
        True if the error is a DMT-4001 propagated through the call graph; False otherwise.

    """
    if error.error_type != VerificationErrorType.RAISES_MISSING:
        return False
    if error.dmt_code != DMT_4001:
        return False
    return error.provenance == "propagated"


def _corrected_sections_from_warnings(warnings: tuple[FormatWarning, ...]) -> frozenset[str]:
    """Return canonical sections represented by misspelled headings.

    Parameters
    ----------
    warnings : tuple[FormatWarning, ...]
        Warnings to scan for misspelled section headings.

    Returns
    -------
    frozenset[str]
        A frozenset of canonical section names for each warning that had a misspelled section heading and resolved to a different
        canonical section; empty if there are none.

    """
    corrected_sections: set[str] = set()
    for warning in warnings:
        if warning.kind != "misspelled_section":
            continue

        section = section_canonical_probable(warning.section)
        if section is not None and section != warning.section:
            corrected_sections.add(section)

    return frozenset(corrected_sections)


def _format_warning_line(func: FunctionRecord, warning: FormatWarning) -> int | None:
    """Return the absolute line of a format warning when known.

    Parameters
    ----------
    func : FunctionRecord
        The function record for which to format the warning line.
    warning : FormatWarning
        The FormatWarning instance used to determine the absolute source line. Its line attribute provides the relative offset
        used to compute the final line number.

    Returns
    -------
    int | None
        The absolute line number of the format warning, or None if the docstring start line is not known.

    """
    if func.docstring_line_start is None:
        return None

    relative_line = getattr(warning, "line", None)
    if relative_line is None:
        return func.docstring_line_start

    return func.docstring_line_start + relative_line - 1


def _format_warning_span(warning: FormatWarning) -> tuple[int, int] | None:
    """Return a valid warning-local span without replacing the symbol span."""
    span = getattr(warning, "span", None)
    start = getattr(span, "start", None)
    end = getattr(span, "end", None)
    if type(start) is not int or type(end) is not int or start < 0 or end < start:
        return None
    return start, end


def _downstream_diagnostic_masked(code: str, section: str | None, corrected_sections: frozenset[str]) -> bool:
    """Return whether a diagnostic is downstream of a corrected section heading.

    Parameters
    ----------
    code : str
        Diagnostic code to check for downstream masking.
    section : str | None
        The heading of the section to check, or None if no section is associated with the diagnostic.
    corrected_sections : frozenset[str]
        A frozenset of section names that have been corrected, used to determine whether a diagnostic is masked because it appears
        downstream of any of these sections.

    Returns
    -------
    bool
        True if the diagnostic is downstream of a corrected section heading; False otherwise.

    """
    if section is None or section not in corrected_sections:
        return False

    return code in _DOWNSTREAM_DIAGNOSTICS_BY_SECTION.get(section, frozenset())


def _parameter_line(func: FunctionRecord, parameter_name: str) -> int | None:
    """Return a parameter declaration line when extracted by M1.

    Parameters
    ----------
    func : FunctionRecord
        A FunctionRecord object representing the function to inspect for the specified parameter.
    parameter_name : str
        The name of the parameter to search for in the function's signature.

    Returns
    -------
    int | None
        The line number of the parameter declaration for the given parameter name, or None if the function has no signature or the
        parameter is not found.

    """
    if func.signature is None:
        return None

    for parameter in func.signature.parameters:
        if parameter.name == parameter_name:
            return parameter.line_start

    return None


def diagnostics_for_file(
    content: str,
    file: Path,
    project_root: Path,
    config: CheckConfig,
) -> tuple[list[CheckEntry], Counter[DiagnosticKey]]:
    """Extract diagnostics from file content.

    The content is written to a temporary directory to reuse M1, which extracts
    a ``ModuleRecord`` from a ``Path``. The analysis therefore runs on an
    isolated content snapshot while retaining only the relative path for the
    module name and diagnostics.

    Parameters
    ----------
    content : str
        Source content of the file to analyze.
    file : Path
        Path of the file, used to compute the relative path.
    project_root : Path
        Root directory of the project the file belongs to.
    config : CheckConfig
        Configuration controlling which diagnostics are produced.

    Returns
    -------
    tuple[list[CheckEntry], Counter[DiagnosticKey]]
        A tuple containing the list of CheckEntry diagnostics and the Counter of DiagnosticKey counts; empty collections when the
        file is outside the project root or cannot be analyzed.

    Notes
    -----
    Isolation in a temporary directory means detections that depend on nearby
    files (``py.typed``, ``conftest.py``, and so on) can differ from analysis of
    the real files.

    """
    relative_path = _relative_path_or_none(file, project_root)
    if relative_path is None:
        logger.warning("File ignored outside project root: %s", file)
        return [], Counter()

    record = _extract_module_record_snapshot(content, relative_path)
    if record is None:
        return [], Counter()

    return produce_diagnostics(record, config, file=relative_path)


def base_diagnostics(
    base_rev: str,
    file: Path,
    project_root: Path,
    config: CheckConfig,
    *,
    snapshot_cache: dict[tuple[str, Path], BaseSnapshot | None] | None = None,
) -> tuple[list[CheckEntry], Counter[DiagnosticKey]] | None:
    """Extract diagnostics from the base revision.

    Parameters
    ----------
    base_rev : str
        The base revision to extract diagnostics from.
    file : Path
        Path to the target file to extract diagnostics for from the base revision.
    project_root : Path
        Root directory of the project, used to resolve the file path and compute relative paths for diagnostics.
    config : CheckConfig
        Configuration object that controls how diagnostics are extracted from the base revision.
    snapshot_cache : dict[tuple[str, Path], BaseSnapshot | None] | None = None
        Optional cache mapping a revision and file path to a BaseSnapshot or None, used to reuse previously computed base
        snapshots for the same revision and file across multiple calls.

    Returns
    -------
    tuple[list[CheckEntry], Counter[DiagnosticKey]] | None
        Returns a tuple containing a list of CheckEntry diagnostics and a Counter of DiagnosticKey counts when diagnostics can be
        produced from the base revision. Returns None if the base snapshot is unavailable. If the snapshot has no record, returns
        an empty list and an empty Counter.

    """
    snapshot = _base_snapshot_or_none(
        base_rev,
        file,
        project_root,
        log_context="Base",
        snapshot_cache=snapshot_cache,
    )
    if snapshot is None:
        return None
    if snapshot.record is None:
        return [], Counter()
    return produce_diagnostics(snapshot.record, config, file=snapshot.relative_path)


def base_functions(
    base_rev: str,
    file: Path,
    project_root: Path,
    *,
    snapshot_cache: dict[tuple[str, Path], BaseSnapshot | None] | None = None,
) -> list[FunctionRecord] | None:
    """Extract functions from a file at a base revision.

    Parameters
    ----------
    base_rev : str
        The base revision (e.g., a Git commit, branch, or tag) from which to extract the file's functions.
    file : Path
        Path to the file from which to extract functions.
    project_root : Path
        Root directory of the project, used to resolve the file path and locate repository metadata.
    snapshot_cache : dict[tuple[str, Path], BaseSnapshot | None] | None = None
        Optional cache mapping (revision, file path) tuples to a BaseSnapshot instance or None, allowing reuse of previously
        loaded snapshots for the same revision and file to avoid redundant work.

    Returns
    -------
    list[FunctionRecord] | None
        Returns a list of FunctionRecord objects representing the functions extracted from the file at the base revision, or None
        if the base snapshot cannot be obtained.

    """
    snapshot = _base_snapshot_or_none(
        base_rev,
        file,
        project_root,
        log_context="Base functions",
        snapshot_cache=snapshot_cache,
    )
    if snapshot is None:
        return None
    if snapshot.record is None:
        return []
    return list(iter_functions([snapshot.record]))


def base_classes(
    base_rev: str,
    file: Path,
    project_root: Path,
    *,
    snapshot_cache: dict[tuple[str, Path], BaseSnapshot | None] | None = None,
) -> list[ClassRecord] | None:
    """Extract classes from a file at a base revision.

    Parameters
    ----------
    base_rev : str
        The revision identifier of the base version to load the file from.
    file : Path
        Path to the file from which to extract classes.
    project_root : Path
        Root directory of the project, used to resolve relative paths and locate version-control metadata.
    snapshot_cache : dict[tuple[str, Path], BaseSnapshot | None] | None = None
        An optional cache mapping (base revision, file path) pairs to previously loaded BaseSnapshot objects or None, used to
        avoid reloading snapshots when the same base revision and file are requested again. If not provided, a fresh cache is
        created for the call.

    Returns
    -------
    list[ClassRecord] | None
        A list of class records extracted from the file at the base revision, or None if the base snapshot could not be
        determined; an empty list when the snapshot contains no class records.

    """
    snapshot = _base_snapshot_or_none(
        base_rev,
        file,
        project_root,
        log_context="Base classes",
        snapshot_cache=snapshot_cache,
    )
    if snapshot is None:
        return None
    if snapshot.record is None:
        return []
    return list(snapshot.record.classes)


def _base_snapshot_or_none(
    base_rev: str,
    file: Path,
    project_root: Path,
    *,
    log_context: str,
    snapshot_cache: dict[tuple[str, Path], BaseSnapshot | None] | None = None,
) -> BaseSnapshot | None:
    """Return a base AST snapshot, using the supplied cache when present.

    Parameters
    ----------
    base_rev : str
        The revision identifier used to retrieve the base version of the file for snapshot comparison.
    file : Path
        Path to the file for which to obtain a base AST snapshot.
    project_root : Path
        Root directory of the project, used to resolve the file path to a relative path and to locate base content for the
        specified revision.
    log_context : str
        A string used to identify the operation in log messages, providing context for warnings and diagnostics.
    snapshot_cache : dict[tuple[str, Path], BaseSnapshot | None] | None = None
        Mapping used to cache base snapshots keyed by base revision and resolved file path; when provided, the function checks it
        before computing and stores results in it. If None, no caching is performed.

    Returns
    -------
    BaseSnapshot | None
        A BaseSnapshot for the file at the specified base revision, or None if the snapshot cannot be created or retrieved (for
        example, when the file is outside the project root or the base content is unavailable).

    """
    cache_key = (base_rev, file.resolve())
    if snapshot_cache is not None and cache_key in snapshot_cache:
        return snapshot_cache[cache_key]

    relative_path = _relative_path_or_none(file, project_root)
    if relative_path is None:
        logger.warning("%s ignored for file outside project root: %s", log_context, file)
        snapshot = None
    else:
        content = _base_content_or_none(base_rev, relative_path, project_root)
        if content is None and _base_path_is_absent(base_rev, relative_path, project_root):
            snapshot = BaseSnapshot(relative_path=relative_path, record=None)
        else:
            snapshot = (
                None
                if content is None
                else BaseSnapshot(relative_path=relative_path, record=_extract_module_record_snapshot(content, relative_path))
            )

    if snapshot_cache is not None:
        snapshot_cache[cache_key] = snapshot
    return snapshot


def _base_content_or_none(base_rev: str, relative_path: str, project_root: Path) -> str | None:
    """Return Git file content at a base revision, or None.

    Parameters
    ----------
    base_rev : str
        The Git revision (e.g., commit hash, branch, or tag) from which to retrieve the file content.
    relative_path : str
        Relative path of the file to retrieve from the base revision, resolved against the project root.
    project_root : Path
        The project root directory, used as the working directory when executing Git commands.

    Returns
    -------
    str | None
        The Git file content at the specified base revision as a string, or None if the content could not be retrieved.

    """
    try:
        git = git_executable()
        return subprocess.run(  # noqa: S603
            [git, "show", f"{base_rev}:{relative_path}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_SHOW_TIMEOUT_SECONDS,
            cwd=project_root,
        ).stdout
    except subprocess.TimeoutExpired:
        logger.warning("git show timed out (%ss) for %s:%s", _GIT_SHOW_TIMEOUT_SECONDS, base_rev, relative_path)
        return None
    except (subprocess.CalledProcessError, OSError):
        return None


def _base_path_is_absent(base_rev: str, relative_path: str, project_root: Path) -> bool:
    """Return whether a path is absent from an otherwise available base revision.

    Parameters
    ----------
    base_rev : str
        The revision to inspect.
    relative_path : str
        Project-relative path to look up in the revision.
    project_root : Path
        Root directory used as the Git working directory.

    Returns
    -------
    bool
        True when Git can inspect the revision and the path is absent; False when the path exists or the lookup is inconclusive.

    """
    try:
        git = git_executable()
        result = subprocess.run(  # noqa: S603
            [git, "ls-tree", "-r", "--name-only", base_rev, "--", relative_path],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_SHOW_TIMEOUT_SECONDS,
            cwd=project_root,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and not result.stdout.strip()


def _extract_module_record_snapshot(content: str, relative_path: str) -> ModuleRecord | None:
    """Extract a ModuleRecord from an isolated content snapshot.

    Parameters
    ----------
    content : str
        The source code text to be written into the temporary file for module extraction.
    relative_path : str
        Relative path of the module within the isolated snapshot; it determines the temporary file location used to resolve the
        module name and classification.

    Returns
    -------
    ModuleRecord | None
        The extracted ModuleRecord for the isolated content snapshot, or None if no module record could be extracted.

    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        tmp_path = tmp_root / relative_path
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(content, encoding="utf-8")

        module_name = resolve_module_name(tmp_path, tmp_root)
        return extract_module_record(
            tmp_path,
            module_name,
            is_stub=is_stub_file(tmp_path),
            is_test=is_test_file(tmp_path, tmp_root),
            file_hash=_HASH_NOT_APPLICABLE,
        )


def _return_type_to_document(func: FunctionRecord) -> TypeInfo | None:
    """Return the explicit return type that should appear in the docstring.

    Parameters
    ----------
    func : FunctionRecord
        The function record to inspect for an explicit return type to include in the docstring.

    Returns
    -------
    TypeInfo | None
        Returns the explicit return type from the function record that should be documented, or None if there is no return type,
        the type is not explicitly annotated, or the type is None/NoneType.

    """
    if func.types is None or func.types.return_type is None:
        return None
    return_type = func.types.return_type
    if return_type.confidence is not Confidence.EXPLICIT:
        return None
    if return_type.type_str in {"None", "NoneType"}:
        return None
    return return_type


def _relative_path_or_none(file: Path, project_root: Path) -> str | None:
    """Return the project-relative path, or None when the file is outside the root.

    Parameters
    ----------
    file : Path
        The file to evaluate; a pathlib.Path representing the file whose project-relative path should be computed.
    project_root : Path
        Root directory used to compute the relative path for the given file.

    Returns
    -------
    str | None
        The project-relative path as a POSIX-style string, or None if the file is outside the project root.

    """
    try:
        return file.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return None
