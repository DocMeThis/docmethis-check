# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Phase 1 C10 DIA tests: complete symmetric BASE model and raw_records API."""

from __future__ import annotations

import json
import subprocess
from textwrap import dedent
from typing import TYPE_CHECKING

from docmethis_extract_python.static_extraction.__main__ import analyze_project
from docmethis_extract_python.static_extraction.callgraph_ast import build_callgraph
from docmethis_extract_python.static_extraction.configuration import load_configuration
from docmethis_extract_python.static_extraction.models import (
    CallgraphEdge,
    Confidence,
    ExceptionRecord,
    MethodOrigin,
    ProjectRecord,
    Provenance,
    iter_functions,
)
from docmethis_verify.dmt import DMT_4201, DMT_4202, DMT_5202, DMT_7302, DMT_7303

from docmethis_check import dia, runner
from docmethis_check.config import CheckConfig
from docmethis_check.dia import (
    BehavioralKey,
    DeclarativeChange,
    DeltaBehavioral,
    DiaEmissionContext,
    EmissionDelta,
    _affected_callers,
    _api_diff,
    _api_diff_visible,
    _base_head_models,
    _behavioral_delta,
    _build_symbol_contexts,
    _confirmed_exception_removal,
    _emit_dia_delta,
    _extract_behavioral_keys,
    _extract_declarative_signatures,
    _impact_to_dict,
    _mark_affected_callers,
    _propagation_path,
)
from docmethis_check.formatters.github import format as format_github
from docmethis_check.formatters.json import format as format_json
from docmethis_check.git_diff import ChangedFile, DiffRange, discover_changed_python_files
from docmethis_check.models import CheckEntry, CheckResult, ImpactAnalysis

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from docmethis_extract_python.static_extraction.models import ModuleRecord


def _write(source_file: Path, payload: str) -> None:
    """Write a dedented Python test file ending with a newline."""
    source_file.write_text(dedent(payload).strip() + "\n", encoding="utf-8")


def _git(tmp_path: Path, *args: str) -> None:
    """Run a Git command in the temporary repository."""
    subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True)


def _initialize_repo(tmp_path: Path) -> None:
    """Initialize a temporary Git repository with a commit identity."""
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "c10@example.com")
    _git(tmp_path, "config", "user.name", "C10")


def _commit(tmp_path: Path, message: str) -> None:
    """Commit the entire working tree."""
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", message)


def _sha_head(tmp_path: Path) -> str:
    """Returns the SHA of HEAD of repository temporary."""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_analyze_project_exposes_independent_raw_record_copies(tmp_path: Path) -> None:
    """``raw_records`` contains pre-propagation copies that propagation does not mutate."""
    _write(tmp_path / "source.py", 'def origin() -> None:\n    raise ValueError("x")')
    _write(
        tmp_path / "client.py",
        "from source import origin\n\n\ndef caller() -> None:\n    origin()",
    )
    configuration = load_configuration(tmp_path)
    raw_records: list[ModuleRecord] = []
    project_record = analyze_project(
        tmp_path,
        configuration,
        skip_dynamic=True,
        write_cache=False,
        raw_records=raw_records,
    )

    raw_client = next(m for m in raw_records if m.module_name == "client")
    head_client = next(m for m in project_record.modules if m.module_name == "client")

    assert len(raw_records) == 2
    assert (raw_client.functions[0].exceptions or []) == []
    assert [e.exception_type for e in head_client.functions[0].exceptions or []] == ["ValueError"]


