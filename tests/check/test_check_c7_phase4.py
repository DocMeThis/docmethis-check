# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""C7 phase 4 tests for class docstring summaries and Raises sections."""

from __future__ import annotations

from inspect import cleandoc
from typing import TYPE_CHECKING

from docmethis_extract_python.static_extraction.models import (
    ClassRecord,
    ExceptBlock,
    ExceptionRecord,
    FunctionRecord,
    MethodType,
    ModuleRecord,
    Parameter,
    ParameterType,
    Provenance,
    Signature,
    Visibility,
)

from docmethis_check.config import CheckConfig
from docmethis_check.diagnostics import diagnostics_for_class, diagnostics_for_function, produce_diagnostics
from docmethis_check.models import MethodExceptionContract, SymbolKind

if TYPE_CHECKING:
    from pathlib import Path


def _parameter(identifier_name: str, source_line: int) -> Parameter:
    """Create a minimal parameter for a test signature."""
    return Parameter(
        name=identifier_name,
        annotation_raw="<absent>",
        default_value="<absent>",
        kind=ParameterType.POSITIONAL_OR_KEYWORD,
        line_start=source_line,
    )


def _exception(identifier_name: str, source_line: int, *, provenance: Provenance = Provenance.LOCAL_INFERENCE) -> ExceptionRecord:
    """Create a minimal exception detected by M1."""
    return ExceptionRecord(exception_type=identifier_name, message=None, raise_line=source_line, provenance=provenance)


def _init_method(
    source_file: Path,
    *,
    exceptions: list[ExceptionRecord] | None = None,
    except_blocks: list[ExceptBlock] | None = None,
) -> FunctionRecord:
    """Create a public __init__ method with two documentable parameters."""
    return FunctionRecord(
        qualified_name="module.Service.__init__",
        file_path=source_file,
        line_start=10,
        line_end=12,
        col_start=4,
        col_end=0,
        method_kind=MethodType.INSTANCE_METHOD,
        visibility=Visibility.PUBLIC,
        parent_class="module.Service",
        parent_module="module",
        existing_docstring=None,
        signature=Signature(
            parameters=[_parameter("self", 10), _parameter("name", 10), _parameter("count", 11)],
            return_annotation="<absent>",
            decorators=[],
        ),
        exceptions=exceptions,
        except_blocks=except_blocks,
    )


def _class(source_file: Path, docstring: str, *, methods: list[FunctionRecord] | None = None) -> ClassRecord:
    """Create a complete public class except for the finding under test."""
    return ClassRecord(
        qualified_name="module.Service",
        file_path=source_file,
        line_start=1,
        line_end=20,
        col_start=0,
        col_end=0,
        visibility=Visibility.PUBLIC,
        parent_module="module",
        existing_docstring=cleandoc(docstring),
        docstring_line_start=2,
        methods=methods if methods is not None else [_init_method(source_file)],
        class_attributes=["name", "count"],
        instance_attributes=["total"],
    )


def test_class_reports_missing_summary_when_dmt500_enabled(tmp_path: Path) -> None:
    """DMT-7001 reports a class docstring without a summary when enabled."""
    class_ = _class(
        tmp_path / "module.py",
        """Parameters
        ----------
        name : str
            Name displayed.
        count : int
            Counter.

        Attributes
        ----------
        name : str
            Name displayed.
        count : int
            Counter.
        total : int
            Total computed.
        """,
    )

    entries, keys = diagnostics_for_class(class_, CheckConfig(severity={"DMT-7001": "warning"}), file="module.py")

    assert [(entry.code, entry.severity, entry.symbol_kind, entry.symbol) for entry in entries] == [
        ("DMT-7001", "warning", SymbolKind.CLASS, "module.Service")
    ]
    assert entries[0].diagnostic_key.section == "Summary"
    assert keys[entries[0].diagnostic_key] == 1


def test_class_reports_undocumented_local_exception(tmp_path: Path) -> None:
    """A local exception raised by a method must appear in Raises."""
    source_file = tmp_path / "module.py"
    class_ = _class(
        source_file,
        """Summary.

        Parameters
        ----------
        name : str
            Name displayed.
        count : int
            Counter.

        Attributes
        ----------
        name : str
            Name displayed.
        count : int
            Counter.
        total : int
            Total computed.
        """,
        methods=[_init_method(source_file, exceptions=[_exception("ValueError", 12)])],
    )

    entries, keys = diagnostics_for_class(
        class_,
        CheckConfig(method_exception_contract=MethodExceptionContract.CLASS_AGGREGATE),
        file="module.py",
    )

    assert [(entry.code, entry.severity, entry.diagnostic_key.expected) for entry in entries] == [
        ("DMT-4301", "error", "valueerror")
    ]
    assert entries[0].annotation_line == 12
    assert keys[entries[0].diagnostic_key] == 1


def test_class_does_not_report_documented_exception(tmp_path: Path) -> None:
    """An exception documented by its simple name does not produce DMT-4301."""
    source_file = tmp_path / "module.py"
    class_ = _class(
        source_file,
        """Summary.

        Parameters
        ----------
        name : str
            Name displayed.
        count : int
            Counter.

        Attributes
        ----------
        name : str
            Name displayed.
        count : int
            Counter.
        total : int
            Total computed.

        Raises
        ------
        builtins.ValueError
            When the value is invalid.
        """,
        methods=[_init_method(source_file, exceptions=[_exception("ValueError", 12)])],
    )

    entries, keys = diagnostics_for_class(
        class_,
        CheckConfig(method_exception_contract=MethodExceptionContract.CLASS_AGGREGATE),
        file="module.py",
    )

    assert entries == []
    assert not keys


