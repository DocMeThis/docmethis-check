# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""OSS configuration for the check module."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from docmethis_extract_python.api import PropertyAccessor, Visibility
from docmethis_verify.validation import (
    OptionalStr,
    StrictBool,
    StrictListOfStrictStr,
    dynamic_type_check,
    normalize_str_enum_value,
    type_check,
)
from docmethis_verify.verification.forbidden_terms import load_forbidden_terms

from docmethis_check.models import AnnotationPlacement, CheckMode, MethodExceptionContract, OnMissingBase, SymbolKind

if TYPE_CHECKING:
    from docmethis_check.models import Severity

__all__ = [
    "CheckConfig",
    "load_check_config",
    "parse_include_visibility",
    "parse_path_filters",
    "parse_property_accessors",
    "parse_symbol_kinds",
    "path_matches_filters",
]

_SECTION_CHECK = ("tool", "docmethis", "check")
_CHECK_OPTION_KEYS = frozenset(
    {
        "annotation-placement",
        "base-ref",
        "check-mode",
        "dia",
        "dia-exclude-paths",
        "exclude-paths",
        "fail-on-warning",
        "include-visibility",
        "method-exception-contract",
        "on-missing-base",
        "on-nonlinear-push-without-base",
        "profile",
        "property-accessors",
        "severity",
        "symbol-kinds",
    }
)
_VISIBILITIES_BY_NAME = {visibility.value: visibility for visibility in Visibility}
_VALID_SEVERITIES = {"error", "warning", "disabled"}
_FORBIDDEN_TERMS_FILE = Path("config/forbidden_terms.txt")
_PROFILE_RESOURCE = ("defaults", "severity_profiles.toml")


def _load_profiles() -> dict[str, dict[str, str]]:
    """Load the non-secret severity policies shipped with the Check package.

    Returns
    -------
    dict[str, dict[str, str]]
        A dictionary mapping profile names to dictionaries that map check codes to severity strings.

    Raises
    ------
    ValueError
        Explicitly raised.
    TypeError
        Explicitly raised.

    """
    resource = resources.files("docmethis_check").joinpath(*_PROFILE_RESOURCE)
    with resource.open("rb") as file_handle:
        parsed = tomllib.load(file_handle)

    profiles: dict[str, dict[str, str]] = {}
    for profile, values in parsed.items():
        if not isinstance(profile, str) or not isinstance(values, dict):
            msg = "Invalid built-in Check profile"
            raise TypeError(msg)
        normalized: dict[str, str] = {}
        for code, severity in values.items():
            if not isinstance(code, str) or not isinstance(severity, str) or severity not in _VALID_SEVERITIES:
                msg = "Invalid built-in Check profile"
                raise ValueError(msg)
            normalized[code] = severity
        profiles[profile] = normalized
    return profiles


_PROFILES = _load_profiles()


def _normalize_profile(value: object) -> str:
    """Validate one named built-in severity policy.

    Parameters
    ----------
    value : object
        The profile name to validate. It must be a non-empty string and one of the available built-in severity profiles.

    Returns
    -------
    str
        The normalized profile string, stripped of surrounding whitespace, after confirming it is a valid built-in severity
        policy.

    Raises
    ------
    ValueError
        Explicitly raised.

    """
    if not isinstance(value, str) or not value.strip():
        msg = "profile must be a non-empty string"
        raise ValueError(msg)
    profile = value.strip()
    if profile not in _PROFILES:
        available = ", ".join(sorted(_PROFILES))
        msg = f"Unknown profile: {profile!r}. Available profiles: {available}"
        raise ValueError(msg)
    return profile


