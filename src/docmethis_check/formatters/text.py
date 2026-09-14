# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Human-readable text formatter for the check module."""

from __future__ import annotations

import ast
import os
import sys
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING

from docmethis_check.models import AnnotationPlacement, annotation_line

if TYPE_CHECKING:
    from docmethis_check.models import CheckEntry, CheckResult

__all__ = ["format"]

_SEPARATOR = "─" * 60
_ASCII_CONTROL_LIMIT = 32
_ASCII_DELETE = 127
_BRIGHT_ANSI_COLOR_START = 8
_MIN_PROPAGATION_PATH_LENGTH = 2
_DIAGNOSTIC_PREFIX_WIDTH = 21
_GROUP_INDENT = "  "
_RESET = "\x1b[0m"
_COLOR_PALETTES = {
    "dark": {
        "PASS": "\x1b[32m",
        "WARN": "\x1b[33m",
        "FAIL": "\x1b[31m",
        "ERROR": "\x1b[91m",
        "ERROR_CODE": "\x1b[1;91m",
        "WARNING": "\x1b[93m",
        "WARNING_CODE": "\x1b[1;93m",
        "MESSAGE": "\x1b[1;90m",
        "CYAN": "\x1b[96m",
        "BLUE": "\x1b[94m",
        "RED": "\x1b[91m",
    },
    "light": {
        "PASS": "\x1b[32m",
        "WARN": "\x1b[33m",
        "FAIL": "\x1b[31m",
        "ERROR": "\x1b[31m",
        "ERROR_CODE": "\x1b[1;31m",
        "WARNING": "\x1b[33m",
        "WARNING_CODE": "\x1b[1;33m",
        "MESSAGE": "\x1b[1;30m",
        "CYAN": "\x1b[36m",
        "BLUE": "\x1b[34m",
        "RED": "\x1b[31m",
    },
}
_SCOPE_LABELS = {
    "local_working_tree": "local changes",
    "explicit": "explicit range",
    "pr_merge_base": "pull request range",
}
_COMPLETENESS_LABELS = {
    "complete_relative_to_base": "complete relative to base",
    "complete_relative_to_common_ancestor": "complete relative to common ancestor",
    "partial": "partial",
    "none": "unresolved",
}


def format(  # noqa: A001, PLR0913
    result: CheckResult,
    file: str | None = None,
    *,
    fail_on_warning: bool = False,
    project_root: Path | None = None,
    verbose: bool = False,
    color: bool | str | None = None,
    ascii_mode: bool = False,
    annotation_placement: AnnotationPlacement = AnnotationPlacement.SIGNATURE,
) -> str:
    """Produce a deterministic human-readable check report.

    Parameters
    ----------
    result : CheckResult
        Check result to format.
    file : str | None = None
        Output file path; stdout is used when omitted.
    fail_on_warning : bool = False
        Whether warnings make the displayed status ``FAIL``.
    project_root : Path | None = None
        Project root used to display absolute entry paths relatively.
    verbose : bool = False
        Whether to include additional diagnostic metadata.
    color : bool | str | None = None
        Color mode. ``None`` and ``"auto"`` detect a compatible TTY, ``True``
        enables colors on a TTY, ``"always"`` forces colors on stdout, and
        ``False`` or ``"never"`` disables colors. Files never receive ANSI.
    ascii_mode : bool = False
        Whether to use ASCII-only tree, separator, and status punctuation glyphs.
    annotation_placement : AnnotationPlacement
        Source location policy used for local diagnostic frames.

    Returns
    -------
    str
        The generated report text.

    """
    use_color = _color_enabled(file, color=color)
    palette = _color_palette() if use_color else None
    status = _status(result, fail_on_warning=fail_on_warning)
    title_separator = "-" if ascii_mode else "—"
    lines = [
        f"DocMeThis Check {title_separator} {_paint(status, style=status, palette=palette)}",
        _format_scope(result, ascii_mode=ascii_mode),
        "",
    ]

    if result.summary.warning_count == 0 and result.summary.error_count == 0:
        message = "No documentation issues found."
        if result.analysis_errors:
            message = "No documentation issues found in checked files."
        lines.append(message)
    else:
        lines.extend(
            _format_diagnostics(
                result,
                project_root,
                verbose=verbose,
                palette=palette,
                ascii_mode=ascii_mode,
                annotation_placement=annotation_placement,
            ),
        )

    lines.extend(
        [
            "",
            _paint("-" * len(_SEPARATOR) if ascii_mode else _SEPARATOR, style="MESSAGE", palette=palette),
            _format_summary(result, ascii_mode=ascii_mode),
        ],
    )
    file_summary = _format_file_summary(result, project_root, ascii_mode=ascii_mode)
    if file_summary:
        lines.append(file_summary)
    analysis_errors = _format_analysis_errors(result, project_root, palette=palette, ascii_mode=ascii_mode)
    if file_summary and analysis_errors:
        lines.append("")
    lines.extend(analysis_errors)
    lines.extend(_format_dia(result, verbose=verbose))
    if verbose:
        lines.extend(_format_verbose_report_metadata(result))
    lines.append("Use --format json for the complete report.")

    text = "\n".join(lines) + "\n"
    if file is not None:
        Path(file).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return text


