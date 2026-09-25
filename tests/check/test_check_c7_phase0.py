# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""C7 phase 0 tests for the Check contract and configurable scope."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from docmethis_extract_python.api import PropertyAccessor, Visibility

from docmethis_check.config import CheckConfig, load_check_config, parse_property_accessors, parse_symbol_kinds
from docmethis_check.formatters.json import format as format_json
from docmethis_check.models import CheckEntry, CheckResult, SymbolKind

if TYPE_CHECKING:
    from pathlib import Path


def test_config_reads_symbol_kinds_from_pyproject_and_cli(tmp_path: Path) -> None:
    """Phase 0 exposes the scope of symbol kinds."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.docmethis.check]\nsymbol-kinds = ["class", "module"]\n',
        encoding="utf-8",
    )

    config = load_check_config(tmp_path)
    override = load_check_config(tmp_path, symbol_kinds=parse_symbol_kinds("function,module"))

    assert config.symbol_kinds == frozenset({SymbolKind.CLASS, SymbolKind.MODULE})
    assert override.symbol_kinds == frozenset({SymbolKind.FUNCTION, SymbolKind.MODULE})


def test_config_reads_property_accessors_with_both_as_default(tmp_path: Path) -> None:
    """Property policy defaults to both source accessors and is configurable."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.docmethis.check]\nproperty-accessors = ["getter"]\n',
        encoding="utf-8",
    )

    configured = load_check_config(tmp_path)
    default = CheckConfig()
    override = load_check_config(tmp_path, property_accessors=parse_property_accessors("setter"))

    assert default.property_accessors == frozenset({PropertyAccessor.GETTER, PropertyAccessor.SETTER})
    assert configured.property_accessors == frozenset({PropertyAccessor.GETTER})
    assert override.property_accessors == frozenset({PropertyAccessor.SETTER})


def test_parse_property_accessors_validates_values() -> None:
    assert parse_property_accessors("getter,setter") == frozenset({PropertyAccessor.GETTER, PropertyAccessor.SETTER})

    with pytest.raises(ValueError, match=r"property_accessors\[\].*'getter'.*'setter'"):
        parse_property_accessors("deleter")


def test_check_config_validates_property_accessors_directly() -> None:
    with pytest.raises(TypeError, match="property_accessors must contain only PropertyAccessor instances"):
        CheckConfig(property_accessors=frozenset({"getter"}))

    with pytest.raises(ValueError, match="property_accessors must contain at least one accessor"):
        CheckConfig(property_accessors=frozenset())


def test_parse_symbol_kinds_uses_enum_normalization() -> None:
    """Parsing symbol kinds reuses the shared enum helper."""
    assert parse_symbol_kinds("class,module") == frozenset({SymbolKind.CLASS, SymbolKind.MODULE})

    with pytest.raises(ValueError, match=r"symbol_kinds\[\].*'function'.*'module'"):
        parse_symbol_kinds("service")


def test_check_config_validates_symbol_kinds_directly() -> None:
    """A configuration built directly keeps a strictly typed contract."""
    with pytest.raises(TypeError, match="symbol_kinds must contain only SymbolKind instances"):
        CheckConfig(symbol_kinds=frozenset({"class"}))

    with pytest.raises(ValueError, match="symbol_kinds must contain at least one symbol kind"):
        CheckConfig(symbol_kinds=frozenset())


def test_check_config_validates_include_visibility_directly() -> None:
    """Direct validation also covers the visibility container."""
    with pytest.raises(TypeError, match="include_visibility must contain only Visibility instances"):
        CheckConfig(include_visibility=frozenset({SymbolKind.CLASS}))

    with pytest.raises(ValueError, match="include_visibility must contain at least one visibility"):
        CheckConfig(include_visibility=frozenset())

    config = CheckConfig(include_visibility=frozenset({Visibility.PUBLIC}))
    assert config.include_visibility == frozenset({Visibility.PUBLIC})


def test_json_serializes_symbol_kind(tmp_path: Path) -> None:
    """Report JSON v1 exposes the symbol kind."""
    outcome = CheckResult()
    outcome.checked_files = ["module.py"]
    outcome.summary.error_count = 1
    outcome.checks.append(
        CheckEntry(
            severity="error",
            code="DMT-1110",
            symbol="module.Service",
            file="module.py",
            line_start=1,
            col_start=1,
            message="Docstring missing.",
            symbol_kind=SymbolKind.CLASS,
            accessor_kind="setter",
        )
    )

    data = json.loads(format_json(outcome, file=str(tmp_path / "report.json")))

    assert data["version"] == 1
    assert data["checks"][0]["symbol_kind"] == "class"
    assert data["checks"][0]["accessor_kind"] == "setter"