def _normalize_path_filters(value: object, *, field: str) -> tuple[str, ...]:
    """Normalize project-relative exact paths and directory prefixes.

    Parameters
    ----------
    value : object
        Path filter values loaded from configuration or supplied directly.
    field : str
        Configuration field name used in validation errors.

    Returns
    -------
    tuple[str, ...]
        Deduplicated normalized path filters.

    Raises
    ------
    TypeError
        If the value is not a list or tuple of strings.
    ValueError
        If a path is empty, absolute, unsafe, or uses glob syntax.

    """
    if not isinstance(value, (list, tuple)):
        msg = f"{field} must be a list of strings."
        raise TypeError(msg)

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            msg = f"{field} must contain only strings."
            raise TypeError(msg)
        path = item.strip().replace("\\", "/")
        if not path or path == ".":
            msg = f"{field} must not contain empty or current-directory paths."
            raise ValueError(msg)
        if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
            msg = f"{field} must contain paths relative to the project root."
            raise ValueError(msg)
        if any(character in path for character in "*?["):
            msg = f"{field} does not support glob patterns: {item!r}"
            raise ValueError(msg)

        pure_path = PurePosixPath(path)
        if ".." in pure_path.parts:
            msg = f"{field} must not contain parent-directory components: {item!r}"
            raise ValueError(msg)
        normalized.append(pure_path.as_posix().rstrip("/"))

    return tuple(dict.fromkeys(normalized))


def path_matches_filters(path: Path, root: Path, filters: tuple[str, ...]) -> bool:
    """Return whether a path matches an exact filter or a directory prefix.

    Parameters
    ----------
    path : Path
        File path to compare with the configured filters.
    root : Path
        Project root used to make the file path relative.
    filters : tuple[str, ...]
        Normalized project-relative exact paths or directory prefixes.

    Returns
    -------
    bool
        True when the path is excluded by one of the filters.

    """
    if not filters:
        return False
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    return any(relative == item or relative.startswith(f"{item}/") for item in filters)