def _format_diagnostics(  # noqa: PLR0913
    result: CheckResult,
    project_root: Path | None,
    *,
    verbose: bool,
    palette: dict[str, str] | None,
    ascii_mode: bool,
    annotation_placement: AnnotationPlacement,
) -> list[str]:
    """Format diagnostics in stable file and symbol groups."""
    ordered = sorted(result.checks, key=lambda entry: _entry_sort_key(entry, project_root))

    branch = "+-" if ascii_mode else "├─"
    last_branch = r"\-" if ascii_mode else "└─"
    tree_vertical = "|" if ascii_mode else "│"
    symbol_separator = "-" if ascii_mode else "·"
    lines: list[str] = []
    first_file = True
    for display_file, file_entries_iterator in groupby(
        ordered,
        key=lambda entry: _display_path(entry.file, project_root),
    ):
        if not first_file:
            lines.append("")
        first_file = False
        lines.append(_paint(display_file, style="CYAN", palette=palette))
        file_entries = list(file_entries_iterator)
        symbol_groups = [
            list(symbol_entries)
            for _symbol_key, symbol_entries in groupby(
                file_entries,
                key=lambda entry: (entry.symbol, entry.accessor_kind),
            )
        ]
        for group_index, group in enumerate(symbol_groups):
            is_last = group_index == len(symbol_groups) - 1
            lines.extend(
                _format_symbol_group(
                    group,
                    project_root=project_root,
                    verbose=verbose,
                    palette=palette,
                    branch=branch,
                    last_branch=last_branch,
                    tree_vertical=tree_vertical,
                    source_gutter=tree_vertical,
                    symbol_separator=symbol_separator,
                    is_last=is_last,
                    annotation_placement=annotation_placement,
                ),
            )
    return lines


def _format_symbol_group(  # noqa: PLR0913
    entries: list[CheckEntry],
    *,
    project_root: Path | None,
    verbose: bool,
    palette: dict[str, str] | None,
    branch: str,
    last_branch: str,
    tree_vertical: str,
    source_gutter: str,
    symbol_separator: str,
    is_last: bool,
    annotation_placement: AnnotationPlacement,
) -> list[str]:
    """Render one source frame followed by all diagnostics for its symbol."""
    source = _read_source(entries[0], project_root)
    frames = _frame_groups(entries, source, annotation_placement)
    symbol = _display_symbol(entries[0], project_root)
    lines: list[str] = []
    for frame_index, (frame_entries, location) in enumerate(frames):
        frame_is_last = frame_index == len(frames) - 1
        item_is_last = frame_is_last and is_last
        tree_prefix = "   " if item_is_last else f"{tree_vertical}  "
        frame_branch = last_branch if item_is_last else branch
        line_start, col_start, _ = location
        lines.append(
            f"{frame_branch} {_paint(symbol, style='CYAN', palette=palette)} {symbol_separator} {line_start}:{col_start}"
        )
        lines.extend(
            _format_source_context(
                frame_entries[0],
                project_root=project_root,
                palette=palette,
                source=source,
                location=location,
                tree_prefix=tree_prefix,
                source_gutter=source_gutter,
            ),
        )
        for diagnostic in frame_entries:
            lines.extend(_format_diagnostic(diagnostic, verbose=verbose, palette=palette, tree_prefix=tree_prefix))
        if not item_is_last:
            lines.append(source_gutter)
    return lines


