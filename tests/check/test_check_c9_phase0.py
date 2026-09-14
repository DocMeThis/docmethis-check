# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Tests for the C9 phase 0 human-readable formatter."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import TYPE_CHECKING

from docmethis_check.formatters.text import format as format_text
from docmethis_check.models import AnnotationPlacement, CheckEntry, CheckResult, CheckSummary, ImpactAnalysis, SymbolKind

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _entry(
    *,
    severity: str = "error",
    code: str = "DMT-3001",
    symbol: str = "module.calculate_total",
    file: str = "pricing.py",
    line_start: int = 6,
    message: str = "Return is not documented",
    **kwargs: object,
) -> CheckEntry:
    """Build a compact entry for formatter tests."""
    return CheckEntry(
        severity=severity,  # type: ignore[arg-type]
        code=code,
        symbol=symbol,
        file=file,
        line_start=line_start,
        col_start=1,
        message=message,
        **kwargs,
    )


def _result(
    *,
    entries: list[CheckEntry] | None = None,
    passed: int = 0,
    warnings: int = 0,
    errors: int = 0,
    files: list[str] | None = None,
    **kwargs: object,
) -> CheckResult:
    """Build a result with explicit summary counters."""
    return CheckResult(
        checks=entries or [],
        summary=CheckSummary(pass_count=passed, warning_count=warnings, error_count=errors),
        checked_files=files or [],
        **kwargs,
    )


def test_empty_result_is_compact_pass() -> None:
    """An empty result has a useful success summary."""
    output = format_text(_result(passed=47, files=[f"file_{index}.py" for index in range(12)]))

    assert "DocMeThis Check — PASS\nScope: unknown" in output
    assert "No documentation issues found." in output
    assert "47 checks · 47 passed" in output
    assert "12 files checked" in output
    assert "Use --format json" in output


def test_warning_policy_changes_status_but_not_diagnostic_severity() -> None:
    """The same factual result can be WARN or FAIL under different policies."""
    result = _result(entries=[_entry(severity="warning")], warnings=1, files=["pricing.py"])

    tolerated = format_text(result, fail_on_warning=False)
    blocking = format_text(result, fail_on_warning=True)

    assert "DocMeThis Check — WARN" in tolerated
    assert "DocMeThis Check — FAIL" in blocking
    assert "WARNING  DMT-3001" in blocking


def test_diagnostics_are_grouped_and_sorted_with_relative_paths(tmp_path: Path) -> None:
    """Absolute paths are normalized and diagnostics have stable order."""
    first = _entry(
        code="DMT-2401",
        symbol="module.create_order",
        file=str(tmp_path / "orders.py"),
        severity="warning",
        line_start=42,
        section="Raises",
        expected="ValueError",
    )
    second = _entry(
        code="DMT-2101",
        symbol="module.create_order",
        file=str(tmp_path / "orders.py"),
        severity="error",
        line_start=42,
        section="Parameters",
        parameter_name="discount",
        expected="float",
    )
    result = _result(entries=[first, second], warnings=1, errors=1, files=[str(tmp_path / "orders.py")])

    output = format_text(result, project_root=tmp_path)

    assert output.index("ERROR    DMT-2101") < output.index("WARNING  DMT-2401")
    assert "└─ create_order · 42:1" in output
    assert str(tmp_path) not in output
    assert "Required: parameter `discount`" in output
    assert "Required: `ValueError` in Raises" in output


def test_expected_returns_and_symbol_kinds_are_human_readable() -> None:
    """Symbols are rendered as short names in their group headers."""
    function = _entry(section="Returns", expected="int")
    class_entry = _entry(symbol="module.Order", symbol_kind=SymbolKind.CLASS, expected="class contract")
    result = _result(entries=[function, class_entry], errors=2, files=["pricing.py"])

    output = format_text(result)

    assert "└─ calculate_total · 6:1" in output
    assert "Required: `class contract`" in output
    required_line = next(line for line in output.splitlines() if "Required: `class contract`" in line)
    assert required_line.startswith("│")
    assert "├─ Order · 6:1" in output