def test_base_head_models_reconstruction_is_symmetric(tmp_path: Path) -> None:
    """BASE reconstruction is complete and symmetric with HEAD."""
    _initialize_repo(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _write(pkg / "__init__.py", "")
    _write(pkg / "uses.py", "def duplicate(x: int) -> int:\n    return x * 2")
    _write(pkg / "change.py", "def f() -> int:\n    return duplicate(1)")
    _write(pkg / "vieux.py", 'def remnant() -> str:\n    return "old"')
    _commit(tmp_path, "base")
    sha_base = _sha_head(tmp_path)

    (pkg / "vieux.py").unlink()
    _write(
        pkg / "change.py",
        'def f() -> int:\n    return duplicate(1)\n\n\ndef boom() -> None:\n    raise ValueError("x")',
    )
    _write(pkg / "new.py", "def fresh() -> int:\n    return 1")
    _commit(tmp_path, "head")
    sha_head = _sha_head(tmp_path)

    diff_files, diff_range = discover_changed_python_files(tmp_path, git_diff=f"{sha_base}..{sha_head}")
    configuration = load_configuration(tmp_path)
    raw_records: list[ModuleRecord] = []
    project_head = analyze_project(
        tmp_path,
        configuration,
        skip_dynamic=True,
        write_cache=False,
        raw_records=raw_records,
    )

    with _base_head_models(
        project_head,
        raw_records,
        diff_files,
        diff_range.base_rev,
        tmp_path,
        configuration,
    ) as outcome:
        assert outcome.completeness == "complete"
        names = {module.module_name for module in outcome.base.modules}
        assert names == {"pkg", "pkg.uses", "pkg.change", "pkg.vieux"}

        by_name = {module.module_name: module for module in outcome.base.modules}

        base_change = by_name["pkg.change"]
        assert {f.qualified_name for f in base_change.functions} == {"pkg.change.f"}
        assert (base_change.functions[0].exceptions or []) == []
        assert base_change.file_path.is_file()

        base_uses = by_name["pkg.uses"]
        assert base_uses.file_path.resolve() == (pkg / "uses.py").resolve()

        old_base = by_name["pkg.vieux"]
        assert {f.qualified_name for f in old_base.functions} == {"pkg.vieux.remnant"}

    head_change = next(m for m in project_head.modules if m.module_name == "pkg.change")
    assert {f.qualified_name for f in head_change.functions} == {"pkg.change.f", "pkg.change.boom"}


def test_base_head_models_base_revision_unavailable(tmp_path: Path) -> None:
    """An unavailable base revision makes DIA inconclusive, never silent."""
    _write(tmp_path / "module.py", "def f() -> int:\n    return 1")
    configuration = load_configuration(tmp_path)
    raw_records: list[ModuleRecord] = []
    project_record = analyze_project(
        tmp_path,
        configuration,
        skip_dynamic=True,
        write_cache=False,
        raw_records=raw_records,
    )

    with _base_head_models(
        project_record,
        raw_records,
        [],
        "sha-inexistant",
        tmp_path,
        configuration,
    ) as outcome:
        assert outcome.completeness == "inconclusive"
        assert outcome.reason == "base_revision_unavailable"


def test_base_head_models_raw_records_unavailable(tmp_path: Path) -> None:
    """Without raw records, DIA does not run without a symmetric model."""
    _write(tmp_path / "module.py", "def f() -> int:\n    return 1")
    configuration = load_configuration(tmp_path)
    project_record = analyze_project(tmp_path, configuration, skip_dynamic=True, write_cache=False)

    with _base_head_models(project_record, None, [], "HEAD", tmp_path, configuration) as outcome:
        assert outcome.completeness == "inconclusive"
        assert outcome.reason == "head_records_unavailable"


def test_base_head_models_completeness_partial_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A base file outside the root is ignored with a degraded-completeness warning."""
    _initialize_repo(tmp_path)
    _write(tmp_path / "module.py", "def f() -> int:\n    return 1")
    _write(tmp_path / "stable.py", "def s() -> int:\n    return 3")
    _commit(tmp_path, "base")
    sha_base = _sha_head(tmp_path)

    _write(tmp_path / "module.py", "def f() -> int:\n    return 2")
    _commit(tmp_path, "head")
    sha_head = _sha_head(tmp_path)

    diff_files, diff_range = discover_changed_python_files(tmp_path, git_diff=f"{sha_base}..{sha_head}")
    configuration = load_configuration(tmp_path)
    raw_records: list[ModuleRecord] = []
    project_record = analyze_project(
        tmp_path,
        configuration,
        skip_dynamic=True,
        write_cache=False,
        raw_records=raw_records,
    )

    original = dia._relative_path_or_none

    def _outside_root(source_path: Path, project_root: Path) -> str | None:
        if source_path.name == "module.py":
            return None
        return original(source_path, project_root)

    monkeypatch.setattr(dia, "_relative_path_or_none", _outside_root)

    with _base_head_models(
        project_record,
        raw_records,
        diff_files,
        diff_range.base_rev,
        tmp_path,
        configuration,
    ) as outcome:
        assert outcome.completeness == "partial"
        assert "outside project root" in (outcome.reason or "")
        assert {m.module_name for m in outcome.base.modules} == {"stable"}


# ---------------------------------------------------------------------------
# Phase 2 -- Normalized behavioral key comparison
# ---------------------------------------------------------------------------


def _analyze_sources(tmp_path: Path, identifier_name: str, files: dict[str, str]) -> ProjectRecord:
    """Analyze an isolated source without Git or cache and return the enriched project."""
    root = tmp_path / identifier_name
    root.mkdir(parents=True)
    for relative_path, payload in files.items():
        source_path = root / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        _write(source_path, payload)
    return analyze_project(
        root,
        load_configuration(root),
        skip_dynamic=True,
        write_cache=False,
    )


def _tuple_delta(delta: object) -> tuple[str, str, str, str, Confidence]:
    """Project a DeltaBehavioral into a comparable tuple."""
    return (
        delta.symbol,  # type: ignore[attr-defined]
        delta.field,  # type: ignore[attr-defined]
        delta.value,  # type: ignore[attr-defined]
        delta.direction,  # type: ignore[attr-defined]
        delta.confidence,  # type: ignore[attr-defined]
    )


def test_delta_exception_local_added(tmp_path: Path) -> None:
    """A new local exception with an existing field produces a delta."""
    base = _analyze_sources(
        tmp_path,
        "base",
        {"module.py": 'def f(x: int) -> int:\n    if x:\n        return 1\n    raise ValueError("a")'},
    )
    head = _analyze_sources(
        tmp_path,
        "head",
        {"module.py": 'def f(x: int) -> int:\n    if x:\n        return 1\n    raise KeyError("b")'},
    )

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())

    deltas = _behavioral_delta(base_keys, head_keys)

    assert _tuple_delta(deltas[0]) == ("module.f", "exception", "KeyError:local", "ajouté", Confidence.EXPLICIT)
    assert _tuple_delta(deltas[1]) == ("module.f", "exception", "ValueError:local", "retiré", Confidence.EXPLICIT)


def test_delta_propagation_new_via_callgraph(tmp_path: Path) -> None:
    """A newly propagated exception produces a propagated delta for its caller."""
    base = _analyze_sources(
        tmp_path,
        "base",
        {
            "src/a.py": 'def g() -> None:\n    raise ValueError("x")',
            "src/b.py": "from src.a import g\n\n\ndef h() -> None:\n    g()",
        },
    )
    head = _analyze_sources(
        tmp_path,
        "head",
        {
            "src/a.py": 'def g() -> None:\n    raise ValueError("x")\n\n\ndef g2() -> None:\n    raise KeyError("k")',
            "src/b.py": "from src.a import g, g2\n\n\ndef h() -> None:\n    g()\n    g2()",
        },
    )

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())

    deltas = _behavioral_delta(base_keys, head_keys)

    assert _tuple_delta(deltas[0]) == (
        "src.b.h",
        "exception",
        "KeyError:propagated",
        "ajouté",
        Confidence.INFERRED_HIGH,
    )


def test_delta_absent_confidence_on_one_side_produces_no_delta() -> None:
    """A field with ABSENT confidence on one side is not comparable, so no delta is produced."""
    base_keys = {"module.f": {"exception": frozenset({BehavioralKey("exception", "ValueError:local", Confidence.EXPLICIT)})}}
    head_keys = {"module.f": {"io": frozenset({BehavioralKey("io", "io.writes_files", Confidence.EXPLICIT)})}}

    assert _behavioral_delta(base_keys, head_keys) == []


def test_delta_io_added_and_removed(tmp_path: Path) -> None:
    """Adding and removing an I/O effect produce symmetric deltas."""
    base = _analyze_sources(
        tmp_path,
        "base",
        {
            "module.py": 'def f() -> None:\n    with open("x", "w") as fh:\n        fh.write("a")\n\n\n'
            'def g() -> None:\n    with open("x", "w") as fh:\n        fh.write("a")\n'
            '    with open("y", "r") as fr:\n        fr.read()',
        },
    )
    head = _analyze_sources(
        tmp_path,
        "head",
        {
            "module.py": 'def f() -> None:\n    with open("x", "w") as fh:\n        fh.write("a")\n'
            '    with open("y", "r") as fr:\n        fr.read()\n\n\n'
            'def g() -> None:\n    with open("x", "w") as fh:\n        fh.write("a")',
        },
    )

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())

    deltas = _behavioral_delta(base_keys, head_keys)

    assert _tuple_delta(deltas[0]) == (
        "module.f",
        "io",
        "io.reads_files",
        "ajouté",
        Confidence.EXPLICIT,
    )
    assert _tuple_delta(deltas[1]) == (
        "module.g",
        "io",
        "io.reads_files",
        "retiré",
        Confidence.EXPLICIT,
    )
    assert len(deltas) == 2


def test_delta_override_divergent(tmp_path: Path) -> None:
    """A method override whose parent changes produces a method_origin delta."""
    base = _analyze_sources(
        tmp_path,
        "base",
        {
            "module.py": "class Base:\n"
            "    def m(self) -> int:\n"
            "        return 1\n\n\n"
            "class Derived(Base):\n"
            "    def m(self) -> int:\n"
            "        return 2",
        },
    )
    head = _analyze_sources(
        tmp_path,
        "head",
        {
            "module.py": "class Other:\n    pass\n\n\nclass Derived(Other):\n    def m(self) -> int:\n        return 2",
        },
    )

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())

    deltas = [d for d in _behavioral_delta(base_keys, head_keys) if d.field == "method_origin"]

    assert _tuple_delta(deltas[0]) == (
        "module.Derived.m",
        "method_origin",
        "new",
        "ajouté",
        Confidence.EXPLICIT,
    )
    assert _tuple_delta(deltas[1]) == (
        "module.Derived.m",
        "method_origin",
        "overridden",
        "retiré",
        Confidence.EXPLICIT,
    )


def test_delta_class_inheritance_change(tmp_path: Path) -> None:
    """A change in class parents produces an inheritance delta."""
    base = _analyze_sources(
        tmp_path,
        "base",
        {"module.py": "class A:\n    pass\n\n\nclass B(A):\n    pass"},
    )
    head = _analyze_sources(
        tmp_path,
        "head",
        {"module.py": "class A:\n    pass\n\n\nclass C:\n    pass\n\n\nclass B(A, C):\n    pass"},
    )

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())

    deltas = _behavioral_delta(base_keys, head_keys)

    assert {d.field for d in deltas} == {"heritage"}
    assert {d.value for d in deltas} == {
        "parents=module.A,module.C;abstract=False",
        "parents=module.A;abstract=False",
    }


def test_delta_symbol_missing_on_one_side_produces_no_delta(tmp_path: Path) -> None:
    """A symbol missing from one revision is not compared (covers the 1xx rules)."""
    base = _analyze_sources(
        tmp_path,
        "base",
        {"module.py": "def f() -> int:\n    return 1"},
    )
    head = _analyze_sources(
        tmp_path,
        "head",
        {"module.py": 'def f() -> int:\n    return 1\n\n\ndef g() -> None:\n    raise ValueError("x")'},
    )

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())

    assert "module.g" not in base_keys
    assert "module.g" in head_keys
    assert _behavioral_delta(base_keys, head_keys) == []


def test_delta_filters_private_visibility(tmp_path: Path) -> None:
    """A private helper is not compared with include_visibility={PUBLIC}."""
    base = _analyze_sources(
        tmp_path,
        "base",
        {"module.py": 'def _helper() -> None:\n    raise ValueError("x")'},
    )
    head = _analyze_sources(
        tmp_path,
        "head",
        {"module.py": 'def _helper() -> None:\n    raise KeyError("k")'},
    )

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())

    assert "_module._helper" not in base_keys
    assert "_module._helper" not in head_keys
    assert _behavioral_delta(base_keys, head_keys) == []


def test_api_diff_parameters_return_and_async_state(tmp_path: Path) -> None:
    """The declarative diff lists parameters, return type, and async state, never impacts."""
    base = _analyze_sources(
        tmp_path,
        "base",
        {"module.py": "def f(a: int, b: int = 1) -> int:\n    return a"},
    )
    head = _analyze_sources(
        tmp_path,
        "head",
        {"module.py": "async def f(a: int, c: int = 2) -> str:\n    return a"},
    )

    base_sig = _extract_declarative_signatures(base.modules, CheckConfig())
    head_sig = _extract_declarative_signatures(head.modules, CheckConfig())

    changes = _api_diff(base_sig, head_sig)
    aspects = {(change.aspect, change.parameter_symbol) for change in changes}

    assert aspects == {
        ("parametre_retire", "b"),
        ("parametre_ajoute", "c"),
        ("retour_modifie", None),
        ("async", None),
    }
    assert all(change.symbol == "module.f" for change in changes)


# ---------------------------------------------------------------------------
# Phase 3 -- Scope extended to affected callers (blast radius)
# ---------------------------------------------------------------------------


def test_affected_callers_blast_radius_propagation(tmp_path: Path) -> None:
    """Unchanged callers at depth 2 carry propagated deltas."""
    base = _analyze_sources(
        tmp_path,
        "base",
        {
            "src/a.py": 'def g() -> None:\n    raise ValueError("x")',
            "src/b.py": "from src.a import g\n\n\ndef h() -> None:\n    g()",
            "src/c.py": "from src.b import h\n\n\ndef k() -> None:\n    h()",
        },
    )
    head = _analyze_sources(
        tmp_path,
        "head",
        {
            "src/a.py": 'def g() -> None:\n    raise ValueError("x")\n    raise KeyError("k")',
            "src/b.py": "from src.a import g\n\n\ndef h() -> None:\n    g()",
            "src/c.py": "from src.b import h\n\n\ndef k() -> None:\n    h()",
        },
    )

    callgraph_base = build_callgraph(base)
    callgraph_head = build_callgraph(head)

    callers = _affected_callers(callgraph_base, callgraph_head, {"src.a.g"})
    assert callers == {"src.b.h", "src.c.k"}
    assert _affected_callers(callgraph_base, callgraph_head, {"src.a.g"}, depth=1) == {"src.b.h"}

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())
    deltas = _mark_affected_callers(_behavioral_delta(base_keys, head_keys), callers)

    by_symbol = {d.symbol: d for d in deltas if d.field == "exception"}
    assert by_symbol["src.b.h"].provenance == "propagated"
    assert by_symbol["src.b.h"].impact_completeness == "complete"
    assert by_symbol["src.c.k"].provenance == "propagated"
    assert by_symbol["src.a.g"].provenance == "local"


def test_affected_callers_private_helper_propagates(tmp_path: Path) -> None:
    """A private helper causes propagation; its public caller receives the impact."""
    base = _analyze_sources(
        tmp_path,
        "base",
        {"module.py": 'def _h() -> None:\n    raise ValueError("x")\n\n\ndef pub() -> None:\n    _h()'},
    )
    head = _analyze_sources(
        tmp_path,
        "head",
        {
            "module.py": 'def _h() -> None:\n    raise ValueError("x")\n\n\n'
            'def _h2() -> None:\n    raise KeyError("k")\n\n\n'
            "def pub() -> None:\n"
            "    _h()\n"
            "    _h2()"
        },
    )

    callgraph_base = build_callgraph(base)
    callgraph_head = build_callgraph(head)

    callers = _affected_callers(callgraph_base, callgraph_head, {"module._h"})
    assert callers == {"module.pub"}

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())
    deltas = _mark_affected_callers(_behavioral_delta(base_keys, head_keys), callers)

    public_deltas = [d for d in deltas if d.symbol == "module.pub" and d.field == "exception"]
    assert public_deltas
    assert all(d.provenance == "propagated" for d in public_deltas)


def test_affected_callers_without_exception_delta_do_not_mark_callers(tmp_path: Path) -> None:
    """Without an exception change, no caller delta is propagated."""
    base = _analyze_sources(
        tmp_path,
        "base",
        {
            "src/a.py": 'def g() -> None:\n    with open("x", "w") as fh:\n        fh.write("a")',
            "src/b.py": "from src.a import g\n\n\ndef h() -> None:\n    g()",
        },
    )
    head = _analyze_sources(
        tmp_path,
        "head",
        {
            "src/a.py": 'def g() -> None:\n    with open("x", "w") as fh:\n        fh.write("a")\n'
            '    with open("y", "r") as fr:\n        fr.read()',
            "src/b.py": "from src.a import g\n\n\ndef h() -> None:\n    g()",
        },
    )

    callgraph_base = build_callgraph(base)
    callgraph_head = build_callgraph(head)

    callers = _affected_callers(callgraph_base, callgraph_head, {"src.a.g"})
    assert callers == {"src.b.h"}

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())
    deltas = _mark_affected_callers(_behavioral_delta(base_keys, head_keys), callers)

    assert all(d.provenance == "local" for d in deltas)
    assert {d.symbol for d in deltas} == {"src.a.g"}


def test_mark_affected_callers_partial_impact_completeness() -> None:
    """An unresolved caller outside the project degrades impact completeness."""
    deltas = [
        DeltaBehavioral("module.pub", "exception", "KeyError:propagated", "ajouté", Confidence.INFERRED_HIGH),
        DeltaBehavioral("module.other", "exception", "KeyError:propagated", "ajouté", Confidence.INFERRED_HIGH),
    ]

    marked_deltas = _mark_affected_callers(deltas, {"module.pub"}, partial_symbols=frozenset({"module.pub"}))

    assert marked_deltas[0].provenance == "propagated"
    assert marked_deltas[0].impact_completeness == "partial"
    assert marked_deltas[1].provenance == "local"
    assert marked_deltas[1].impact_completeness == "complete"


# ---------------------------------------------------------------------------
# Phase 4 -- Severity, criticality, and emission (state model)
# ---------------------------------------------------------------------------


def _src(*lines: str) -> str:
    """Assemble source lines without a trailing newline."""
    return "\n".join(lines)


def _index_dia(project_record: ProjectRecord) -> dict[str, object]:
    """Index symbols to function and class records for DIA emission."""
    index: dict[str, object] = {}
    for module in project_record.modules:
        for fn in iter_functions([module]):
            index[fn.qualified_name] = fn
        for class_ in module.classes:
            index[class_.qualified_name] = class_
    return index


def _emit(
    tmp_path: Path,
    base: dict[str, str],
    head: dict[str, str],
    config: CheckConfig | None = None,
) -> list[tuple[DeltaBehavioral, EmissionDelta]]:
    """Analyze base and head, then emit each behavioral delta."""
    project_base = _analyze_sources(tmp_path, "base", base)
    head_project = _analyze_sources(tmp_path, "head", head)
    base_keys = _extract_behavioral_keys(project_base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head_project.modules, CheckConfig())
    deltas = _behavioral_delta(base_keys, head_keys)
    emission = DiaEmissionContext(
        configuration=config or CheckConfig(),
        records_head=_index_dia(head_project),
        records_base=_index_dia(project_base),
        project_root=tmp_path,
    )
    return [(delta, _emit_dia_delta(emission, delta)) for delta in deltas]


_SOURCE_F_VALUE = _src(
    "def f(x: int) -> int:",
    '    """Computes a value.',
    "",
    "    Returns",
    "    -------",
    "    int",
    "        Result.",
    '    """',
    '    if x < 0:\n        raise ValueError("negative")',
    "    return x",
)