def _format_diagnostic(
    entry: CheckEntry,
    *,
    verbose: bool,
    palette: dict[str, str] | None,
    tree_prefix: str,
) -> list[str]:
    """Format one diagnostic inside a symbol group."""
    severity = _ascii(entry.severity).upper()
    if severity == "ERROR":
        severity_style = "ERROR"
        code_style = "ERROR_CODE"
    elif severity == "WARNING":
        severity_style = "WARNING"
        code_style = "WARNING_CODE"
    else:
        severity_style = "MESSAGE"
        code_style = "MESSAGE"
    message = _format_message(entry)
    severity_gap = " " * max(1, 9 - len(severity))
    code = _ascii(entry.code)
    code_gap = " " * max(1, 10 - len(code))
    lines = [
        (
            f"{tree_prefix}{_paint(severity, style=severity_style, palette=palette)}{severity_gap}"
            f"{_paint(code, style=code_style, palette=palette)}{code_gap}{message}"
        ),
    ]
    propagated_from = _propagated_from(entry)
    if propagated_from is not None:
        continuation_prefix = f"{tree_prefix}{' ' * (_DIAGNOSTIC_PREFIX_WIDTH - len(_GROUP_INDENT))}"
        lines.append(f"{continuation_prefix}{_paint('via ', style='CYAN', palette=palette)}{propagated_from}")
    required = _format_requirement(entry)
    if required is not None:
        continuation_prefix = f"{tree_prefix}{' ' * (_DIAGNOSTIC_PREFIX_WIDTH - len(_GROUP_INDENT))}"
        lines.append(
            f"{continuation_prefix}{_paint('Required: ', style='MESSAGE', palette=palette)}{required}",
        )
    if verbose:
        lines.extend(_format_verbose_entry(entry))
    return lines


def _format_message(entry: CheckEntry) -> str:
    """Build a concise, consistent message from structured diagnostic fields."""
    if entry.attribute_name and (entry.section or "").casefold() == "attributes":
        visibility = _ascii(entry.visibility or "public").capitalize()
        attribute = _inline_code(entry.attribute_name)
        section = _inline_code(entry.section)
        return f"{visibility} attribute is not documented in {section}: {attribute}"
    if entry.code == "DMT-4001" and entry.section == "Raises" and entry.expected:
        return f"{_inline_code(_simple_name(entry.expected))} is not documented in `Raises`."
    if entry.code == "DMT-4301" and entry.section == "Raises" and entry.expected:
        return f"{_inline_code(_simple_name(entry.expected))} is not documented in class `Raises`."
    if entry.code == "DMT-4002" and entry.section == "Raises" and entry.observed:
        return f"{_inline_code(entry.observed)} is documented in `Raises` but not observed."
    if entry.code in {"DMT-4201", "DMT-4202"}:
        change = _dia_change(entry)
        if change is not None:
            direction, value, provenance = change
            suffix = f" ({provenance})" if provenance else ""
            return f"Observable contract {direction}: {_inline_code(value)}{suffix}."
    return _ascii(entry.message)


def _format_requirement(entry: CheckEntry) -> str | None:
    """Render an actionable requirement without exposing internal DIA deltas."""
    if entry.attribute_name or entry.code in {"DMT-4201", "DMT-4202"}:
        return None
    return _format_expected(entry)