def test_symbol_groups_share_context_and_distinguish_contract_states(tmp_path: Path) -> None:
    """A symbol gets one context while current and changed contracts stay distinct."""
    source = tmp_path / "demo_shop" / "catalog.py"
    source.parent.mkdir()
    source.write_text(
        "class Catalog:\n"
        '    """Catalog."""\n'
        "\n"
        "    def find(self) -> Product:\n"
        '        """Find a product."""\n'
        "        raise Exception\n",
        encoding="utf-8",
    )
    entries = [
        _entry(
            code="DMT-4001",
            symbol="demo_shop.catalog.Catalog.find",
            file=str(source),
            line_start=4,
            section="Raises",
            expected="Exception",
            message="Exception raised by a method is not documented in Raises: Exception",
        ),
        _entry(
            code="DMT-4002",
            symbol="demo_shop.catalog.Catalog.find",
            file=str(source),
            line_start=4,
            severity="error",
            section="Raises",
            observed="ProductNotFound",
            message="exception 'ProductNotFound' is documented but not observed",
        ),
        _entry(
            code="DMT-4201",
            symbol="demo_shop.catalog.Catalog.find",
            file=str(source),
            line_start=4,
            message="Observable contract change; documentation unchanged (raises Exception)",
            expected="added : Exception:local",
            dia={"direction": "added", "value": "Exception:local", "provenance": "local"},
        ),
        _entry(
            code="DMT-4202",
            symbol="demo_shop.catalog.Catalog.find",
            file=str(source),
            line_start=4,
            severity="warning",
            message="Observable contract change; documentation unchanged (raises ProductNotFound)",
            expected="removed : ProductNotFound:local",
            dia={"direction": "removed", "value": "ProductNotFound:local", "provenance": "local"},
        ),
    ]
    result = _result(
        entries=entries,
        passed=1,
        warnings=1,
        errors=2,
        files=[str(source)],
    )
    result.analysis_errors = [
        {"file": str(tmp_path / "demo_shop" / "pricing.py"), "type": "parse_error", "message": "Unable to parse the file."},
    ]

    output = format_text(result, project_root=tmp_path)

    assert output.count("└─ Catalog.find · 4:5") == 1
    assert "ERROR    DMT-4001  `Exception` is not documented in `Raises`." in output
    assert "ERROR    DMT-4002  `ProductNotFound` is documented in `Raises` but not observed." in output
    assert "ERROR    DMT-4201  Observable contract added: `Exception` (local)." in output
    assert "WARNING  DMT-4202  Observable contract removed: `ProductNotFound` (local)." in output
    assert "Required: `Exception` in Raises" in output
    assert "Expected:" not in output
    assert "1 file checked · 1 skipped" in output
    assert "Skipped: demo_shop/pricing.py — syntax error" in output


def test_default_hides_internal_metadata_and_verbose_reveals_useful_facts() -> None:
    """Verbose output adds metadata without changing the core diagnostic layout."""
    entry = _entry(
        visibility="public",
        observed="actual result",
        granularity="section",
        error_type="ReturnsMissing",
        docstring_sha256="a" * 64,
    )
    result = _result(entries=[entry], errors=1, files=["pricing.py"], diff_strategy="local_working_tree")

    normal = format_text(result)
    verbose = format_text(result, verbose=True)

    assert "actual result" not in normal
    assert "ReturnsMissing" not in normal
    assert "a" * 64 not in normal
    assert "Visibility: public" in verbose
    assert "Observed: actual result" in verbose
    assert "Error type: ReturnsMissing" in verbose
    assert "Diff: strategy=local_working_tree" in verbose


def test_partial_scope_and_inconclusive_dia_are_explicit() -> None:
    """Partial diff and DIA analysis are not presented as complete."""
    result = _result(
        passed=1,
        files=["pricing.py"],
        diff_strategy="local_working_tree",
        diff_completeness="partial",
        diff_reason="no_ci_environment_detected",
        impact_analysis=ImpactAnalysis(completeness="inconclusive", reason="missing_diff_base"),
    )

    output = format_text(result)

    assert "Scope: local changes · partial" in output
    assert "DIA: inconclusive (missing diff base)" in output
    assert "impact_analysis.completeness" not in output


def test_ascii_escaping_keeps_messages_on_one_line() -> None:
    """Special and non-ASCII characters are escaped deterministically."""
    result = _result(
        entries=[_entry(message="échec\nnext\tline\x1b")],
        errors=1,
        files=["pricing.py"],
    )

    output = format_text(result)

    assert "é" not in output
    assert r"\xE9".lower() in output.lower()
    assert r"\n" in output
    assert r"\t" in output
    assert r"\x1b" in output


