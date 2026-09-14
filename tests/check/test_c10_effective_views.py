# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""C10 regressions for inherited views and unknown documentation contracts."""

from __future__ import annotations

from textwrap import dedent
from typing import TYPE_CHECKING

from docmethis_extract_python.static_extraction.__main__ import analyze_project
from docmethis_extract_python.static_extraction.configuration import load_configuration

from docmethis_check.config import CheckConfig
from docmethis_check.dia import (
    DiaEmissionContext,
    _behavioral_delta,
    _build_symbol_contexts,
    _emit_dia_delta,
    _extract_behavioral_keys,
    _index_symbols,
)
from docmethis_check.formatters.github import format as format_github
from docmethis_check.models import CheckResult, ImpactAnalysis

if TYPE_CHECKING:
    from pathlib import Path

    from docmethis_extract_python.static_extraction.models import ProjectRecord


def _analyze_source(tmp_path: Path, identifier_name: str, source: str) -> ProjectRecord:
    """Analyze an isolated Python source without a cache or Git repository."""
    root = tmp_path / identifier_name
    root.mkdir()
    (root / "module.py").write_text(dedent(source).strip() + "\n", encoding="utf-8")
    return analyze_project(
        root,
        load_configuration(root),
        skip_dynamic=True,
        write_cache=False,
    )


def test_dia_emits_purely_inherited_views_and_evidence(tmp_path: Path) -> None:
    """A parent change reaches Child and GrandChild views without local duplicates."""
    source_base = '''
    class Base:
        def run(self, value: int) -> int:
            """Contrat parent."""
            return value

    class Child(Base):
        pass

    class GrandChild(Child):
        pass
    '''
    source_head = '''
    class Base:
        def run(self, value: int) -> int:
            """Contrat parent."""
            if value < 0:
                raise ValueError("negative value")
            return value

    class Child(Base):
        pass

    class GrandChild(Child):
        pass
    '''
    base = _analyze_source(tmp_path, "base", source_base)
    head = _analyze_source(tmp_path, "head", source_head)
    contexts = _build_symbol_contexts(head.modules)

    for symbol_name in ("module.Child.run", "module.GrandChild.run"):
        analysis_context = contexts[symbol_name]
        assert analysis_context.documentation_origin == "inherited"
        assert analysis_context.inherited_contract_source == "module.Base.run"
        assert analysis_context.method_origin.value == "inherited"

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())
    deltas = _behavioral_delta(base_keys, head_keys)
    symbols = {delta.symbol for delta in deltas if delta.field == "exception"}
    assert {"module.Base.run", "module.Child.run", "module.GrandChild.run"} <= symbols
    assert sum(delta.symbol == "module.Child.run" for delta in deltas) == 1

    index = _index_symbols(head)
    assert "module.Child.run" in index
    assert "module.GrandChild.run" in index

    emission = DiaEmissionContext(
        configuration=CheckConfig(),
        records_head=index,
        records_base=_index_symbols(base),
    )
    delta = next(delta for delta in deltas if delta.symbol == "module.Child.run" and delta.field == "exception")
    outcome = _emit_dia_delta(emission, delta)

    assert outcome.impact is not None
    assert outcome.impact.status == "contradiction"
    assert outcome.impact.fixable is False
    assert outcome.entry is not None
    assert outcome.entry.docstring_sha256 is None
    assert outcome.meta is not None
    assert outcome.meta.fixable is False


def test_github_unknown_does_not_report_changed_docstring(tmp_path: Path) -> None:
    """A contract unknown is displayed as not resolved, not as changed."""
    outcome = CheckResult(
        impact_analysis=ImpactAnalysis(
            completeness="complete",
            impacts=[
                {
                    "symbol": "module.Child.run",
                    "impact_kind": "io_effect",
                    "value": "io.writes_files",
                    "status": "changed_unverified",
                    "symbol_context": {
                        "base": {"documentation_origin": "unknown"},
                        "head": {"documentation_origin": "unknown"},
                    },
                }
            ],
        )
    )

    output = format_github(outcome, str(tmp_path / "annotations.txt"))

    assert "documentation contract unresolved" in output
    assert "docstring changed" not in output