def _simple_name(value: object) -> str:
    """Return the display name of a qualified value."""
    return _ascii(str(value).strip("`").rsplit(".", maxsplit=1)[-1])


def _dia_change(entry: CheckEntry) -> tuple[str, str, str | None] | None:
    """Extract a user-facing direction, value, and provenance from a DIA entry."""
    dia = entry.dia or {}
    direction = dia.get("direction")
    value = dia.get("value")
    provenance = dia.get("provenance")
    if not isinstance(direction, str) or not isinstance(value, str):
        raw = entry.expected
        if raw is None or ":" not in raw:
            return None
        direction, value = (part.strip() for part in raw.split(":", maxsplit=1))
    if not isinstance(provenance, str):
        provenance = None
    if value.endswith((":local", ":propagated")):
        value, suffix = value.rsplit(":", maxsplit=1)
        provenance = provenance or suffix
    return _ascii(direction), _simple_name(value), _ascii(provenance) if provenance else None


def _frame_groups(
    entries: list[CheckEntry],
    source: tuple[str, list[str]] | None,
    placement: AnnotationPlacement,
) -> list[tuple[list[CheckEntry], tuple[int, int, int | None]]]:
    """Group diagnostics by target line when precise placement requires multiple frames."""
    if placement is not AnnotationPlacement.PRECISE:
        return [(entries, _placement_location(entries[0], source, placement))]

    grouped: dict[int, list[CheckEntry]] = {}
    locations: dict[int, tuple[int, int, int | None]] = {}
    for entry in entries:
        location = _placement_location(entry, source, placement)
        grouped.setdefault(location[0], []).append(entry)
        locations.setdefault(location[0], location)
    return [(grouped[line], locations[line]) for line in sorted(grouped)]


def _placement_location(
    entry: CheckEntry,
    source: tuple[str, list[str]] | None,
    placement: AnnotationPlacement,
) -> tuple[int, int, int | None]:
    """Resolve a local source-frame location from the configured annotation placement."""
    source_text = source[0] if source is not None else None
    signature_location = _signature_location(entry, source_text)
    if placement is AnnotationPlacement.SIGNATURE:
        return signature_location
    display_location = _display_location(entry, source_text)
    if placement is AnnotationPlacement.PRECISE and entry.attribute_name:
        return display_location

    target_line = annotation_line(entry, placement)
    if target_line == signature_location[0]:
        return signature_location
    if source is None or not 1 <= target_line <= len(source[1]):
        return target_line, entry.col_start, entry.col_end
    return _source_line_location(target_line, source[1])


def _source_line_location(line_number: int, source_lines: list[str]) -> tuple[int, int, int]:
    """Return the first non-whitespace column and visible end of a source line."""
    line = source_lines[line_number - 1].expandtabs(4).rstrip()
    col_start = len(line) - len(line.lstrip()) + 1
    return line_number, col_start, max(col_start, len(line))


def _format_source_context(  # noqa: PLR0913
    entry: CheckEntry,
    *,
    project_root: Path | None,
    palette: dict[str, str] | None,
    source: tuple[str, list[str]] | None = None,
    location: tuple[int, int, int | None] | None = None,
    tree_prefix: str,
    source_gutter: str,
) -> list[str]:
    """Render a compact source frame around a symbol location."""
    if source is None:
        source = _read_source(entry, project_root)
    source_text = source[0] if source is not None else None
    source_lines = source[1] if source is not None else None
    line_start, col_start, col_end = location or _display_location(entry, source_text)
    lines: list[str] = []
    if source_lines is None or not 1 <= line_start <= len(source_lines):
        return lines

    line_width = len(str(line_start))
    lines.append(
        _format_source_line(
            line_start,
            source_lines[line_start - 1],
            line_width,
            palette,
            tree_prefix=tree_prefix,
            source_gutter=source_gutter,
        ),
    )
    lines.append(
        _format_caret_line(
            source_lines[line_start - 1],
            col_start,
            col_end,
            line_width,
            palette,
            tree_prefix=tree_prefix,
            source_gutter=source_gutter,
        ),
    )
    return lines