def test_ascii_mode_replaces_layout_glyphs(tmp_path: Path) -> None:
    """ASCII mode keeps the report tree and separators portable."""
    source = tmp_path / "module.py"
    source.write_text("def calculate_total():\n    return 1\n", encoding="utf-8")
    result = _result(
        entries=[
            _entry(symbol="module.calculate_total", file=str(source), line_start=1),
            _entry(symbol="module.other", file=str(source), line_start=3),
        ],
        errors=2,
        files=[str(source)],
    )
    result.analysis_errors = [
        {"file": str(tmp_path / "broken.py"), "type": "parse_error", "message": "Unable to parse the file."},
    ]

    output = format_text(result, project_root=tmp_path, ascii_mode=True)

    assert output.startswith("DocMeThis Check - FAIL\nScope: unknown")
    assert "+- calculate_total - 1:1" in output
    assert "|  1 | def calculate_total():" in output
    assert "\\- other - 3:1" in output
    assert "1 skipped" in output
    assert "Skipped: broken.py - syntax error" in output
    assert "-" * 60 in output
    assert not any(glyph in output for glyph in "—·─├└│")


def test_syntax_error_is_rendered_as_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipped syntax errors use the error color in a TTY."""
    monkeypatch.setenv("DOCMETHIS_COLOR_THEME", "dark")

    class TTY(io.StringIO):
        """String stream pretending to be a terminal."""

        def isatty(self) -> bool:
            """Report terminal capability for the formatter."""
            return True

    result = _result(passed=1)
    result.analysis_errors = [{"file": "broken.py", "type": "parse_error", "message": "Unable to parse the file."}]
    tty = TTY()

    with redirect_stdout(tty):
        output = format_text(result, color=True)

    assert "Skipped: broken.py — \x1b[91msyntax error\x1b[0m" in output


def test_local_annotation_placement_controls_source_frames(tmp_path: Path) -> None:
    """Local frames follow the configured annotation placement policy."""
    source = tmp_path / "module.py"
    source.write_text(
        "def calculate_total(value: int) -> int:\n"
        '    """Summary."""\n'
        "    if value < 0:\n"
        "        raise ValueError\n"
        "    return value\n",
        encoding="utf-8",
    )
    entries = [
        _entry(
            code="DMT-2001",
            symbol="module.calculate_total",
            file=str(source),
            line_start=1,
            message="Parameter is not documented.",
            docstring_line=2,
            annotation_line=1,
            section="Parameters",
            parameter_name="value",
            expected="int",
        ),
        _entry(
            code="DMT-4001",
            symbol="module.calculate_total",
            file=str(source),
            line_start=1,
            message="Exception is not documented.",
            docstring_line=2,
            annotation_line=4,
            section="Raises",
            expected="ValueError",
        ),
    ]
    result = _result(entries=entries, errors=2, files=[str(source)])

    signature = format_text(result, project_root=tmp_path)
    docstring = format_text(result, project_root=tmp_path, annotation_placement=AnnotationPlacement.DOCSTRING)
    precise = format_text(result, project_root=tmp_path, annotation_placement=AnnotationPlacement.PRECISE)

    assert signature.count("calculate_total · 1:1") == 1
    assert docstring.count("calculate_total · 2:5") == 1
    assert '   2 │     """Summary."""' in docstring
    assert precise.count("calculate_total ·") == 2
    assert "├─ calculate_total · 1:1" in precise
    assert "└─ calculate_total · 4:9" in precise


def test_signature_placement_anchors_multiple_attribute_diagnostics_on_class(tmp_path: Path) -> None:
    """Signature placement uses the symbol definition, not the first attribute."""
    source = tmp_path / "catalog.py"
    source.write_text(
        'class Product:\n    """Product."""\n    ean: int\n    lolwut: int\n',
        encoding="utf-8",
    )
    entries = [
        _entry(
            code="DMT-1150",
            symbol="catalog.Product",
            file=str(source),
            line_start=1,
            message="Public attribute is not documented in Attributes: ean",
            symbol_kind=SymbolKind.CLASS,
            attribute_name="ean",
            section="Attributes",
            annotation_line=3,
        ),
        _entry(
            code="DMT-1150",
            symbol="catalog.Product",
            file=str(source),
            line_start=1,
            message="Public attribute is not documented in Attributes: lolwut",
            symbol_kind=SymbolKind.CLASS,
            attribute_name="lolwut",
            section="Attributes",
            annotation_line=4,
        ),
    ]
    result = _result(entries=entries, errors=2, files=[str(source)])

    signature = format_text(result, project_root=tmp_path, ascii_mode=True)
    precise = format_text(
        result,
        project_root=tmp_path,
        ascii_mode=True,
        annotation_placement=AnnotationPlacement.PRECISE,
    )

    assert signature.count("Product - 1:1") == 1
    assert "Product - 3:5" not in signature
    assert "Product - 4:5" not in signature
    assert "ean" in signature
    assert "lolwut" in signature
    assert "Product - 3:5" in precise
    assert "Product - 4:5" in precise


def test_file_output_is_quiet_and_has_no_ansi(tmp_path: Path) -> None:
    """File output returns the report without writing to stdout or adding color."""
    output_file = tmp_path / "report.txt"
    stream = io.StringIO()
    result = _result(passed=1, files=["pricing.py"])

    with redirect_stdout(stream):
        output = format_text(result, file=str(output_file), color=True)

    assert stream.getvalue() == ""
    assert output_file.read_text(encoding="utf-8") == output
    assert "\x1b[" not in output


def test_color_is_used_only_for_a_compatible_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTY color is opt-in through capability detection, never through a pipe."""
    monkeypatch.setenv("DOCMETHIS_COLOR_THEME", "dark")

    class TTY(io.StringIO):
        """String stream pretending to be a terminal."""

        def isatty(self) -> bool:
            """Report terminal capability for the formatter."""
            return True

    tty = TTY()
    result = _result(passed=1, files=["pricing.py"])
    with redirect_stdout(tty):
        output = format_text(result, color=True)

    assert "\x1b[32mPASS\x1b[0m" in output

    pipe = io.StringIO()
    with redirect_stdout(pipe):
        output = format_text(result, color=True)
    assert "\x1b[" not in output


