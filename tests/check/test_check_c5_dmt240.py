# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""C5 tests for DMT-4001 policy and call-graph provenance."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from docmethis_extract_python.static_extraction.models import (
    ExceptionRecord,
    FunctionRecord,
    MethodType,
    Provenance,
    Visibility,
)
from docmethis_verify.models.contracts import VerificationError, VerificationResponse
from docmethis_verify.models.enums import PatchGranularity, VerificationErrorType

from docmethis_check import diagnostics
from docmethis_check.config import CheckConfig


def _function_for_test() -> FunctionRecord:
    """Build a base function for the verification scenarios."""
    return FunctionRecord(
        qualified_name="module.function",
        file_path=Path("/tmp/module.py"),
        line_start=1,
        line_end=2,
        col_start=1,
        col_end=1,
        method_kind=MethodType.FUNCTION,
        visibility=Visibility.PUBLIC,
        parent_class=None,
        parent_module="module",
        existing_docstring="""Executes business logic.""",
    )


def _error_raises_missing(exception: str, *, provenance: str = "local") -> VerificationError:
    """Build a simulated Verify diagnostic for a missing DMT-4001."""
    return VerificationError(
        error_type=VerificationErrorType.RAISES_MISSING,
        section="Raises",
        granularity=PatchGranularity.ENTRY,
        detail=f"exception '{exception}' not documented",
        expected=exception,
        provenance=provenance,
        dmt_code="DMT-4001",
    )


def _config_default() -> CheckConfig:
    """Return a minimal Check configuration for the tests."""
    return CheckConfig()


def _mock_verification_with_dmt_240(monkeypatch: pytest.MonkeyPatch, exception: str, *, provenance: str = "local") -> None:
    """Inject an Verify response containing only the targeted DMT-4001."""
    response = VerificationResponse(
        errors=(_error_raises_missing(exception, provenance=provenance),),
        warnings=(),
    )
    monkeypatch.setattr(diagnostics, "verify_docstring", lambda *_args, **_kwargs: response)


def test_diagnostics_filter_dmt_240_callgraph_propagation_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """DMT-4001 exceptions with CALLGRAPH_PROPAGATION are ignored."""
    function = _function_for_test()
    function.exceptions = [
        ExceptionRecord(
            exception_type="ValueError",
            message=None,
            raise_line=2,
            provenance=Provenance.CALLGRAPH_PROPAGATION,
        )
    ]
    _mock_verification_with_dmt_240(monkeypatch, "ValueError", provenance="propagated")

    entries, keys = diagnostics.diagnostics_for_function(function, _config_default(), file="module.py")

    assert entries == []
    assert keys == Counter()


def test_diagnostics_keep_dmt_240_local_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local DMT-4001 exceptions are not filtered."""
    function = _function_for_test()
    function.exceptions = [
        ExceptionRecord(
            exception_type="TypeError",
            message=None,
            raise_line=2,
            provenance=Provenance.LOCAL_INFERENCE,
        )
    ]
    _mock_verification_with_dmt_240(monkeypatch, "TypeError", provenance="local")

    entries, keys = diagnostics.diagnostics_for_function(function, _config_default(), file="module.py")

    assert len(entries) == 1
    assert entries[0].code == "DMT-4001"
    assert keys == Counter({entries[0].diagnostic_key: 1})


def test_diagnostics_treat_format_error_as_propagated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The propagated _format_error case is excluded by the call-graph filter."""
    function = _function_for_test()
    function.exceptions = [
        ExceptionRecord(
            exception_type="_format_error",
            message=None,
            raise_line=2,
            provenance=Provenance.CALLGRAPH_PROPAGATION,
        )
    ]
    _mock_verification_with_dmt_240(monkeypatch, "_format_error", provenance="propagated")

    entries, keys = diagnostics.diagnostics_for_function(function, _config_default(), file="module.py")

    assert entries == []
    assert keys == Counter()


def test_diagnostics_keep_local_exception_with_same_name_as_propagated_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local exception with the same name remains reported despite propagation."""
    function = _function_for_test()
    function.exceptions = [
        ExceptionRecord(
            exception_type="builtins.ValueError",
            message=None,
            raise_line=2,
            provenance=Provenance.LOCAL_INFERENCE,
        ),
        ExceptionRecord(
            exception_type="ValueError",
            message=None,
            raise_line=2,
            provenance=Provenance.CALLGRAPH_PROPAGATION,
        ),
    ]
    response = VerificationResponse(
        errors=(
            _error_raises_missing("builtins.ValueError", provenance="local"),
            _error_raises_missing("ValueError", provenance="propagated"),
        ),
        warnings=(),
    )
    monkeypatch.setattr(diagnostics, "verify_docstring", lambda *_args, **_kwargs: response)

    entries, keys = diagnostics.diagnostics_for_function(function, _config_default(), file="module.py")

    assert len(entries) == 1
    assert entries[0].diagnostic_key is not None
    assert entries[0].diagnostic_key.expected == "builtins.ValueError"
    assert keys == Counter({entries[0].diagnostic_key: 1})