def _source_f_value_key(addition: str = "") -> str:
    """Source for f raising ValueError and KeyError, with an optional body insertion."""
    return _src(
        "def f(x: int) -> int:",
        '    """Computes a value.',
        "",
        "    Returns",
        "    -------",
        "    int",
        "        Result.",
        '    """',
        '    if x < 0:\n        raise ValueError("negative")',
        '    raise KeyError("other")',
        addition,
        "    return x",
    )


def test_emission_contradiction_exception_exposed(tmp_path: Path) -> None:
    """A local exposed exception absent from Raises with an unchanged docstring is an error."""
    emissions = _emit(tmp_path, {"module.py": _SOURCE_F_VALUE}, {"module.py": _source_f_value_key()})

    assert len(emissions) == 1
    delta, outcome = emissions[0]
    assert delta.field == "exception"
    assert delta.value == "KeyError:local"
    assert outcome.entry is not None
    assert outcome.entry.severity == "error"
    assert outcome.entry.code == DMT_4201
    assert outcome.meta is not None
    assert outcome.meta.provenance == "local"
    assert outcome.meta.fixable is True
    assert outcome.impact is not None
    assert outcome.impact.status == "contradiction"
    assert outcome.entry.file == "head/module.py"
    assert outcome.entry.diagnostic_key is not None
    assert outcome.entry.diagnostic_key.file == "head/module.py"
    assert outcome.impact.file == "head/module.py"
    assert _impact_to_dict(outcome.impact)["file"] == "head/module.py"


def test_emission_exception_documented_compatible(tmp_path: Path) -> None:
    """An exception documented in Raises produces no diagnostic (treated)."""
    head = _src(
        "def f(x: int) -> int:",
        '    """Computes a value.',
        "",
        "    Parameters",
        "    ----------",
        "    x : int",
        "        Input.",
        "",
        "    Raises",
        "    ------",
        "    KeyError",
        "        If negative.",
        "",
        "    Returns",
        "    -------",
        "    int",
        "        Result.",
        '    """',
        '    if x < 0:\n        raise ValueError("negative")',
        '    raise KeyError("other")',
        "    return x",
    )

    emissions = _emit(tmp_path, {"module.py": _SOURCE_F_VALUE}, {"module.py": head})

    assert len(emissions) == 1
    _, outcome = emissions[0]
    assert outcome.entry is None
    assert outcome.impact is not None
    assert outcome.impact.status == "treated"