@dataclass(frozen=True, slots=True)
class CheckConfig:
    """Configurable OSS check policy.

    Attributes
    ----------
    fail_on_warning : bool
        Whether to treat warnings as failures.
    include_visibility : frozenset[Visibility]
        Visibilities included in the check.
    symbol_kinds : frozenset[SymbolKind]
        Symbol kinds included in the check.
    property_accessors : frozenset[PropertyAccessor]
        Property accessors included in the check.
    severity : dict[str, Severity]
        Severity overrides by DMT code.
    profile : str
        Name of the built-in severity policy.
    base_ref : str | None
        Base Git reference used to resolve the diff range.
    on_nonlinear_push_without_base : str
        Behavior when a nonlinear push has no reliable base.
    check_mode : CheckMode
        Check verification mode.
    annotation_placement : AnnotationPlacement
        Where type annotations are placed in generated documentation.
    on_missing_base : OnMissingBase
        Behavior when the base revision is unavailable.
    dia : bool
        Whether diagnostic annotations are enabled.
    forbidden_terms : tuple[str, ...]
        Terms forbidden in the docstring.
    method_exception_contract : MethodExceptionContract
        Documentation owner for exceptions exposed by class methods.
    exclude_paths : tuple[str, ...]
        Project-relative files or directory prefixes excluded from Check and DIA.
    dia_exclude_paths : tuple[str, ...]
        Project-relative files or directory prefixes excluded from DIA only.

    Raises
    ------
    ValueError
        If a configured value is invalid: an empty visibility or symbol kind set, an unknown profile, or an unsupported check
        mode.

    """

    fail_on_warning: bool = False
    include_visibility: frozenset[Visibility] = field(default_factory=lambda: frozenset({Visibility.PUBLIC}))
    symbol_kinds: frozenset[SymbolKind] = field(
        default_factory=lambda: frozenset({SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CLASS, SymbolKind.MODULE})
    )
    property_accessors: frozenset[PropertyAccessor] = field(
        default_factory=lambda: frozenset({PropertyAccessor.GETTER, PropertyAccessor.SETTER})
    )
    severity: dict[str, Severity] = field(default_factory=dict)
    profile: str = "standard"
    base_ref: str | None = None
    on_nonlinear_push_without_base: str = "fail"
    check_mode: CheckMode = CheckMode.REGRESSION
    annotation_placement: AnnotationPlacement = AnnotationPlacement.SIGNATURE
    on_missing_base: OnMissingBase = OnMissingBase.EMIT_ALL
    dia: bool = True
    forbidden_terms: tuple[str, ...] = ()
    method_exception_contract: MethodExceptionContract = MethodExceptionContract.CALLABLE
    exclude_paths: tuple[str, ...] = ()
    dia_exclude_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate the direct Check configuration contract.

        Raises
        ------
        ValueError
            Explicitly raised when a configured value is invalid.

        Notes
        -----
        Path filters are normalized after the scalar and collection settings are validated.

        """
        type_check(value=self.fail_on_warning, expected_alias_type=StrictBool, field="fail_on_warning")
        type_check(value=self.dia, expected_alias_type=StrictBool, field="dia")
        profile = _normalize_profile(self.profile)
        object.__setattr__(self, "profile", profile)
        method_exception_contract = normalize_str_enum_value(
            value=self.method_exception_contract,
            enum_type=MethodExceptionContract,
            field="method_exception_contract",
        )
        object.__setattr__(self, "method_exception_contract", method_exception_contract)
        dynamic_type_check(
            value=self.include_visibility,
            expected_type=Visibility,
            container=frozenset,
            field="include_visibility",
        )
        if not self.include_visibility:
            msg = "include_visibility must contain at least one visibility."
            raise ValueError(msg)

        dynamic_type_check(
            value=self.symbol_kinds,
            expected_type=SymbolKind,
            container=frozenset,
            field="symbol_kinds",
        )
        if not self.symbol_kinds:
            msg = "symbol_kinds must contain at least one symbol kind."
            raise ValueError(msg)

        dynamic_type_check(
            value=self.property_accessors,
            expected_type=PropertyAccessor,
            container=frozenset,
            field="property_accessors",
        )
        if not self.property_accessors:
            msg = "property_accessors must contain at least one accessor."
            raise ValueError(msg)

        if self.check_mode == CheckMode.SURVEY:
            msg = "check_mode='survey' is not implemented yet. Supported values: regression, catchup."
            raise ValueError(msg)

        object.__setattr__(self, "exclude_paths", _normalize_path_filters(self.exclude_paths, field="exclude_paths"))
        object.__setattr__(
            self,
            "dia_exclude_paths",
            _normalize_path_filters(self.dia_exclude_paths, field="dia_exclude_paths"),
        )

    def severity_for(self, code: str, default: Severity) -> Severity:
        """Return the effective severity for a diagnostic code.

        Parameters
        ----------
        code : str
            The diagnostic code to look up, used as the key in the severity mapping to determine the effective severity level.
        default : Severity
            The severity to use if the specified code is not found in the severity mapping.

        Returns
        -------
        Severity
            Return the effective severity for a diagnostic code, using the provided default if no specific severity is configured
            for the given code.

        """
        return self.severity.get(code, _PROFILES[self.profile].get(code, default))


def load_check_config(  # noqa: C901, PLR0913
    project_root: str | Path,
    *,
    fail_on_warning: bool | None = None,
    include_visibility: frozenset[Visibility] | None = None,
    symbol_kinds: frozenset[SymbolKind] | None = None,
    property_accessors: frozenset[PropertyAccessor] | None = None,
    profile: str | None = None,
    check_mode: str | None = None,
    annotation_placement: str | None = None,
    on_missing_base: str | None = None,
    method_exception_contract: str | None = None,
    dia: bool | None = None,
    exclude_paths: tuple[str, ...] | None = None,
    dia_exclude_paths: tuple[str, ...] | None = None,
) -> CheckConfig:
    """Load configuration from `pyproject.toml`, then apply CLI overrides.

    Parameters
    ----------
    project_root : str | Path
        The root directory of the project, specified as a string or a Path object, from which the `pyproject.toml` file is located
        and loaded to initialize the configuration.
    fail_on_warning : bool | None = None
        Whether to treat warnings as failures. When set to True, any warnings encountered during checks will cause the process to
        fail. When set to False, warnings are reported but do not cause failure. When None, the value from the configuration file
        is used.
    include_visibility : frozenset[Visibility] | None = None
        A frozenset of Visibility enum members specifying which symbol visibilities to include during checks. When provided, this
        overrides the corresponding value loaded from `pyproject.toml`. If None, the configuration falls back to the value defined
        in the config file.
    symbol_kinds : frozenset[SymbolKind] | None = None
        Optional set of symbol kinds to include in the check. When provided, only symbols matching one of these kinds will be
        considered; when omitted (the default), all symbol kinds are included. This overrides the corresponding value loaded from
        the configuration file.
    property_accessors : frozenset[PropertyAccessor] | None = None
        Optional property accessor roles to include. When omitted, both getters and setters are included.
    profile : str | None = None
        Optional built-in severity policy. Explicit per-code values remain authoritative.
    check_mode : str | None = None
        Override the check mode loaded from configuration. The value is normalized via ``_normalize_check_mode`` before being
        applied to the resulting ``CheckConfig``. When ``None`` (the default), the check mode read from ``pyproject.toml`` is used
        unchanged.
    annotation_placement : str | None = None
        Controls where type annotations are placed in the generated documentation. When provided, this value overrides the
        corresponding setting read from the ``[tool.docmethis_check]`` table in ``pyproject.toml``. The value must be a string
        matching one of the members of the ``AnnotationPlacement`` enum, such as ``"signature"`` or ``"separate"``; an invalid
        value raises a ``ValueError``. If ``None`` (the default), the configuration loaded from ``pyproject.toml`` is used without
        modification.
    on_missing_base : str | None = None
        Determines the action to take when a base symbol is missing during documentation checks. When provided, this value
        overrides the setting loaded from ``pyproject.toml`` by normalizing the given string to a member of the ``OnMissingBase``
        enum. If not provided, the default value from the configuration file or ``OnMissingBase.EMIT_ALL`` is used.
    method_exception_contract : str | None = None
        Selects whether class-method exceptions are documented on each callable or in a class-level aggregate. When ``None``, the
        value from ``pyproject.toml`` is used.
    dia : bool | None = None
        Whether to enable diagnostic annotations (dia). When set to True, diagnostic annotations are enabled; when False, they are
        disabled. If None, the value from the configuration file is used.
    exclude_paths : tuple[str, ...] | None = None
        Project-relative files or directory prefixes to exclude from Check and DIA. When None, the values from the configuration
        file are used.
    dia_exclude_paths : tuple[str, ...] | None = None
        Project-relative files or directory prefixes to exclude from DIA while keeping direct Check enabled. When None, the values
        from the configuration file are used.

    Returns
    -------
    CheckConfig
        The resolved CheckConfig instance, built by loading defaults from the `pyproject.toml` file located at the given project
        root and then applying any provided keyword arguments as overrides.

    """
    root = Path(project_root).resolve()
    data = _read_pyproject(root / "pyproject.toml")
    section = _check_section(data)
    _validate_check_keys(section)

    config = CheckConfig(
        fail_on_warning=_read_bool(section, "fail-on-warning", default=False),
        include_visibility=_read_include_visibility(section),
        symbol_kinds=_read_symbol_kinds(section),
        property_accessors=_read_property_accessors(section),
        severity=_read_severities(section),
        profile=_read_profile(section),
        base_ref=_read_str(section, "base-ref"),
        on_nonlinear_push_without_base=_read_str(section, "on-nonlinear-push-without-base", default="fail"),
        check_mode=_read_check_mode(section),
        annotation_placement=_read_enum(
            section, "annotation-placement", enum_type=AnnotationPlacement, default=AnnotationPlacement.SIGNATURE
        ),
        on_missing_base=_read_enum(section, "on-missing-base", enum_type=OnMissingBase, default=OnMissingBase.EMIT_ALL),
        method_exception_contract=_read_enum(
            section,
            "method-exception-contract",
            enum_type=MethodExceptionContract,
            default=MethodExceptionContract.CALLABLE,
        ),
        dia=_read_bool(section, "dia", default=True),
        forbidden_terms=_load_forbidden_terms(root),
        exclude_paths=_read_path_filters(section, "exclude-paths"),
        dia_exclude_paths=_read_path_filters(section, "dia-exclude-paths"),
    )

    if fail_on_warning is not None:
        config = replace(config, fail_on_warning=fail_on_warning)

    if include_visibility is not None:
        config = replace(config, include_visibility=include_visibility)

    if symbol_kinds is not None:
        config = replace(config, symbol_kinds=symbol_kinds)

    if property_accessors is not None:
        config = replace(config, property_accessors=property_accessors)

    if profile is not None:
        config = replace(config, profile=_normalize_profile(profile))

    if check_mode is not None:
        config = replace(config, check_mode=_normalize_check_mode(check_mode))
    if annotation_placement is not None:
        config = replace(
            config,
            annotation_placement=normalize_str_enum_value(
                value=annotation_placement, enum_type=AnnotationPlacement, field="annotation_placement"
            ),
        )
    if on_missing_base is not None:
        config = replace(
            config,
            on_missing_base=normalize_str_enum_value(value=on_missing_base, enum_type=OnMissingBase, field="on_missing_base"),
        )

    if method_exception_contract is not None:
        config = replace(
            config,
            method_exception_contract=normalize_str_enum_value(
                value=method_exception_contract,
                enum_type=MethodExceptionContract,
                field="tool.docmethis.check.method-exception-contract",
            ),
        )

    if dia is not None:
        config = replace(config, dia=dia)

    if exclude_paths is not None:
        config = replace(config, exclude_paths=exclude_paths)

    if dia_exclude_paths is not None:
        config = replace(config, dia_exclude_paths=dia_exclude_paths)

    return config


def _load_forbidden_terms(root: Path) -> tuple[str, ...]:
    """Load the project vocabulary when the project declares one.

    Parameters
    ----------
    root : Path
        Root directory of the project from which the forbidden terms file is resolved.

    Returns
    -------
    tuple[str, ...]
        A tuple of forbidden terms loaded from the project vocabulary; an empty tuple if the project does not declare one.

    Raises
    ------
    ValueError
        Explicitly raised.

    """
    path = root / _FORBIDDEN_TERMS_FILE
    if not path.is_file():
        return ()
    try:
        return load_forbidden_terms(path)
    except (OSError, UnicodeError) as exc:
        message = f"Unable to read forbidden terms file: {path}"
        raise ValueError(message) from exc


def parse_include_visibility(value: str) -> frozenset[Visibility]:
    """Parse a comma-separated list of visibilities.

    Parameters
    ----------
    value : str
        A comma-separated string of visibility names to be parsed, where each name is stripped of surrounding whitespace and
        converted into a Visibility value to form the resulting frozenset.

    Returns
    -------
    frozenset[Visibility]
        Parse a comma-separated string of visibility names into a frozenset of Visibility enums, raising a ValueError if the input
        is empty.

    Raises
    ------
    ValueError
        Explicitly raised.

    """
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        msg = "include_visibility must contain at least one visibility."
        raise ValueError(msg)

    return frozenset(_visibility_from_name(name) for name in names)


def parse_path_filters(value: str) -> tuple[str, ...]:
    """Parse a comma-separated list of project-relative path filters.

    Parameters
    ----------
    value : str
        Comma-separated exact paths or directory prefixes.

    Returns
    -------
    tuple[str, ...]
        Deduplicated normalized path filters.

    """
    return _normalize_path_filters([name.strip() for name in value.split(",") if name.strip()], field="path filters")


def parse_symbol_kinds(value: str) -> frozenset[SymbolKind]:
    """Parse a comma-separated list of symbol kinds.

    Parameters
    ----------
    value : str
        A comma-separated string of symbol kind names to be parsed and converted into a frozenset of SymbolKind enum values. Each
        name is stripped of surrounding whitespace, and empty entries are ignored. The names are matched case-insensitively
        against valid SymbolKind enum member names. If the resulting list of names is empty, a ValueError is raised indicating
        that at least one symbol kind must be provided.

    Returns
    -------
    frozenset[SymbolKind]
        A frozenset containing the parsed SymbolKind enum members corresponding to the provided comma-separated list of symbol
        kind names.

    Raises
    ------
    ValueError
        Explicitly raised.

    """
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        msg = "symbol_kinds must contain at least one symbol kind."
        raise ValueError(msg)

    return frozenset(normalize_str_enum_value(value=name, enum_type=SymbolKind, field="symbol_kinds[]") for name in names)


def parse_property_accessors(value: str) -> frozenset[PropertyAccessor]:
    """Parse a comma-separated list of property accessor roles."""
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        msg = "property_accessors must contain at least one accessor."
        raise ValueError(msg)

    return frozenset(
        normalize_str_enum_value(value=name, enum_type=PropertyAccessor, field="property_accessors[]") for name in names
    )


def _read_pyproject(path: Path) -> dict[str, object]:
    """Read `pyproject.toml` when present.

    Parameters
    ----------
    path : Path
        The `path` parameter specifies the location of the `pyproject.toml` file to be read. If the file does not exist at the
        given path, an empty dictionary is returned. Otherwise, the file is opened in binary mode and parsed using `tomllib`,
        returning its contents as a dictionary of string keys and arbitrary object values.

    Returns
    -------
    dict[str, object]
        A dictionary representing the parsed contents of the `pyproject.toml` file located at the given path. If the file does not
        exist, an empty dictionary is returned.

    """
    if not path.exists():
        return {}
    with path.open("rb") as file:
        return tomllib.load(file)


def _check_section(data: dict[str, object]) -> dict[str, object]:
    """Return the `tool.docmethis.check` section when it exists.

    Parameters
    ----------
    data : dict[str, object]
        The input dictionary representing the parsed configuration data, typically obtained from a project's pyproject.toml file,
        from which the `tool.docmethis.check` section is extracted through successive key lookups.

    Returns
    -------
    dict[str, object]
        Return the `tool.docmethis.check` section when it exists.

    """
    section: object = data
    for key in _SECTION_CHECK:
        if not isinstance(section, dict):
            return {}
        section = section.get(key, {})
    if not isinstance(section, dict):
        return {}
    return section


def _validate_check_keys(section: dict[str, object]) -> None:
    """Reject unknown or non-canonical Check configuration keys.

    Parameters
    ----------
    section : dict[str, object]
        Direct values from the ``tool.docmethis.check`` TOML table.

    Raises
    ------
    ValueError
        If the table contains an unsupported option name.

    """
    unknown = sorted(set(section) - _CHECK_OPTION_KEYS)
    if unknown:
        names = ", ".join(unknown)
        msg = f"Unknown tool.docmethis.check option(s): {names}. Use kebab-case option names."
        raise ValueError(msg)


def _read_str(section: dict[str, object], key: str, *, default: str | None = None) -> str | None:
    """Read a configuration string or return the default.

    Parameters
    ----------
    section : dict[str, object]
        A dictionary representing a section of the configuration, where each key is a string and each value is an arbitrary
        object. This dictionary is expected to contain configuration entries, and the function retrieves the value associated with
        the specified key from this section.
    key : str
        The configuration key used to look up the desired string value within the given section.
    default : str | None = None
        The default value to return when the specified key is not present in the section. It can be either a string or None, and
        defaults to None if not provided.

    Returns
    -------
    str | None
        The configuration string value for the given key if it exists and is valid; otherwise, the default value.

    """
    value = section.get(key, default)
    type_check(value=value, expected_alias_type=OptionalStr, field=f"tool.docmethis.check.{key}")
    return value


def _read_bool(section: dict[str, object], key: str, *, default: bool) -> bool:
    """Read a configuration boolean.

    Parameters
    ----------
    section : dict[str, object]
        A dictionary representing the configuration section from which the boolean value is read. Each entry in the dictionary
        corresponds to a configuration key and its associated value, where the value for the specified key is expected to be a
        boolean or compatible type.
    key : str
        The key whose associated value is read and validated as a boolean within the given configuration section.
    default : bool
        The default boolean value to return when the specified key is not found within the given configuration section.

    Returns
    -------
    bool
        A boolean value indicating whether the configuration option was successfully read and validated as a strict boolean type.

    """
    value = section.get(key, default)
    type_check(value=value, expected_alias_type=StrictBool, field=f"tool.docmethis.check.{key}")
    return value


def _read_enum[T](section: dict[str, object], key: str, *, enum_type: type[T], default: T) -> T:
    """Reads a value from a configuration section and converts it to a specified enum type.

    Parameters
    ----------
    section : dict[str, object]
        A dictionary representing a configuration section.
    key : str
        The key to look up in the configuration section.
    enum_type : type[T]
        The target enum type to convert the value to.
    default : T
        The default value whose ``value`` attribute is used when the key is missing.

    Returns
    -------
    T
        The value from the configuration section converted to the specified enum type.

    """
    value = section.get(key, default.value)
    return normalize_str_enum_value(value=value, enum_type=enum_type, field=f"tool.docmethis.check.{key}")


def _read_check_mode(section: dict[str, object]) -> CheckMode:
    """Read the configured check mode, rejecting unsupported modes.

    Parameters
    ----------
    section : dict[str, object]
        The ``section`` parameter is a dictionary mapping configuration keys to their associated values, from which the check mode
        is read. The function looks up the ``"check-mode"`` key within this dictionary to determine the desired mode, defaulting
        to the value of ``CheckMode.REGRESSION`` if the key is not present.

    Returns
    -------
    CheckMode
        Reads the configured check mode from the given section, defaulting to the regression mode value if not specified, and
        normalizes it into a CheckMode instance while rejecting any unsupported modes.

    """
    value = section.get("check-mode", CheckMode.REGRESSION.value)
    return _normalize_check_mode(value)


def _normalize_check_mode(value: object) -> CheckMode:
    """Normalize a check mode supported by the current execution.

    Parameters
    ----------
    value : object
        The check mode value to be normalized, which can be any object that represents a supported check mode.

    Returns
    -------
    CheckMode
        Normalize the given value into a valid CheckMode enum member supported by the current execution environment, using the
        configuration field 'tool.docmethis.check.check-mode' for reference.

    """
    return normalize_str_enum_value(value=value, enum_type=CheckMode, field="tool.docmethis.check.check-mode")


def _read_include_visibility(section: dict[str, object]) -> frozenset[Visibility]:
    """Read the visibilities included in the check.

    Parameters
    ----------
    section : dict[str, object]
        A dictionary representing the configuration section from which the visibilities to include in the check are read. It is
        expected to contain a key ``include-visibility`` whose value is a list of strings corresponding to visibility names; if
        absent, it defaults to including only public visibility.

    Returns
    -------
    frozenset[Visibility]
        Return a frozenset of Visibility values parsed from the include_visibility configuration list, defaulting to public
        visibility if unspecified.

    Raises
    ------
    ValueError
        Explicitly raised.

    """
    value = section.get("include-visibility", [Visibility.PUBLIC.value])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        msg = "tool.docmethis.check.include-visibility must be a list of strings."
        raise ValueError(msg)
    if not value:
        msg = "tool.docmethis.check.include-visibility must contain at least one visibility."
        raise ValueError(msg)
    return frozenset(_visibility_from_name(item) for item in value)


def _read_symbol_kinds(section: dict[str, object]) -> frozenset[SymbolKind]:
    """Read the symbol kinds included in the check.

    Parameters
    ----------
    section : dict[str, object]
        The configuration section from the project's tool settings that contains the symbol kinds to be checked. This dictionary
        may include a 'symbol-kinds' entry whose value is a list of strings representing the desired SymbolKind values; if absent,
        the function defaults to including function, method, and class symbol kinds. The function validates that the provided
        value is a non-empty list of strings and converts each entry into its corresponding SymbolKind enum member.

    Returns
    -------
    frozenset[SymbolKind]
        A frozenset of SymbolKind values representing the symbol kinds to include in the check, parsed from the 'symbol-kinds'
        entry in the configuration section. If the entry is absent, it defaults to function, method, and class symbol kinds. Each
        string value is validated and converted to its corresponding SymbolKind enum member.

    Raises
    ------
    ValueError
        Explicitly raised.

    """
    value = section.get(
        "symbol-kinds",
        [SymbolKind.FUNCTION.value, SymbolKind.METHOD.value, SymbolKind.CLASS.value],
    )
    type_check(value=value, expected_alias_type=StrictListOfStrictStr, field="tool.docmethis.check.symbol-kinds")
    if not value:
        msg = "tool.docmethis.check.symbol-kinds must contain at least one symbol kind."
        raise ValueError(msg)
    return frozenset(
        normalize_str_enum_value(value=item, enum_type=SymbolKind, field="tool.docmethis.check.symbol-kinds[]") for item in value
    )


def _read_property_accessors(section: dict[str, object]) -> frozenset[PropertyAccessor]:
    """Read the property accessor roles included in the check.

    Parameters
    ----------
    section : dict[str, object]
        Configuration section containing the ``property-accessors`` value.

    Returns
    -------
    frozenset[PropertyAccessor]
        Configured property accessor roles.

    Raises
    ------
    ValueError
        If the list is empty or contains an unsupported accessor.

    """
    value = section.get("property-accessors", [PropertyAccessor.GETTER.value, PropertyAccessor.SETTER.value])
    type_check(value=value, expected_alias_type=StrictListOfStrictStr, field="tool.docmethis.check.property-accessors")
    if not value:
        msg = "tool.docmethis.check.property-accessors must contain at least one accessor."
        raise ValueError(msg)
    return frozenset(
        normalize_str_enum_value(value=item, enum_type=PropertyAccessor, field="tool.docmethis.check.property-accessors[]")
        for item in value
    )


def _read_path_filters(section: dict[str, object], key: str) -> tuple[str, ...]:
    """Read project-relative path filters from the Check configuration.

    Parameters
    ----------
    section : dict[str, object]
        Parsed ``tool.docmethis.check`` configuration.
    key : str
        Configuration key containing the path filters.

    Returns
    -------
    tuple[str, ...]
        Deduplicated normalized path filters, or an empty tuple when absent.

    """
    return _normalize_path_filters(section.get(key, []), field=f"tool.docmethis.check.{key}")


def _read_severities(section: dict[str, object]) -> dict[str, Severity]:
    """Read severity overrides by DMT code.

    Parameters
    ----------
    section : dict[str, object]
        A dictionary representing the configuration section containing severity overrides, where each key is a DMT code and the
        corresponding value is a string specifying the severity level (one of 'error', 'warning', or 'disabled').

    Returns
    -------
    dict[str, Severity]
        A dictionary mapping each DMT code to its corresponding severity level, as parsed and validated from the provided
        configuration section.

    Raises
    ------
    ValueError
        Explicitly raised.
    TypeError
        Explicitly raised.

    """
    value = section.get("severity", {})
    if not isinstance(value, dict):
        msg = "tool.docmethis.check.severity must be a TOML table."
        raise TypeError(msg)

    severities: dict[str, Severity] = {}
    for code, severity in value.items():
        if not isinstance(code, str) or not isinstance(severity, str):
            msg = "tool.docmethis.check.severity must map codes to strings."
            raise TypeError(msg)
        if severity not in _VALID_SEVERITIES:
            msg = f"Invalid severity for {code}: {severity!r}. Expected values: error, warning, disabled."
            raise ValueError(msg)
        severities[code] = cast("Severity", severity)
    return severities


def _read_profile(section: dict[str, object]) -> str:
    """Read the built-in severity policy selected by the project.

    Parameters
    ----------
    section : dict[str, object]
        Configuration section containing the profile selection; the profile is read from the 'profile' key, defaulting to
        'standard' if absent.

    Returns
    -------
    str
        The normalized severity profile string corresponding to the built-in policy selected by the project.

    """
    return _normalize_profile(section.get("profile", "standard"))


def _visibility_from_name(name: str) -> Visibility:
    """Convert a configured visibility name to the M1 enum.

    Parameters
    ----------
    name : str
        The name of the visibility level to convert, specified as a string. Valid values are 'public', 'protected', and 'private'.
        An invalid name raises a ValueError.

    Returns
    -------
    Visibility
        Return the Visibility enum member corresponding to the given visibility name string. The name must be one of the
        predefined visibility names (public, protected, private). If the provided name does not match any known visibility, a
        ValueError is raised with a message indicating the invalid value and listing the valid options.

    Raises
    ------
    ValueError
        Explicitly raised.

    """
    try:
        return _VISIBILITIES_BY_NAME[name]
    except KeyError as exc:
        msg = f"Invalid visibility: {name!r}. Expected values: public, protected, private."
        raise ValueError(msg) from exc