def test_class_ignores_propagated_exceptions(tmp_path: Path) -> None:
    """Exceptions propagated by the call graph are not required in Raises."""
    source_file = tmp_path / "module.py"
    class_ = _class(
        source_file,
        """Summary.

        Parameters
        ----------
        name : str
            Name displayed.
        count : int
            Counter.

        Attributes
        ----------
        name : str
            Name displayed.
        count : int
            Counter.
        total : int
            Total computed.
        """,
        methods=[
            _init_method(source_file, exceptions=[_exception("ValueError", 12, provenance=Provenance.CALLGRAPH_PROPAGATION)])
        ],
    )

    entries, keys = diagnostics_for_class(
        class_,
        CheckConfig(method_exception_contract=MethodExceptionContract.CLASS_AGGREGATE),
        file="module.py",
    )

    assert entries == []
    assert not keys


def test_class_aggregate_deduplicates_normalized_exceptions(tmp_path: Path) -> None:
    """A class emits at most one aggregate diagnostic per normalized exception."""
    source_file = tmp_path / "module.py"
    class_ = _class(
        source_file,
        """Summary.

        Parameters
        ----------
        name : str
            Name displayed.
        count : int
            Counter.

        Attributes
        ----------
        name : str
            Name displayed.
        count : int
            Counter.
        total : int
            Total computed.
        """,
        methods=[
            _init_method(source_file, exceptions=[_exception("ValueError", 12)]),
            _init_method(source_file, exceptions=[_exception("builtins.ValueError", 13)]),
        ],
    )

    entries, keys = diagnostics_for_class(
        class_,
        CheckConfig(method_exception_contract=MethodExceptionContract.CLASS_AGGREGATE),
        file="module.py",
    )

    assert [(entry.code, entry.expected) for entry in entries] == [("DMT-4301", "ValueError")]
    assert len(keys) == 1


def test_class_aggregate_ignores_locally_caught_exception(tmp_path: Path) -> None:
    """A locally swallowed exception is not part of the class contract."""
    source_file = tmp_path / "module.py"
    class_ = _class(
        source_file,
        """Summary.

        Parameters
        ----------
        name : str
            Name displayed.
        count : int
            Counter.

        Attributes
        ----------
        name : str
            Name displayed.
        count : int
            Counter.
        total : int
            Total computed.
        """,
        methods=[
            _init_method(
                source_file,
                exceptions=[_exception("ValueError", 12)],
                except_blocks=[
                    ExceptBlock(
                        caught_types=["ValueError"],
                        is_reraised=False,
                        is_swallowed=True,
                        line=14,
                        try_start=10,
                        try_end=12,
                    )
                ],
            )
        ],
    )

    entries, keys = diagnostics_for_class(
        class_,
        CheckConfig(method_exception_contract=MethodExceptionContract.CLASS_AGGREGATE),
        file="module.py",
    )

    assert entries == []
    assert not keys


def test_class_aggregate_falls_back_to_callable_when_class_is_not_selected(tmp_path: Path) -> None:
    """Excluding classes keeps method-level DMT-4001 diagnostics available."""
    method = _init_method(tmp_path / "module.py", exceptions=[_exception("ValueError", 12)])

    entries, _keys = diagnostics_for_function(
        method,
        CheckConfig(
            symbol_kinds=frozenset({SymbolKind.METHOD}),
            method_exception_contract=MethodExceptionContract.CLASS_AGGREGATE,
        ),
        file="module.py",
    )

    assert any(entry.code == "DMT-4001" for entry in entries)


def test_class_aggregate_suppresses_method_dmt4001(tmp_path: Path) -> None:
    """A covered method and class produce one aggregate diagnostic, not two rules."""
    source_file = tmp_path / "module.py"
    class_ = _class(
        source_file,
        """Summary.

        Parameters
        ----------
        name : str
            Name displayed.
        count : int
            Counter.

        Attributes
        ----------
        name : str
            Name displayed.
        count : int
            Counter.
        total : int
            Total computed.
        """,
        methods=[_init_method(source_file, exceptions=[_exception("ValueError", 12)])],
    )
    module = ModuleRecord(
        file_path=source_file,
        module_name="module",
        line_start=1,
        line_end=20,
        visibility=Visibility.PUBLIC,
        existing_docstring=None,
        classes=[class_],
    )

    entries, _keys = produce_diagnostics(
        module,
        CheckConfig(method_exception_contract=MethodExceptionContract.CLASS_AGGREGATE),
        file="module.py",
    )

    assert [entry.code for entry in entries].count("DMT-4001") == 0
    assert [entry.code for entry in entries].count("DMT-4301") == 1


def test_class_aggregate_reports_exceptions_when_class_docstring_is_absent(tmp_path: Path) -> None:
    """The aggregate owner still reports exposed exceptions without a class docstring."""
    source_file = tmp_path / "module.py"
    class_ = _class(source_file, "", methods=[_init_method(source_file, exceptions=[_exception("ValueError", 12)])])

    entries, _keys = diagnostics_for_class(
        class_,
        CheckConfig(method_exception_contract=MethodExceptionContract.CLASS_AGGREGATE),
        file="module.py",
    )

    assert [entry.code for entry in entries] == ["DMT-1110", "DMT-4301"]