def test_emission_changed_unverified_docstring_changed(tmp_path: Path) -> None:
    """A changed docstring without Raises produces no CheckEntry; impact is changed_unverified."""
    head = _src(
        "def f(x: int) -> int:",
        '    """Computes an updated value.',
        "",
        "    Returns",
        "    -------",
        "    int",
        "        Result.",
        '    """',
        '    if x < 0:\n        raise ValueError("negative")',
        '    raise KeyError("other")',
        "    return x",
    )

    emissions = _emit(tmp_path, {"module.py": _SOURCE_F_VALUE}, {"module.py": head})

    assert len(emissions) == 1
    _, outcome = emissions[0]
    assert outcome.entry is None
    assert outcome.impact is not None
    assert outcome.impact.status == "changed_unverified"


def test_emission_locally_caught_exception_has_no_impact(tmp_path: Path) -> None:
    """A raise absorbed by a local handler is not a public contract change."""
    head = _src(
        "def f(x: int) -> int:",
        '    """Computes a value.',
        "",
        "    Returns",
        "    -------",
        "    int",
        "        Result.",
        '    """',
        '    if x < 0:\n        raise ValueError("negative")',
        "    try:",
        '        raise KeyError("absorbed")',
        "    except KeyError:",
        "        pass",
        "    return x",
    )

    emissions = _emit(tmp_path, {"module.py": _SOURCE_F_VALUE}, {"module.py": head})

    assert emissions == []


def test_emission_propagated_remains_warning_despite_override(tmp_path: Path) -> None:
    """A user severity override cannot make propagation blocking."""
    base = {
        "src/a.py": 'def g() -> None:\n    raise ValueError("x")',
        "src/b.py": "from src.a import g\n\n\ndef h() -> None:\n    g()",
    }
    head = {
        "src/a.py": 'def g() -> None:\n    raise ValueError("x")\n    raise KeyError("k")',
        "src/b.py": "from src.a import g\n\n\ndef h() -> None:\n    g()",
    }
    project_base = _analyze_sources(tmp_path / "pb", "base", base)
    head_project = _analyze_sources(tmp_path / "ph", "head", head)
    base_keys = _extract_behavioral_keys(project_base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head_project.modules, CheckConfig())
    deltas = _behavioral_delta(base_keys, head_keys)
    callgraph_base = build_callgraph(project_base)
    callgraph_head = build_callgraph(head_project)
    callers = _affected_callers(callgraph_base, callgraph_head, {"src.a.g"})
    deltas = _mark_affected_callers(deltas, callers)
    emission = DiaEmissionContext(
        configuration=CheckConfig(severity={DMT_4201: "error"}),
        records_head=_index_dia(head_project),
        records_base=_index_dia(project_base),
    )

    results = [(delta, _emit_dia_delta(emission, delta)) for delta in deltas if delta.symbol == "src.b.h"]

    assert results
    _, outcome = results[0]
    assert outcome.entry is not None
    assert outcome.entry.severity == "warning"
    assert outcome.meta is not None
    assert outcome.meta.provenance == "propagated"


def test_emission_complete_documented_removal_is_contradiction(tmp_path: Path) -> None:
    """A complete explicit local removal contradicts an unchanged Raises section."""
    base = _src(
        "def f(x: int) -> int:",
        '    """Computes a value.',
        "",
        "    Raises",
        "    ------",
        "    KeyError",
        "        If negative.",
        "",
        "    Returns",
        "    -------",
        "    int",
        "        Result.",
        '    """',
        '    if x < 0:\n        raise ValueError("negative")',
        '    raise KeyError("other")',
        "    return x",
    )
    head = _src(
        "def f(x: int) -> int:",
        '    """Computes a value.',
        "",
        "    Raises",
        "    ------",
        "    KeyError",
        "        If negative.",
        "",
        "    Returns",
        "    -------",
        "    int",
        "        Result.",
        '    """',
        '    if x < 0:\n        raise ValueError("negative")',
        "    return x",
    )

    emissions = _emit(tmp_path, {"module.py": base}, {"module.py": head})

    assert len(emissions) == 1
    delta, outcome = emissions[0]
    assert delta.direction == "retiré"
    assert outcome.entry is not None
    assert outcome.entry.severity == "warning"
    assert outcome.entry.code == DMT_4202
    assert outcome.meta is not None
    assert outcome.meta.fixable is True
    assert outcome.impact is not None
    assert outcome.impact.status == "contradiction"


def test_removal_not_confirmed_when_head_still_exposes_exception(tmp_path: Path) -> None:
    """A propagated HEAD exposure prevents confirming a complete removal."""
    base = _analyze_sources(tmp_path, "base_removal", {"module.py": "def f() -> None:\n    raise KeyError('x')"})
    head = _analyze_sources(tmp_path, "head_removal", {"module.py": "def f() -> None:\n    return None"})
    record_head = next(iter(head.modules[0].functions))
    record_base = next(iter(base.modules[0].functions))
    record_head.exceptions = [
        ExceptionRecord(
            exception_type="KeyError",
            message="propagated",
            raise_line=1,
            provenance=Provenance.CALLGRAPH_PROPAGATION,
        )
    ]
    record_head.exceptions_confidence = Confidence.INFERRED_HIGH

    delta = DeltaBehavioral(
        symbol="module.f",
        field="exception",
        value="KeyError:local",
        direction="retiré",
        confidence=Confidence.EXPLICIT,
    )

    assert not _confirmed_exception_removal(
        delta=delta,
        record_head=record_head,
        record_base=record_base,
        analysis_completeness="complete",
    )


def test_removal_not_confirmed_for_partial_analysis(tmp_path: Path) -> None:
    """A partial analysis does not authorize an automatic removal fix."""
    base = _analyze_sources(tmp_path, "partial_base", {"module.py": "def f() -> None:\n    raise KeyError('x')"})
    head = _analyze_sources(tmp_path, "partial_head", {"module.py": "def f() -> None:\n    return None"})
    record_base = next(iter(base.modules[0].functions))
    record_head = next(iter(head.modules[0].functions))
    delta = DeltaBehavioral(
        symbol="module.f",
        field="exception",
        value="KeyError:local",
        direction="retiré",
        confidence=Confidence.EXPLICIT,
    )

    assert not _confirmed_exception_removal(
        delta=delta,
        record_head=record_head,
        record_base=record_base,
        analysis_completeness="partial",
    )


def test_emission_io_observed_then_changed_unverified(tmp_path: Path) -> None:
    """I/O is observed when the docstring is unchanged and unverified when changed."""
    base = {"module.py": 'def f() -> None:\n    with open("x", "w") as fh:\n        fh.write("a")'}
    head_unchanged = {
        "module.py": 'def f() -> None:\n    with open("x", "w") as fh:\n        fh.write("a")\n'
        '    with open("y", "r") as fr:\n        fr.read()'
    }
    head_changed = {
        "module.py": _src(
            "def f() -> None:",
            '    """Writes a file.',
            '    """',
            '    with open("x", "w") as fh:\n        fh.write("a")',
            '    with open("y", "r") as fr:\n        fr.read()',
        )
    }

    unchanged_emissions = _emit(tmp_path / "a", base, head_unchanged)
    assert len(unchanged_emissions) == 1
    _, result_unchanged = unchanged_emissions[0]
    assert result_unchanged.entry is not None
    assert result_unchanged.entry.severity == "warning"
    assert result_unchanged.entry.code == DMT_7302
    assert result_unchanged.impact is not None
    assert result_unchanged.impact.status == "documentation_unchanged"

    emissions_changed = _emit(tmp_path / "b", base, head_changed)
    _, result_changed = emissions_changed[0]
    assert result_changed.entry is None
    assert result_changed.impact is not None
    assert result_changed.impact.status == "changed_unverified"


def test_emission_disabled_config(tmp_path: Path) -> None:
    """A configured disabled severity removes the entry, not the impact."""
    base = {"module.py": 'def f() -> None:\n    with open("x", "w") as fh:\n        fh.write("a")'}
    head = {
        "module.py": 'def f() -> None:\n    with open("x", "w") as fh:\n        fh.write("a")\n'
        '    with open("y", "r") as fr:\n        fr.read()'
    }

    emissions = _emit(
        tmp_path,
        base,
        head,
        config=CheckConfig(severity={DMT_7302: "disabled"}),
    )

    assert len(emissions) == 1
    _, outcome = emissions[0]
    assert outcome.entry is None
    assert outcome.impact is not None
    assert outcome.impact.status == "documentation_unchanged"


