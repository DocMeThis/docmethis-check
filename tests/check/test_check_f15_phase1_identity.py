# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Phase 1 identity fields published by Check."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from docmethis_extract_python.api import Visibility

from docmethis_check.config import CheckConfig
from docmethis_check.diagnostics import diagnostics_for_file
from docmethis_check.formatters.json import format as format_json
from docmethis_check.models import CheckResult, SymbolKind

if TYPE_CHECKING:
    from pathlib import Path


def _all_symbols_config() -> CheckConfig:
    """Enable every symbol and visibility needed by the identity contract."""
    return CheckConfig(
        include_visibility=frozenset(Visibility),
        symbol_kinds=frozenset(SymbolKind),
    )


def test_check_publishes_visibility_owner_and_span_for_attribute(tmp_path: Path) -> None:
    """An attribute diagnostic identifies its class owner instead of only its class symbol."""
    content = "class Service:\n    " + '"""Service."""' + "\n    value = 1\n"

    entries, keys = diagnostics_for_file(content, tmp_path / "module.py", tmp_path, _all_symbols_config())

    attribute = next(entry for entry in entries if entry.code == "DMT-1150")
    assert attribute.visibility == "public"
    assert attribute.owner_symbol == "module.Service"
    assert attribute.attribute_name == "value"
    assert attribute.line_end == 3
    assert attribute.col_end is not None
    assert next(key for key in keys if key.code == "DMT-1150").attribute_name == "value"


def test_check_keeps_getter_and_setter_identity_in_json(tmp_path: Path) -> None:
    """Property accessors retain separate identity fields in Check JSON."""
    content = (
        "class Service:\n"
        "    @property\n"
        "    def value(self):\n"
        "        return self._value\n"
        "\n"
        "    @value.setter\n"
        "    def value(self, value):\n"
        "        self._value = value\n"
    )

    entries, _ = diagnostics_for_file(content, tmp_path / "module.py", tmp_path, _all_symbols_config())
    properties = [entry for entry in entries if entry.code == "DMT-1140"]
    assert {entry.accessor_kind for entry in properties} == {"getter", "setter"}
    assert all(entry.visibility == "public" and entry.line_end is not None for entry in properties)

    result = CheckResult(checks=properties)
    data = json.loads(format_json(result))
    assert {check["accessor_kind"] for check in data["checks"]} == {"getter", "setter"}
    assert all(check["visibility"] == "public" for check in data["checks"])


def test_check_publishes_module_visibility_and_span(tmp_path: Path) -> None:
    """A module presence diagnostic carries the module visibility and source span."""
    entries, _ = diagnostics_for_file("VALUE = 1\n", tmp_path / "module.py", tmp_path, _all_symbols_config())

    module = next(entry for entry in entries if entry.code == "DMT-1101")
    assert module.symbol_kind is SymbolKind.MODULE
    assert module.visibility == "public"
    assert module.line_end == 1
