# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""CLI for the check module."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docmethis_check.config import (
    load_check_config,
    parse_include_visibility,
    parse_path_filters,
    parse_property_accessors,
    parse_symbol_kinds,
)
from docmethis_check.formatters.github import format as format_github
from docmethis_check.formatters.json import format as format_json
from docmethis_check.formatters.text import format as format_text
from docmethis_check.runner import run_check


def create_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns
    -------
    argparse.ArgumentParser
        The configured command-line argument parser for the docmethis_check tool, including all defined options for project path,
        output files, git diff range, cache control, warning behavior, visibility and symbol kind filters, verification modes,
        annotation placement, missing base handling, method exception ownership, and documentation impact analysis settings.

    """
    parser = argparse.ArgumentParser(
        prog="docmethis_check",
        description="Check consistency between code and existing docstrings.",
    )
    parser.add_argument(
        "project",
        type=Path,
        help="Path to the Python project to analyze",
    )
    parser.add_argument(
        "--github-output-file",
        type=Path,
        default=None,
        help="GitHub annotations output file (default: none)",
    )
    parser.add_argument(
        "--json-output-file",
        type=Path,
        default=None,
        help="Canonical JSON report output file (default: none)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Stdout report format: text (default) or json; ignored when an output file is set",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include additional metadata in text output (default: disabled)",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "never", "always"),
        default="auto",
        help="Color text output: auto (default), never, or always",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        dest="ascii_mode",
        help="Use ASCII-only tree and separator glyphs (default: Unicode layout)",
    )
    parser.add_argument(
        "--git-diff",
        type=str,
        default=None,
        help="Git range to analyze (default: local changes since HEAD)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable writing .docmethis_cache.json for this run (default: cache enabled)",
    )
    # For configuration-backed options below, None means no CLI override:
    # the project setting wins when present; otherwise the built-in default applies.
    parser.add_argument(
        "--base-ref",
        type=str,
        default=None,
        help="Base branch for non-linear pushes (default: unset)",
    )
    parser.add_argument(
        "--on-nonlinear-push-without-base",
        type=str,
        default=None,
        choices=("fail", "head_commit", "warn"),
        help=("Behavior for a non-linear push without a base: fail, head_commit, or warn (default: fail)"),
    )
    warning_group = parser.add_mutually_exclusive_group()
    warning_group.add_argument(
        "--fail-on-warning",
        dest="fail_on_warning",
        action="store_true",
        default=None,
        help="Fail the check if at least one warning is emitted (default: false)",
    )
    warning_group.add_argument(
        "--no-fail-on-warning",
        dest="fail_on_warning",
        action="store_false",
        help="Do not fail on warnings, even if enabled by pyproject.toml (default: not selected)",
    )
    parser.add_argument(
        "--include-visibility",
        type=str,
        default=None,
        help="Visibilities to check, comma-separated: public,protected,private (default: public only)",
    )
    parser.add_argument(
        "--symbol-kinds",
        type=str,
        default=None,
        help=("Symbol kinds to check, comma-separated: function,method,class,module (default: function,method,class)"),
    )
    parser.add_argument(
        "--property-accessors",
        type=str,
        default=None,
        help="Property accessors to check, comma-separated: getter,setter (default: getter,setter)",
    )
    parser.add_argument(
        "--exclude-paths",
        type=str,
        default=None,
        help="Project-relative files or directories to exclude from Check and DIA, comma-separated (default: configuration)",
    )
    parser.add_argument(
        "--dia-exclude-paths",
        type=str,
        default=None,
        help="Project-relative files or directories to exclude from DIA only, comma-separated (default: configuration)",
    )
    parser.add_argument(
        "--profile",
        type=str,
        choices=("loose", "standard", "strict"),
        default=None,
        help=("Built-in severity policy: loose, standard, or strict (default: standard); explicit per-code overrides still win"),
    )
    parser.add_argument(
        "--check-mode",
        type=str,
        default=None,
        choices=("regression", "catchup"),
        help="Verification mode: regression or catchup (default: regression)",
    )
    parser.add_argument(
        "--annotation-placement",
        type=str,
        default=None,
        choices=("signature", "docstring", "precise"),
        help="Annotation placement: signature, docstring, precise (default: signature)",
    )
    parser.add_argument(
        "--on-missing-base",
        type=str,
        default=None,
        choices=("fail", "emit_all"),
        help="Behavior when the base is unavailable: emit_all or fail (default: emit_all)",
    )
    parser.add_argument(
        "--method-exception-contract",
        type=str,
        default=None,
        choices=("callable", "class_aggregate"),
        help="Exception documentation owner: callable or class_aggregate (default: callable)",
    )
    dia_group = parser.add_mutually_exclusive_group()
    dia_group.add_argument(
        "--dia",
        dest="dia",
        action="store_true",
        default=None,
        help=("Enable documentation impact analysis (DIA): BASE→HEAD model and impact_analysis (default: enabled)"),
    )
    dia_group.add_argument(
        "--no-dia",
        dest="dia",
        action="store_false",
        help="Disable DIA (overrides the configured default)",
    )
    return parser


def _identical_output_files(github_output_file: Path | None, json_output_file: Path | None) -> bool:
    """Return whether the two configured destinations refer to the same file.

    Parameters
    ----------
    github_output_file : Path | None
        The path to the GitHub output file, or None if not configured.
    json_output_file : Path | None
        The path to the JSON output file, or None if not configured.

    Returns
    -------
    bool
        True if both files are configured and resolve to the same path, False otherwise.

    """
    return (
        github_output_file is not None
        and json_output_file is not None
        and github_output_file.resolve() == json_output_file.resolve()
    )


def main(argv: list[str] | None = None) -> int:
    """Run the check module's main entry point.

    Parameters
    ----------
    argv : list[str] | None = None
        Optional list of command-line arguments to parse instead of using the ones passed to the program. When set to None, the
        arguments are read from sys.argv.

    Returns
    -------
    int
        The integer exit code indicating the outcome of the check. A return value of 0 signifies that no errors or warnings were
        encountered (or warnings were tolerated), 1 indicates that errors were found or warnings were treated as failures, and 2
        is returned when an unexpected exception occurs during execution.

    """
    parser = create_parser()
    args = parser.parse_args(argv)
    if _identical_output_files(args.github_output_file, args.json_output_file):
        parser.error("--github-output-file and --json-output-file must refer to different files.")

    try:
        include_visibility = parse_include_visibility(args.include_visibility) if args.include_visibility else None
        symbol_kinds = parse_symbol_kinds(args.symbol_kinds) if args.symbol_kinds else None
        property_accessors = parse_property_accessors(args.property_accessors) if args.property_accessors else None
        exclude_paths = parse_path_filters(args.exclude_paths) if args.exclude_paths is not None else None
        dia_exclude_paths = parse_path_filters(args.dia_exclude_paths) if args.dia_exclude_paths is not None else None
        config = load_check_config(
            args.project,
            fail_on_warning=args.fail_on_warning,
            include_visibility=include_visibility,
            symbol_kinds=symbol_kinds,
            property_accessors=property_accessors,
            profile=args.profile,
            check_mode=args.check_mode,
            annotation_placement=args.annotation_placement,
            on_missing_base=args.on_missing_base,
            method_exception_contract=args.method_exception_contract,
            dia=args.dia,
            exclude_paths=exclude_paths,
            dia_exclude_paths=dia_exclude_paths,
        )
        result = run_check(
            project=args.project,
            git_diff=args.git_diff,
            write_cache=not args.no_cache,
            base_ref=args.base_ref,
            on_nonlinear_push_without_base=args.on_nonlinear_push_without_base,
            config=config,
        )

        if args.json_output_file is not None:
            format_json(result, file=str(args.json_output_file))

        if args.github_output_file is not None:
            format_github(
                result,
                file=str(args.github_output_file),
                annotation_placement=config.annotation_placement,
            )

        if args.json_output_file is None and args.github_output_file is None:
            if args.format == "json":
                format_json(result)
            else:
                format_text(
                    result,
                    fail_on_warning=config.fail_on_warning,
                    project_root=args.project.resolve(),
                    verbose=args.verbose,
                    color=args.color,
                    ascii_mode=args.ascii_mode,
                    annotation_placement=config.annotation_placement,
                )
    except Exception as exc:  # noqa: BLE001 - CLI boundary: convert all errors to user-facing text.
        sys.stderr.write(f"docmethis_check: {exc}\n")
        return 2

    if result.summary.error_count > 0 or (config.fail_on_warning and result.summary.warning_count > 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