def _format_source_line(  # noqa: PLR0913
    number: int,
    source_line: str,
    line_width: int,
    palette: dict[str, str] | None,
    *,
    tree_prefix: str,
    source_gutter: str,
) -> str:
    """Render one source line with a colored line-number gutter."""
    prefix = f"{number:>{line_width}}"
    source = _ascii(source_line.expandtabs(4))
    return (
        f"{tree_prefix}{_paint(prefix, style='BLUE', palette=palette)} "
        f"{_paint(source_gutter, style='BLUE', palette=palette)} {source}"
    )


def _format_caret_line(  # noqa: PLR0913
    source_line: str,
    col_start: int,
    col_end: int | None,
    line_width: int,
    palette: dict[str, str] | None,
    *,
    tree_prefix: str,
    source_gutter: str,
) -> str:
    """Render the red caret under the reported source span."""
    start = max(col_start - 1, 0)
    end = max(col_end or col_start, col_start)
    prefix = _ascii(source_line[:start].expandtabs(4))
    selected = _ascii(source_line[start:end].expandtabs(4))
    carets = "^" * max(1, len(selected))
    gutter = f"{tree_prefix}{' ' * line_width} {_paint(source_gutter, style='BLUE', palette=palette)}"
    return f"{gutter} {' ' * len(prefix)}{_paint(carets, style='RED', palette=palette)}"


def _read_source(entry: CheckEntry, project_root: Path | None) -> tuple[str, list[str]] | None:
    """Read the source file needed for a diagnostic frame, if available."""
    path = Path(entry.file)
    if not path.is_absolute():
        path = (project_root or Path.cwd()) / path
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return text, text.splitlines()


def _display_location(entry: CheckEntry, source_text: str | None) -> tuple[int, int, int | None]:
    """Return the best source span available for a diagnostic."""
    if source_text is not None:
        attribute_location = _attribute_location(entry, source_text)
        if attribute_location is not None:
            return attribute_location
        definition_location = _definition_location(entry, source_text)
        if definition_location is not None:
            return definition_location
    return entry.line_start, entry.col_start, entry.col_end


def _signature_location(entry: CheckEntry, source_text: str | None) -> tuple[int, int, int | None]:
    """Return the source span of the diagnosed symbol definition."""
    if source_text is not None:
        definition_location = _definition_location(entry, source_text)
        if definition_location is not None:
            return definition_location
    return entry.line_start, entry.col_start, entry.col_end


def _attribute_location(entry: CheckEntry, source_text: str) -> tuple[int, int, int] | None:
    """Find a class or instance attribute location for an attribute diagnostic."""
    if not entry.attribute_name:
        return None
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return None

    class_node = _matching_class(tree, entry)
    if class_node is None:
        return None

    attribute_name = entry.attribute_name
    for statement in class_node.body:
        location = _assignment_attribute_location(statement, attribute_name)
        if location is not None:
            return location
    for statement in class_node.body:
        location = _instance_attribute_location(statement, attribute_name)
        if location is not None:
            return location
    return None


def _matching_class(tree: ast.AST, entry: CheckEntry) -> ast.ClassDef | None:
    """Find the diagnosed class by name and nearest source line."""
    class_name = entry.symbol.rsplit(".", maxsplit=1)[-1]
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == class_name]
    return min(classes, key=lambda node: abs(node.lineno - entry.line_start)) if classes else None