def test_emission_class_inheritance_change_warning(tmp_path: Path) -> None:
    """A change in class parents produces an inheritance impact warning."""
    base = {
        "module.py": _src(
            "class A:",
            '    """Class A."""',
            "    pass",
            "",
            "",
            "class B(A):",
            '    """Class B."""',
            "    pass",
        )
    }
    head = {
        "module.py": _src(
            "class A:",
            '    """Class A."""',
            "    pass",
            "",
            "",
            "class C:",
            '    """Class C."""',
            "    pass",
            "",
            "",
            "class B(A, C):",
            '    """Class B."""',
            "    pass",
        )
    }

    emissions = _emit(tmp_path, base, head)

    inheritance = [(delta, outcome) for delta, outcome in emissions if delta.field == "heritage"]
    assert inheritance
    _, outcome = inheritance[0]
    assert outcome.entry is not None
    assert outcome.entry.severity == "warning"
    assert outcome.entry.code == DMT_5202
    assert outcome.meta is not None
    assert outcome.meta.fixable is True


# ---------------------------------------------------------------------------
# Phase 5 -- Orchestration and typed report (impact_analysis)
# ---------------------------------------------------------------------------


def test_run_check_dia_enabled_impact_analysis(tmp_path: Path) -> None:
    """run_check with dia=True populates impact_analysis and emits DIA entries."""
    _initialize_repo(tmp_path)
    _write(
        tmp_path / "module.py",
        _src(
            "def f(x: int) -> int:",
            '    """Computes a value.',
            "",
            "    Returns",
            "    -------",
            "    int",
            "        Result.",
            '    """',
            '    if x < 0:\n        raise ValueError("negative")',
            "    return x",
        ),
    )
    _commit(tmp_path, "base")
    sha_base = _sha_head(tmp_path)
    _write(
        tmp_path / "module.py",
        _src(
            "def f(x: int) -> int:",
            '    """Computes a value.',
            "",
            "    Returns",
            "    -------",
            "    int",
            "        Result.",
            '    """',
            '    if x < 0:\n        raise ValueError("negative")',
            '    raise KeyError("other")',
            "    return x",
        ),
    )
    _commit(tmp_path, "head")
    sha_head = _sha_head(tmp_path)

    outcome = runner.run_check(
        tmp_path,
        git_diff=f"{sha_base}..{sha_head}",
        write_cache=False,
        config=CheckConfig(dia=True),
    )

    assert outcome.impact_analysis is not None
    assert outcome.impact_analysis.completeness == "complete"
    assert any(impact["status"] == "contradiction" for impact in outcome.impact_analysis.impacts)
    assert any(check.code == DMT_4201 for check in outcome.checks)
    entry_dia = next(check for check in outcome.checks if check.dia is not None)
    assert entry_dia.dia is not None
    assert entry_dia.dia["status"] == "contradiction"
    assert entry_dia.file == "module.py"
    assert entry_dia.diagnostic_key is not None
    assert entry_dia.diagnostic_key.file == "module.py"
    contradiction = next(impact for impact in outcome.impact_analysis.impacts if impact["status"] == "contradiction")
    assert contradiction["file"] == "module.py"


def test_run_check_dia_base_unavailable_is_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a usable diff, DIA publishes an inconclusive impact_analysis."""
    monkeypatch.setattr(
        runner,
        "discover_changed_python_files",
        lambda *_args, **_kwargs: ([], DiffRange(None, "test", "none")),
    )

    outcome = runner.run_check(tmp_path, config=CheckConfig(dia=True))

    assert outcome.impact_analysis is not None
    assert outcome.impact_analysis.completeness == "inconclusive"
    assert outcome.impact_analysis.reason == "missing_diff_base"


def test_run_check_dia_disabled_has_no_impact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dia=False produces no impact_analysis and no regression."""
    monkeypatch.setattr(
        runner,
        "discover_changed_python_files",
        lambda *_args, **_kwargs: ([], DiffRange("", "test", "complete")),
    )
    monkeypatch.setattr(
        runner,
        "analyze_project",
        lambda *_args, **_kwargs: ProjectRecord(project_root=tmp_path, project_name="test"),
    )

    outcome = runner.run_check(tmp_path, config=CheckConfig(dia=False))

    assert outcome.impact_analysis is None