def test_light_palette_colors_ruff_like_diagnostic_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Light terminals use 4-bit accents throughout a symbol-oriented diagnostic block."""
    monkeypatch.delenv("DOCMETHIS_COLOR_THEME", raising=False)
    monkeypatch.setenv("COLORFGBG", "0;15")

    source = tmp_path / "demo_shop" / "catalog.py"
    source.parent.mkdir()
    source.write_text('class Product:\n    """Product."""\n    stock: int\n    ean: int\n', encoding="utf-8")

    class TTY(io.StringIO):
        """String stream pretending to be a terminal."""

        def isatty(self) -> bool:
            """Report terminal capability for the formatter."""
            return True

    result = _result(
        entries=[
            _entry(
                code="DMT-1150",
                symbol="demo_shop.catalog.Product",
                file=str(source),
                line_start=1,
                message="Public attribute is not documented in Attributes: ean",
                symbol_kind=SymbolKind.CLASS,
                attribute_name="ean",
                section="Attributes",
                visibility="public",
                dia={
                    "evidence": {
                        "path": [
                            "demo_shop.catalog.Product",
                            "demo_shop.pricing._read_discount",
                        ],
                    },
                },
            ),
            _entry(
                severity="warning",
                code="DMT-2401",
                file=str(source),
                message="Raised exception is not documented",
            ),
        ],
        warnings=1,
        errors=1,
        files=[str(source)],
    )
    tty = TTY()

    with redirect_stdout(tty):
        output = format_text(result, color=True, project_root=tmp_path)

    assert (
        "\x1b[31mERROR\x1b[0m    \x1b[1;31mDMT-1150\x1b[0m  Public attribute is not documented in `Attributes`: `ean`" in output
    )
    assert "\x1b[36mdemo_shop/catalog.py\x1b[0m" in output
    assert "├─ \x1b[36mProduct\x1b[0m · 1:1" in output
    assert "│  \x1b[34m1\x1b[0m \x1b[34m│\x1b[0m class Product:" in output
    assert "\x1b[31m^^^^^^^^^^^^^^\x1b[0m" in output
    assert "│                     \x1b[36mvia \x1b[0mdemo_shop.pricing._read_discount" in output
    assert "\x1b[33mWARNING\x1b[0m  \x1b[1;33mDMT-2401\x1b[0m" in output


def test_never_color_disables_ansi_even_on_a_tty() -> None:
    """The formatter's explicit color switch wins over TTY detection."""

    class TTY(io.StringIO):
        """String stream pretending to be a terminal."""

        def isatty(self) -> bool:
            """Report terminal capability for the formatter."""
            return True

    result = _result(entries=[_entry()], errors=1, files=["pricing.py"])
    tty = TTY()

    with redirect_stdout(tty):
        output = format_text(result, color="never")

    assert "\x1b[" not in output
    assert "ERROR    DMT-3001  Return is not documented" in output