def _definition_location(entry: CheckEntry, source_text: str) -> tuple[int, int, int] | None:
    """Find a definition span for a symbol diagnostic."""
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return None

    kind = getattr(entry.symbol_kind, "value", entry.symbol_kind)
    if kind == "class":
        nodes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    elif kind in {"function", "method"}:
        nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))]
    elif kind is None:
        nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef))]
    else:
        return None

    name = entry.symbol.rsplit(".", maxsplit=1)[-1]
    matching_nodes = [node for node in nodes if getattr(node, "name", None) == name]
    if not matching_nodes:
        return None
    node = min(matching_nodes, key=lambda candidate: abs(candidate.lineno - entry.line_start))
    source_line = source_text.splitlines()[node.lineno - 1].expandtabs(4).rstrip()
    col_start = node.col_offset + 1
    return node.lineno, col_start, max(col_start, len(source_line))


def _instance_attribute_location(statement: ast.AST, attribute_name: str) -> tuple[int, int, int] | None:
    """Find an instance attribute assignment inside one class method."""
    if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    for child in ast.walk(statement):
        if isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            location = _assignment_attribute_location(child, attribute_name)
            if location is not None:
                return location
    return None


def _assignment_attribute_location(statement: ast.AST, attribute_name: str) -> tuple[int, int, int] | None:
    """Return the display span of an assignment target with the requested name."""
    targets: list[ast.AST]
    if isinstance(statement, ast.AnnAssign):
        targets = [statement.target]
    elif isinstance(statement, ast.Assign):
        targets = list(statement.targets)
    elif isinstance(statement, ast.AugAssign):
        targets = [statement.target]
    else:
        return None

    for target in targets:
        if isinstance(target, ast.Name) and target.id == attribute_name:
            end = target.end_col_offset
            return target.lineno, target.col_offset + 1, end
        if isinstance(target, ast.Attribute) and target.attr == attribute_name:
            end = target.end_col_offset
            return target.lineno, end - len(attribute_name) + 1, end
    return None


def _propagated_from(entry: CheckEntry) -> str | None:
    """Return the final symbol in a DIA propagation path, when available."""
    if entry.dia is None:
        return None
    evidence = entry.dia.get("evidence")
    if not isinstance(evidence, dict):
        return None
    path = evidence.get("path")
    if not isinstance(path, (list, tuple)) or len(path) < _MIN_PROPAGATION_PATH_LENGTH:
        return None
    return _ascii(path[-1])


def _format_verbose_entry(entry: CheckEntry) -> list[str]:
    """Format optional metadata for one diagnostic entry."""
    lines: list[str] = []
    kind = getattr(entry.symbol_kind, "value", entry.symbol_kind)
    if kind:
        lines.append(f"        Kind: {_ascii(kind)}")
    if entry.visibility:
        lines.append(f"        Visibility: {_ascii(entry.visibility)}")
    if entry.observed is not None:
        lines.append(f"        Observed: {_ascii(entry.observed)}")
    if entry.granularity:
        lines.append(f"        Granularity: {_ascii(entry.granularity)}")
    if entry.error_type:
        lines.append(f"        Error type: {_ascii(entry.error_type)}")
    if entry.dia:
        impact_kind = entry.dia.get("impact_kind")
        status = entry.dia.get("status")
        dia_label = _ascii(impact_kind) if impact_kind is not None else "impact"
        if status:
            dia_label += f" ({_ascii(status)})"
        lines.append(f"        DIA: {dia_label}")
    return lines


def _format_expected(entry: CheckEntry) -> str | None:
    """Build a short human-readable representation of an expected fact."""
    if entry.expected is None:
        return None
    expected = _inline_code(entry.expected)
    section = (entry.section or "").casefold()
    if entry.parameter_name:
        return f"parameter {_inline_code(entry.parameter_name)}"
    if section == "returns":
        return f"Returns section documenting {expected}"
    if section == "raises":
        return f"{expected} in Raises"
    return expected


def _format_summary(result: CheckResult, *, ascii_mode: bool) -> str:
    """Format check counters in the report's compact footer."""
    summary = result.summary
    parts = [_count(summary.total_count, "check")]
    if summary.error_count:
        parts.append(_count(summary.error_count, "error"))
    if summary.warning_count:
        parts.append(_count(summary.warning_count, "warning"))
    if summary.pass_count:
        parts.append(f"{summary.pass_count} passed")
    return f" {'-' if ascii_mode else '·'} ".join(parts)