def test_run_check_dia_enabled_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DIA is enabled by default, so impact_analysis is always set, including inconclusive results."""
    monkeypatch.setattr(
        runner,
        "discover_changed_python_files",
        lambda *_args, **_kwargs: ([], DiffRange("", "test", "complete")),
    )
    monkeypatch.setattr(
        runner,
        "analyze_project",
        lambda *_args, **_kwargs: ProjectRecord(project_root=tmp_path, project_name="test"),
    )

    outcome = runner.run_check(tmp_path, config=CheckConfig())

    assert CheckConfig().dia is True
    assert outcome.impact_analysis is not None
    assert outcome.impact_analysis.completeness == "inconclusive"


def test_json_serializes_impact_analysis_and_dia(tmp_path: Path) -> None:
    """JSON v1 carries impact_analysis and the DIA block without provisional DMT numbers."""
    outcome = CheckResult(
        impact_analysis=ImpactAnalysis(
            completeness="complete",
            impacts=[
                {
                    "symbol": "module.f",
                    "code": DMT_4201,
                    "impact_kind": "exposed_exception",
                    "value": "KeyError:local",
                    "direction": "added",
                    "status": "contradiction",
                    "confidence": "explicit",
                    "provenance": "local",
                    "impact_completeness": "complete",
                    "fixable": True,
                }
            ],
        ),
        checks=[
            CheckEntry(
                severity="error",
                code=DMT_4201,
                symbol="module.f",
                file="module.py",
                line_start=1,
                col_start=1,
                message="KeyError is raised but missing from the Raises section.",
                dia={
                    "impact_kind": "exposed_exception",
                    "code": DMT_4201,
                    "value": "KeyError:local",
                    "direction": "added",
                    "confidence": "explicit",
                    "provenance": "local",
                    "impact_completeness": "complete",
                    "fixable": True,
                    "evidence": {"before": None, "after": "KeyError:local"},
                    "status": "contradiction",
                },
            )
        ],
    )

    data = json.loads(format_json(outcome, file=str(tmp_path / "report.json")))

    assert data["impact_analysis"]["completeness"] == "complete"
    assert data["impact_analysis"]["impacts"][0]["impact_kind"] == "exposed_exception"
    impact = data["impact_analysis"]["impacts"][0]
    dia = data["checks"][0]["dia"]
    assert impact["value"] == "KeyError:local"
    assert impact["direction"] == "added"
    assert dia["status"] == "contradiction"
    assert not {"valeur", "sens", "statut", "chemin"} & (set(impact) | set(dia))
    assert data["impact_analysis"]["impacts"][0]["code"] == DMT_4201
    assert data["checks"][0]["code"] == DMT_4201
    assert data["checks"][0]["dia"]["impact_kind"] == "exposed_exception"


def test_github_block_public_contract_diff(tmp_path: Path) -> None:
    """The GitHub formatter emits Public Contract Diff notices at the top."""
    outcome = CheckResult(
        impact_analysis=ImpactAnalysis(
            completeness="complete",
            api_diff=[
                {
                    "symbol": "module.f",
                    "aspect": "return_changed",
                    "before": "int",
                    "after": "str",
                },
                {
                    "symbol": "module.f",
                    "aspect": "parameter_added",
                    "parameter": "exclude_paths",
                },
            ],
        ),
        checks=[
            CheckEntry(
                severity="warning",
                code="io_effect",
                symbol="module.f",
                file="module.py",
                line_start=1,
                col_start=1,
                message="Changement of contract observable.",
                dia={
                    "confidence": "explicit",
                    "provenance": "local",
                    "impact_completeness": "complete",
                    "fixable": False,
                    "evidence": {"before": None, "after": "io.writes_files"},
                    "symbol_context": {
                        "base": {
                            "documentation_origin": "inherited",
                            "dispatch_role": "override",
                            "implementation_kind": "concrete",
                            "inherited_contract_source": "module.Base.run",
                            "method_origin": "overridden",
                        },
                        "head": {
                            "documentation_origin": "inherited",
                            "dispatch_role": "override",
                            "implementation_kind": "concrete",
                            "inherited_contract_source": "module.Base.run",
                            "method_origin": "overridden",
                        },
                    },
                    "status": "documentation_unchanged",
                },
            )
        ],
    )

    output = format_github(outcome, file=str(tmp_path / "annotations.txt"))

    assert "::notice title=DocMeThis DIA::" in output
    assert "Public Contract Diff" in output
    assert "observed" in output
    assert "return_changed (int -> str)" in output
    assert "parameter_added (exclude_paths)" in output
    assert "doc=inherited" in output
    assert "contract=module.Base.run" in output


def test_api_diff_visible_filters_symbols(tmp_path: Path) -> None:
    """api_diff_visible includes symbols in the diff or carrying an impact."""
    project_record = _analyze_sources(
        tmp_path,
        "src",
        {"module.py": "def f(a: int, b: int = 1) -> int:\n    return a", "stable.py": "def s() -> int:\n    return 2"},
    )
    diff_files = [ChangedFile(path=(tmp_path / "src" / "module.py").resolve(), changed_lines=frozenset({1}))]
    changes = [
        DeclarativeChange("module.f", "retour_modifie", "int", "str"),
        DeclarativeChange("module.stable.s", "parametre_ajoute", None, "1"),
    ]

    visible_changes = _api_diff_visible(project_record, diff_files, [], changes)

    assert {change["symbol"] for change in visible_changes} == {"module.f"}

    visible_with_impact = _api_diff_visible(
        project_record,
        diff_files,
        [{"symbol": "module.stable.s"}],
        changes,
    )
    assert {change["symbol"] for change in visible_with_impact} == {"module.f", "module.stable.s"}


def test_propagation_path_from_affected_symbol_to_cause() -> None:
    """The evidence path goes from the affected symbol to its cause.

    It follows only targets carrying the exception type.
    """
    callgraph = {
        "module.b": [CallgraphEdge("module.b", "module.a", "direct", is_external=False, external_lib=None, line=5)],
        "module.c": [CallgraphEdge("module.c", "module.b", "direct", is_external=False, external_lib=None, line=3)],
    }
    index_types = {
        "module.a": {"valueerror"},
        "module.b": {"valueerror"},
        "module.c": set(),
    }

    source_path = _propagation_path(
        callgraph,
        "module.c",
        {"module.a"},
        "ValueError:propagated",
        index_types,
    )

    assert source_path == ["module.c", "module.b", "module.a"]


def test_propagation_path_without_type_carrier_is_empty() -> None:
    """Without a target carrying the type, the path remains empty (no arbitrary choice)."""
    callgraph = {
        "module.b": [CallgraphEdge("module.b", "module.a", "direct", is_external=False, external_lib=None, line=5)],
    }
    index_types = {"module.a": {"typeerror"}}

    source_path = _propagation_path(
        callgraph,
        "module.b",
        {"module.a"},
        "ValueError:propagated",
        index_types,
    )

    assert source_path == ["module.b"]


# ---------------------------------------------------------------------------
# Phase 6 -- Hybrid integration tests and featured-case demo
# ---------------------------------------------------------------------------


def _edges(callgraph: dict[str, list[CallgraphEdge]]) -> set[tuple[str, str]]:
    """Local edges (source, target) of a source-oriented call graph."""
    return {(source, edge.target_qname) for source, edges in callgraph.items() for edge in edges if not edge.is_external}


def test_hybrid_base_handles_cross_imports(tmp_path: Path) -> None:
    """The hybrid pass re-parses paths across the working tree and temporary directory."""
    _initialize_repo(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _write(pkg / "__init__.py", "")
    _write(
        pkg / "a.py",
        _src(
            "from pkg.b import b_to_a",
            "",
            "",
            "def a_source() -> int:",
            "    return 1",
            "",
            "",
            "def a_to_b() -> int:",
            "    return b_to_a()",
        ),
    )
    _write(
        pkg / "b.py",
        _src(
            "from pkg.a import a_source",
            "",
            "",
            "def b_to_a() -> int:",
            "    return a_source()",
        ),
    )
    _commit(tmp_path, "base")
    sha_base = _sha_head(tmp_path)

    _write(
        pkg / "a.py",
        _src(
            "from pkg.b import b_to_a",
            "",
            "",
            "def a_source() -> int:",
            "    return 1",
            "",
            "",
            "def a_to_b() -> int:",
            "    return b_to_a()",
            "",
            "",
            "def a_new() -> None:",
            "    pass",
        ),
    )
    _commit(tmp_path, "head")
    sha_head = _sha_head(tmp_path)

    diff_files, diff_range = discover_changed_python_files(tmp_path, git_diff=f"{sha_base}..{sha_head}")
    configuration = load_configuration(tmp_path)
    raw_records: list[ModuleRecord] = []
    project_head = analyze_project(
        tmp_path,
        configuration,
        skip_dynamic=True,
        write_cache=False,
        raw_records=raw_records,
    )

    with _base_head_models(
        project_head,
        raw_records,
        diff_files,
        diff_range.base_rev,
        tmp_path,
        configuration,
    ) as outcome:
        assert outcome.completeness == "complete"
        base = outcome.base
        assert {m.module_name for m in base.modules} == {"pkg", "pkg.a", "pkg.b"}
        assert all(m.file_path.is_file() for m in base.modules)

        callgraph_base = build_callgraph(base)
        callgraph_head = build_callgraph(project_head)

        base_edges = _edges(callgraph_base)
        assert ("pkg.a.a_to_b", "pkg.b.b_to_a") in base_edges  # changed -> unchanged
        assert ("pkg.b.b_to_a", "pkg.a.a_source") in base_edges  # unchanged -> changed
        assert base_edges == _edges(callgraph_head)


def test_demo_three_featured_cases(tmp_path: Path) -> None:
    """The demo produces the first fact, private propagating helper, blast radius, I/O, and contradiction."""
    _initialize_repo(tmp_path)
    pricing = tmp_path / "pricing"
    pricing.mkdir()
    _write(pricing / "__init__.py", "")

    base_grid = _src(
        "def _read_grid(code: str) -> float:",
        '    """Reads the base price for a code.',
        "",
        "    Parameters",
        "    ----------",
        "    code : str",
        "        Code produces.",
        "",
        "    Returns",
        "    -------",
        "    float",
        "        Base price.",
        '    """',
        "    return 10.0",
        "",
        "",
        "def unit_price(code: str) -> float:",
        '    """Returns the unit price for a code.',
        "",
        "    Parameters",
        "    ----------",
        "    code : str",
        "        Code produces.",
        "",
        "    Returns",
        "    -------",
        "    float",
        "        Unit price.",
        '    """',
        "    return 12.0",
    )
    head_grid = _src(
        "def _read_grid(code: str) -> float:",
        '    """Reads the base price for a code.',
        "",
        "    Parameters",
        "    ----------",
        "    code : str",
        "        Code produces.",
        "",
        "    Returns",
        "    -------",
        "    float",
        "        Base price.",
        '    """',
        '    if code.startswith("X"):',
        '        raise KeyError("grid missing")',
        "    return 10.0",
        "",
        "",
        "def unit_price(code: str) -> float:",
        '    """Returns the unit price for a code.',
        "",
        "    Parameters",
        "    ----------",
        "    code : str",
        "        Code produces.",
        "",
        "    Returns",
        "    -------",
        "    float",
        "        Unit price.",
        '    """',
        "    if not code:",
        '        raise ValueError("empty code")',
        "    return 12.0",
    )
    _write(pricing / "grille.py", base_grid)
    _write(
        pricing / "load_price.py",
        _src(
            "from pricing.grille import _read_grid",
            "",
            "",
            "def load_price(code: str) -> float:",
            '    """Loads the displayed price for a code.',
            "",
            "    Parameters",
            "    ----------",
            "    code : str",
            "        Code produces.",
            "",
            "    Returns",
            "    -------",
            "    float",
            "        Prix displayed.",
            '    """',
            "    return _read_grid(code)",
        ),
    )
    _write(
        pricing / "apply_discount.py",
        _src(
            "from pricing.load_price import load_price",
            "",
            "",
            "def apply_discount(code: str) -> float:",
            '    """Applies a discount to the loaded price."""',
            "    return load_price(code) * 0.9",
        ),
    )
    _write(
        pricing / "promotions.py",
        _src(
            "def apply_promotion(code: str) -> str:",
            '    """Applies the current promotion to a code."""',
            "    return code.upper()",
        ),
    )
    _commit(tmp_path, "base")
    sha_base = _sha_head(tmp_path)

    _write(pricing / "grille.py", head_grid)
    _write(
        pricing / "promotions.py",
        _src(
            "def apply_promotion(code: str) -> str:",
            '    """Applies the current promotion to a code."""',
            '    with open("promo.log", "w", encoding="utf-8") as fh:',
            "        fh.write(code)",
            "    return code.upper()",
        ),
    )
    _commit(tmp_path, "head")
    sha_head = _sha_head(tmp_path)

    outcome = runner.run_check(
        tmp_path,
        git_diff=f"{sha_base}..{sha_head}",
        write_cache=False,
        config=CheckConfig(dia=True),
    )

    analysis = outcome.impact_analysis
    assert analysis is not None
    assert analysis.completeness == "complete"

    symbol_lookup = {(impact["symbol"], impact["value"]): impact for impact in analysis.impacts}

    propagated = symbol_lookup[("pricing.load_price.load_price", "KeyError:propagated")]
    assert propagated["status"] == "documentation_unchanged"
    assert propagated["path"] == ["pricing.load_price.load_price", "pricing.grille._read_grid"]

    blast = symbol_lookup[("pricing.apply_discount.apply_discount", "KeyError:propagated")]
    assert blast["path"] == [
        "pricing.apply_discount.apply_discount",
        "pricing.load_price.load_price",
        "pricing.grille._read_grid",
    ]

    contradiction = symbol_lookup[("pricing.grille.unit_price", "ValueError:local")]
    assert contradiction["status"] == "contradiction"

    first_io = symbol_lookup[("pricing.promotions.apply_promotion", "io.writes_files")]
    assert first_io["impact_kind"] == "io_effect"
    assert first_io["status"] == "documentation_unchanged"

    dia_codes = [check.code for check in outcome.checks if check.dia is not None]
    assert dia_codes.count(DMT_4201) >= 3

    dia_errors = [check for check in outcome.checks if check.dia is not None and check.severity == "error"]
    assert [check.symbol for check in dia_errors] == ["pricing.grille.unit_price"]

    dia_symbols = {check.symbol for check in outcome.checks if check.dia is not None}
    assert not any(symbol.endswith("_read_grid") for symbol in dia_symbols)

    entry_propagated = next(
        check for check in outcome.checks if check.dia is not None and check.symbol == "pricing.load_price.load_price"
    )
    assert entry_propagated.dia is not None
    assert entry_propagated.dia["evidence"]["path"] == [
        "pricing.load_price.load_price",
        "pricing.grille._read_grid",
    ]


def test_observable_property_delta_emission(tmp_path: Path) -> None:
    """An observable precondition delta produces an observed warning impact."""
    base = {
        "module.py": _src(
            "def f(x: int) -> int:",
            '    """Computes a value.',
            "",
            "    Returns",
            "    -------",
            "    int",
            "        Result.",
            '    """',
            '    if x < 0:\n        raise ValueError("x")',
            "    return x",
        )
    }
    head = {
        "module.py": _src(
            "def f(x: int) -> int:",
            '    """Computes a value.',
            "",
            "    Returns",
            "    -------",
            "    int",
            "        Result.",
            '    """',
            '    if x < 0:\n        raise ValueError("x")',
            '    if x > 10:\n        raise KeyError("y")',
            "    return x",
        )
    }

    emissions = _emit(tmp_path, base, head)

    observables = [(delta, outcome) for delta, outcome in emissions if delta.field == "observable"]
    assert observables
    delta, outcome = observables[0]
    assert delta.direction == "ajouté"
    assert outcome.entry is not None
    assert outcome.entry.code == DMT_7303
    assert outcome.entry.severity == "warning"
    assert outcome.impact is not None
    assert outcome.impact.status == "documentation_unchanged"


# ---------------------------------------------------------------------------
# Corrections of return reviewer (C1..C5)
# ---------------------------------------------------------------------------


def test_delta_first_and_last_facts(tmp_path: Path) -> None:
    """The first and last exception/I/O facts produce deltas (C1)."""
    base_io = {"module.py": 'def f() -> None:\n    with open("x", "w") as fh:\n        fh.write("a")'}
    without_io = {"module.py": "def f() -> None:\n    return None"}

    added_emissions = _emit(tmp_path / "a", without_io, base_io)
    assert [delta.value for delta, _ in added_emissions] == ["io.writes_files"]

    removed_emissions = _emit(tmp_path / "b", base_io, without_io)
    assert [(delta.value, delta.direction) for delta, _ in removed_emissions] == [("io.writes_files", "retiré")]

    without_exc = {"module.py": "def f() -> None:\n    return None"}
    with_exc = {"module.py": 'def f() -> None:\n    raise KeyError("x")'}
    exception_emissions = _emit(tmp_path / "c", without_exc, with_exc)
    assert [delta.value for delta, _ in exception_emissions] == ["KeyError:local"]


def test_mark_affected_callers_does_not_mark_local_delta_as_propagated() -> None:
    """A local delta for an affected caller is never marked propagated (C2)."""
    deltas = [
        DeltaBehavioral("module.h", "exception", "KeyError:local", "ajouté", Confidence.EXPLICIT),
        DeltaBehavioral("module.h", "exception", "KeyError:propagated", "ajouté", Confidence.INFERRED_HIGH),
    ]

    marked_deltas = _mark_affected_callers(deltas, {"module.h"})

    assert marked_deltas[0].provenance == "local"
    assert marked_deltas[1].provenance == "propagated"


def test_emission_unmarked_propagation_remains_warning(tmp_path: Path) -> None:
    """An unmarked propagation (Phase 3 absent) remains a warning because of its value (C2)."""
    base = {
        "src/a.py": 'def g() -> None:\n    raise ValueError("x")',
        "src/b.py": "from src.a import g\n\n\ndef h() -> None:\n    g()",
    }
    head = {
        "src/a.py": 'def g() -> None:\n    raise ValueError("x")\n    raise KeyError("k")',
        "src/b.py": "from src.a import g\n\n\ndef h() -> None:\n    g()",
    }

    emissions = _emit(
        tmp_path,
        base,
        head,
        config=CheckConfig(severity={DMT_4201: "error"}),
    )

    propagated_outcomes = [outcome for delta, outcome in emissions if delta.value == "KeyError:propagated"]
    assert propagated_outcomes
    assert propagated_outcomes[0].entry is not None
    assert propagated_outcomes[0].entry.severity == "warning"


def test_exception_in_other_try_is_not_masked(tmp_path: Path) -> None:
    """A handler in another try block does not mask an external raise (C3)."""
    base = {"module.py": _SOURCE_F_VALUE}
    head = {
        "module.py": _src(
            "def f(x: int) -> int:",
            '    """Computes a value.',
            "",
            "    Returns",
            "    -------",
            "    int",
            "        Result.",
            '    """',
            '    if x < 0:\n        raise ValueError("negative")',
            "    try:",
            "        pass",
            "    except KeyError:",
            "        pass",
            '    raise KeyError("external")',
            "    return x",
        )
    }

    emissions = _emit(tmp_path, base, head)

    keyerror = [(delta, outcome) for delta, outcome in emissions if delta.value == "KeyError:local"]
    assert keyerror
    _, outcome = keyerror[0]
    assert outcome.entry is not None
    assert outcome.entry.severity == "error"
    assert outcome.impact is not None
    assert outcome.impact.status == "contradiction"


def test_exception_handler_catches_value_error(tmp_path: Path) -> None:
    """A handler catching Exception (root) absorbs a ValueError (C3)."""
    base = {"module.py": _SOURCE_F_VALUE}
    head = {
        "module.py": _src(
            "def f(x: int) -> int:",
            '    """Computes a value.',
            "",
            "    Returns",
            "    -------",
            "    int",
            "        Result.",
            '    """',
            "    try:",
            '        raise ValueError("caught")',
            "    except Exception:",
            "        return 0",
            "    return x",
        )
    }

    emissions = _emit(tmp_path, base, head)

    exception_deltas = [delta for delta, _ in emissions if delta.field == "exception"]
    assert [(delta.value, delta.direction) for delta in exception_deltas] == [("ValueError:local", "retiré")]
    assert all(outcome.entry is None for _, outcome in emissions)
    assert all(outcome.impact is not None and outcome.impact.status == "treated" for _, outcome in emissions)