def _format_file_summary(result: CheckResult, project_root: Path | None, *, ascii_mode: bool) -> str:
    """Format checked and skipped file counts without conflating them with checks."""
    checked = _checked_file_paths(result, project_root)
    skipped = _skipped_files(result, project_root)
    parts: list[str] = []
    if checked:
        parts.append(f"{_count(len(checked), 'file')} checked")
    if skipped:
        parts.append(f"{len(skipped)} skipped")
    return f" {'-' if ascii_mode else '·'} ".join(parts)


def _format_analysis_errors(
    result: CheckResult,
    project_root: Path | None,
    *,
    palette: dict[str, str] | None,
    ascii_mode: bool,
) -> list[str]:
    """Render files that could not be analyzed as part of the report."""
    lines: list[str] = []
    errors = sorted(result.analysis_errors, key=lambda error: _display_path(error.get("file", ""), project_root))
    for error in errors:
        path = _display_path(error.get("file", ""), project_root)
        kind = error.get("type", "analysis error").replace("_", " ")
        if kind == "parse error":
            kind = "syntax error"
        separator = "-" if ascii_mode else "—"
        lines.append(f"Skipped: {_ascii(path)} {separator} {_paint(kind, style='ERROR', palette=palette)}")
    return lines


def _skipped_files(result: CheckResult, project_root: Path | None) -> set[str]:
    """Return normalized paths of files omitted by project analysis."""
    return {_display_path(error.get("file", ""), project_root) for error in result.analysis_errors}


def _checked_file_paths(result: CheckResult, project_root: Path | None) -> set[str]:
    """Return files represented by the checked symbols and diagnostics."""
    paths = {_display_path(path, project_root) for path in result.checked_files}
    paths.update(_display_path(entry.file, project_root) for entry in result.checks)
    return paths - _skipped_files(result, project_root)


def _format_scope(result: CheckResult, *, ascii_mode: bool) -> str:
    """Format the human-readable scope and completeness qualifier."""
    strategy = result.diff_strategy
    if strategy is None:
        label = "unknown"
    else:
        label = _SCOPE_LABELS.get(strategy)
        if label is None and strategy.startswith(("github_", "gitlab_")):
            label = "CI push range"
        if label is None:
            label = strategy.replace("_", " ")

    completeness = result.diff_completeness
    qualifier = _COMPLETENESS_LABELS.get(completeness or "")
    if qualifier is None and completeness and completeness != "complete":
        qualifier = completeness.replace("_", " ")
    if qualifier:
        return f"Scope: {label} {'-' if ascii_mode else '·'} {_ascii(qualifier)}"
    return f"Scope: {label}"


def _format_dia(result: CheckResult, *, verbose: bool) -> list[str]:
    """Report DIA incompleteness without exposing its internal field name."""
    analysis = result.impact_analysis
    if analysis is None or (analysis.completeness == "complete" and not verbose):
        return []
    state = {
        "complete": "complete",
        "partial": "partial",
        "inconclusive": "inconclusive",
    }.get(analysis.completeness, analysis.completeness.replace("_", " "))
    line = f"DIA: {_ascii(state)}"
    if analysis.reason:
        line += f" ({_ascii(analysis.reason.replace('_', ' '))})"
    return [line]


def _format_verbose_report_metadata(result: CheckResult) -> list[str]:
    """Format report-level metadata reserved for verbose output."""
    lines: list[str] = []
    if result.diff_strategy is not None:
        completeness = result.diff_completeness or "unknown"
        lines.append(f"Diff: strategy={_ascii(result.diff_strategy)} completeness={_ascii(completeness)}")
    if result.regression_filter_completeness is not None:
        line = f"Regression filter: {_ascii(result.regression_filter_completeness)}"
        if result.regression_filter_reason:
            line += f" ({_ascii(result.regression_filter_reason.replace('_', ' '))})"
        lines.append(line)
    return lines