def test_base_read_failure_degrades_completeness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A BASE read failure degrades completeness and never claims complete (C5)."""
    _initialize_repo(tmp_path)
    _write(tmp_path / "module.py", "def f() -> int:\n    return 1")
    _commit(tmp_path, "base")
    sha_base = _sha_head(tmp_path)
    _write(tmp_path / "module.py", "def f() -> int:\n    return 2")
    _commit(tmp_path, "head")
    sha_head = _sha_head(tmp_path)

    diff_files, diff_range = discover_changed_python_files(tmp_path, git_diff=f"{sha_base}..{sha_head}")
    configuration = load_configuration(tmp_path)
    raw_records: list[ModuleRecord] = []
    project_record = analyze_project(
        tmp_path,
        configuration,
        skip_dynamic=True,
        write_cache=False,
        raw_records=raw_records,
    )

    monkeypatch.setattr(dia, "_base_content_or_none", lambda *_args, **_kwargs: None)

    with _base_head_models(
        project_record,
        raw_records,
        diff_files,
        diff_range.base_rev,
        tmp_path,
        configuration,
    ) as outcome:
        assert outcome.completeness == "partial"
        assert "BASE read failed" in (outcome.reason or "")
        assert {m.module_name for m in outcome.base.modules} == set()


# ---------------------------------------------------------------------------
# Phase 7 -- Normalized context and inherited documentation contracts
# ---------------------------------------------------------------------------


def test_context_override_uses_inherited_effective_doc_and_serialization(tmp_path: Path) -> None:
    """An override without local documentation uses its parent's documentation contract."""
    source_base = _src(
        "class Base:",
        "    def run(self, value: int) -> int:",
        '        """Executes the parent contract.',
        "",
        "        Raises",
        "        ------",
        "        KeyError",
        "            If the value is absent.",
        '        """',
        "        return value",
        "",
        "",
        "class Child(Base):",
        "    def run(self, value: int) -> int:",
        "        return value",
    )
    return_position = source_base.rfind("        return value")
    source_head = f'{source_base[:return_position]}        raise KeyError("absent")'
    base = _analyze_sources(tmp_path, "phase7_base", {"module.py": source_base})
    head = _analyze_sources(tmp_path, "phase7_head", {"module.py": source_head})

    base_contexts = _build_symbol_contexts(base.modules)
    head_contexts = _build_symbol_contexts(head.modules)
    analysis_context = head_contexts["module.Child.run"]
    assert analysis_context.documentation_origin == "inherited"
    assert analysis_context.dispatch_role == "override"
    assert analysis_context.implementation_kind == "concrete"
    assert analysis_context.inherited_contract_source == "module.Base.run"
    assert analysis_context.method_origin is MethodOrigin.OVERRIDDEN

    base_keys = _extract_behavioral_keys(base.modules, CheckConfig(), symbol_contexts=base_contexts)
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig(), symbol_contexts=head_contexts)
    delta = next(delta for delta in _behavioral_delta(base_keys, head_keys) if delta.symbol == "module.Child.run")
    assert delta.base_context is not None
    assert delta.head_context is not None
    assert delta.head_context.documentation_origin == "inherited"

    emission = DiaEmissionContext(
        configuration=CheckConfig(),
        records_head=_index_dia(head),
        records_base=_index_dia(base),
    )
    outcome = _emit_dia_delta(emission, delta)
    assert outcome.impact is not None
    assert outcome.impact.status == "treated"
    assert outcome.entry is None
    assert outcome.meta is not None
    assert outcome.meta.head_context == delta.head_context

    report = tmp_path / "phase7.json"
    format_json(
        CheckResult(
            impact_analysis=ImpactAnalysis(
                completeness="complete",
                impacts=[_impact_to_dict(outcome.impact)],
            ),
        ),
        str(report),
    )
    context_json = json.loads(report.read_text(encoding="utf-8"))["impact_analysis"]["impacts"][0]["symbol_context"]
    assert context_json["base"]["inherited_contract_source"] == "module.Base.run"
    assert context_json["head"]["documentation_origin"] == "inherited"


def test_context_overload_has_one_behavioral_implementation(tmp_path: Path) -> None:
    """Overload declarations do not duplicate implementation behavior."""
    source = _src(
        "from typing import overload",
        "",
        "@overload",
        "def parse(value: int) -> int: ...",
        "",
        "@overload",
        "def parse(value: str) -> str: ...",
        "",
        "def parse(value: int | str) -> int | str:",
        "    if not value:",
        '        raise ValueError("empty")',
        "    return value",
    )
    project_record = _analyze_sources(tmp_path, "phase7_overload", {"module.py": source})
    contexts = _build_symbol_contexts(project_record.modules)
    analysis_context = contexts["module.parse"]
    assert analysis_context.dispatch_role == "overload"
    assert analysis_context.implementation_kind == "concrete"
    assert analysis_context.implementation is not None
    assert analysis_context.implementation.has_overloads

    keys = _extract_behavioral_keys(project_record.modules, CheckConfig(), symbol_contexts=contexts)
    assert set(keys) == {"module.parse"}
    assert all("overload" not in symbol for symbol in keys)

    override_source = _src(
        "from typing import overload",
        "",
        "class Parent:",
        "    def parse(self, value: int) -> int:",
        "        return value",
        "",
        "",
        "class Child(Parent):",
        "    @overload",
        "    def parse(self, value: int) -> int: ...",
        "",
        "    @overload",
        "    def parse(self, value: str) -> str: ...",
        "",
        "    def parse(self, value: int | str) -> int | str:",
        "        return value",
    )
    override = _analyze_sources(tmp_path, "phase7_overload_override", {"module.py": override_source})
    context_override = _build_symbol_contexts(override.modules)["module.Child.parse"]
    assert context_override.dispatch_role == "overload"
    assert context_override.method_origin is MethodOrigin.OVERRIDDEN


def test_context_overload_only_abstract_and_transition_stub_without_false_delta(tmp_path: Path) -> None:
    """Non-concrete implementations do not create behavioral deltas."""
    overload_only = _src(
        "from typing import overload",
        "",
        "@overload",
        "def parse(value: int) -> int: ...",
        "",
        "@overload",
        "def parse(value: str) -> str: ...",
    )
    project_overload = _analyze_sources(tmp_path, "phase7_overload_only", {"module.py": overload_only})
    context_overload = _build_symbol_contexts(project_overload.modules)["module.parse"]
    assert context_overload.dispatch_role == "overload"
    assert context_overload.implementation_kind == "stub"
    assert "module.parse" not in _extract_behavioral_keys(project_overload.modules, CheckConfig())

    abstract_source = _src(
        "from abc import ABC, abstractmethod",
        "",
        "class Base(ABC):",
        "    @abstractmethod",
        "    def run(self) -> int:",
        "        pass",
    )
    project_abstract = _analyze_sources(tmp_path, "phase7_abstract", {"module.py": abstract_source})
    abstract_contexts = _build_symbol_contexts(project_abstract.modules)
    context_abstract = abstract_contexts["module.Base.run"]
    assert context_abstract.implementation_kind == "abstract"
    abstract_keys = _extract_behavioral_keys(
        project_abstract.modules,
        CheckConfig(),
        symbol_contexts=abstract_contexts,
    )
    assert set(abstract_keys["module.Base.run"]) == {"method_origin"}

    base = _analyze_sources(
        tmp_path,
        "phase7_transition_base",
        {"module.py": 'def run() -> None:\n    raise ValueError("x")'},
    )
    head = _analyze_sources(tmp_path, "phase7_transition_head", {"module.py": "def run() -> None:\n    pass"})
    base_keys = _extract_behavioral_keys(base.modules, CheckConfig())
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig())
    assert _behavioral_delta(base_keys, head_keys) == []


def test_context_mro_multiple_and_unresolved_parent(tmp_path: Path) -> None:
    """Parent documentation comes from the first MRO parent; otherwise context is unknown."""
    source = _src(
        "class Left:",
        "    def run(self) -> int:",
        '        """Left contract."""',
        "        return 1",
        "",
        "",
        "class Right:",
        "    def run(self) -> int:",
        '        """Right contract."""',
        "        return 2",
        "",
        "",
        "class Child(Left, Right):",
        "    def run(self) -> int:",
        "        return 3",
        "",
        "",
        "class GrandChild(Child):",
        "    pass",
        "",
        "",
        "class ExternalChild(External):",
        "    def run(self) -> int:",
        "        return 4",
    )
    project_record = _analyze_sources(tmp_path, "phase7_mro", {"module.py": source})
    contexts = _build_symbol_contexts(project_record.modules)
    assert contexts["module.Child.run"].inherited_contract_source == "module.Left.run"
    assert contexts["module.GrandChild.run"].documentation_origin == "inherited"
    assert contexts["module.GrandChild.run"].inherited_contract_source == "module.Left.run"
    assert contexts["module.GrandChild.run"].method_origin is MethodOrigin.INHERITED
    assert contexts["module.ExternalChild.run"].documentation_origin == "unknown"

    base = _analyze_sources(
        tmp_path,
        "phase7_external_base",
        {"module.py": "class ExternalChild(External):\n    def run(self) -> int:\n        return 1"},
    )
    head = _analyze_sources(
        tmp_path,
        "phase7_external_head",
        {"module.py": 'class ExternalChild(External):\n    def run(self) -> int:\n        raise KeyError("x")'},
    )
    base_contexts = _build_symbol_contexts(base.modules)
    head_contexts = _build_symbol_contexts(head.modules)
    base_keys = _extract_behavioral_keys(base.modules, CheckConfig(), symbol_contexts=base_contexts)
    head_keys = _extract_behavioral_keys(head.modules, CheckConfig(), symbol_contexts=head_contexts)
    delta = next(iter(_behavioral_delta(base_keys, head_keys)))
    outcome = _emit_dia_delta(
        DiaEmissionContext(
            configuration=CheckConfig(),
            records_head=_index_dia(head),
            records_base=_index_dia(base),
        ),
        delta,
    )
    assert outcome.impact is not None
    assert outcome.impact.status == "changed_unverified"
    assert outcome.entry is None