def _entry_sort_key(entry: CheckEntry, project_root: Path | None) -> tuple[str, int, int, str, str, str]:
    """Return a deterministic sort key for a diagnostic entry."""
    return (
        _display_path(entry.file, project_root),
        entry.line_start,
        entry.col_start,
        _ascii(entry.code),
        _ascii(entry.symbol),
        _ascii(entry.message),
    )


def _display_symbol(entry: CheckEntry, project_root: Path | None) -> str:
    """Shorten a qualified symbol using the module represented by its file."""
    symbol = _ascii(entry.symbol)
    path = Path(_display_path(entry.file, project_root))
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    module = ".".join(parts)
    if module and symbol.startswith(f"{module}."):
        return symbol[len(module) + 1 :]
    segments = symbol.split(".")
    if len(segments) > 1 and segments[-2][:1].isupper():
        return ".".join(segments[-2:])
    return segments[-1]


def _display_path(path: str, project_root: Path | None) -> str:
    """Convert an absolute path under the project root to POSIX-relative form."""
    candidate = Path(path)
    if not candidate.is_absolute():
        return candidate.as_posix()
    root = (project_root or Path.cwd()).resolve()
    try:
        return candidate.resolve().relative_to(root).as_posix()
    except ValueError:
        return candidate.as_posix()


def _status(result: CheckResult, *, fail_on_warning: bool) -> str:
    """Return the displayed status for the result and active warning policy."""
    if result.summary.error_count or (fail_on_warning and result.summary.warning_count):
        return "FAIL"
    if result.summary.warning_count:
        return "WARN"
    return "PASS"


def _color_enabled(file: str | None, *, color: bool | str | None) -> bool:
    """Return whether ANSI colors are safe for the selected output stream."""
    if file is not None or color is False or color == "never":
        return False
    if color == "always":
        return True
    if "NO_COLOR" in os.environ:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def _color_palette() -> dict[str, str]:
    """Choose a best-effort palette for the current terminal background."""
    override = os.environ.get("DOCMETHIS_COLOR_THEME", "").casefold()
    if override in _COLOR_PALETTES:
        return _COLOR_PALETTES[override]

    background = os.environ.get("COLORFGBG", "").rsplit(";", maxsplit=1)[-1]
    try:
        background_index = int(background)
    except ValueError:
        return _COLOR_PALETTES["dark"]
    return _COLOR_PALETTES["light"] if background_index >= _BRIGHT_ANSI_COLOR_START else _COLOR_PALETTES["dark"]


def _paint(value: str, *, style: str | None = None, palette: dict[str, str] | None) -> str:
    """Apply a semantic ANSI style when color output is enabled."""
    color = palette.get(style) if palette is not None and style is not None else None
    return f"{color}{value}{_RESET}" if color else value


def _inline_code(value: object) -> str:
    """Render a value as a small inline code fragment."""
    text = _ascii(value)
    return text if text.startswith("`") and text.endswith("`") else f"`{text}`"


def _count(count: int, singular: str) -> str:
    """Render a count with a basic singular/plural label."""
    return f"{count} {singular if count == 1 else singular + 's'}"


def _ascii(value: object) -> str:
    """Escape non-ASCII and control characters without changing report layout."""
    encoded = str(value).encode("ascii", "backslashreplace").decode("ascii")
    return "".join(_ascii_character(character) for character in encoded)


def _ascii_character(character: str) -> str:
    """Escape one control character while preserving printable ASCII."""
    escapes = {"\n": r"\n", "\r": r"\r", "\t": r"\t"}
    if character in escapes:
        return escapes[character]
    codepoint = ord(character)
    return f"\\x{codepoint:02x}" if codepoint < _ASCII_CONTROL_LIMIT or codepoint == _ASCII_DELETE else character
