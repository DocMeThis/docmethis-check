# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Tests for C2 of the check module."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from textwrap import dedent

import pytest
from docmethis_extract_python.api import PropertyAccessor, Visibility

from docmethis_check import cli, diagnostics
from docmethis_check.config import CheckConfig, load_check_config, parse_path_filters, path_matches_filters
from docmethis_check.formatters.github import format as format_github
from docmethis_check.models import (
    AnnotationPlacement,
    CheckEntry,
    CheckMode,
    CheckResult,
    MethodExceptionContract,
    OnMissingBase,
    SymbolKind,
)
from docmethis_check.runner import run_check


def _write_module(tmp_path: Path, payload: str) -> Path:
    source_file = tmp_path / "module.py"
    source_file.write_text(dedent(payload).strip() + "\n", encoding="utf-8")
    return source_file


def _git(tmp_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True)


def _initialize_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@test")
    _git(tmp_path, "config", "user.name", "Test")


def _commit(tmp_path: Path, message: str) -> None:
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", message)


def test_cli_defaults_project_to_current_directory() -> None:
    """The CLI uses the current directory when no project path is provided."""
    args = cli.create_parser().parse_args([])

    assert args.project == Path()


def test_github_format_produces_annotations() -> None:
    """The GitHub formatter converts checks into workflow commands."""
    outcome = CheckResult()
    outcome.checks.append(
        CheckEntry(
            severity="warning",
            code="DMT-3001",
            symbol="module.computes",
            file="src/pkg:mod.py",
            line_start=10,
            col_start=1,
            message="Return not documented, verify 100% of case.",
            docstring_line=12,
        )
    )

    text = format_github(outcome, annotation_placement=AnnotationPlacement.DOCSTRING)

    assert text == (
        "::warning file=src/pkg%3Amod.py,line=12,col=1,title=DMT-3001::"
        "Return not documented, verify 100%25 of case. (DMT-3001).\n"
    )


def test_config_loads_severity_and_fail_on_warning(tmp_path: Path) -> None:
    """The OSS configuration is loaded from pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.docmethis.check]
            fail-on-warning = true
            include-visibility = ["public", "protected"]

            [tool.docmethis.check.severity]
            DMT-1120 = "warning"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    config = load_check_config(tmp_path)

    assert config.fail_on_warning is True
    assert config.include_visibility == frozenset({Visibility.PUBLIC, Visibility.PROTECTED})
    assert config.severity_for("DMT-1120", "error") == "warning"


def test_config_loads_path_filters_and_matches_directory_boundaries(tmp_path: Path) -> None:
    """Path filters load from pyproject and match files without prefix collisions.

    Parameters
    ----------
    tmp_path : Path
        Temporary project root used for the configuration file and path checks.

    """
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.docmethis.check]
exclude-paths = ["tests", "generated.py"]
dia-exclude-paths = ["fixtures"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config = load_check_config(tmp_path)

    assert config.exclude_paths == ("tests", "generated.py")
    assert config.dia_exclude_paths == ("fixtures",)
    assert parse_path_filters("tests, generated.py") == ("tests", "generated.py")
    assert path_matches_filters(tmp_path / "tests" / "unit.py", tmp_path, config.exclude_paths)
    assert path_matches_filters(tmp_path / "generated.py", tmp_path, config.exclude_paths)
    assert not path_matches_filters(tmp_path / "testsuite" / "unit.py", tmp_path, config.exclude_paths)


def test_config_rejects_unsafe_path_filters() -> None:
    """Path filters stay relative and do not accept glob syntax."""
    with pytest.raises(ValueError, match="parent-directory"):
        CheckConfig(exclude_paths=("../tests",))
    with pytest.raises(ValueError, match="glob"):
        CheckConfig(exclude_paths=("tests/*",))


def test_config_rejects_unknown_or_underscore_keys(tmp_path: Path) -> None:
    """Check configuration uses only the documented kebab-case TOML keys."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.docmethis.check]\nfail_on_warning = true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fail_on_warning"):
        load_check_config(tmp_path)

    (tmp_path / "pyproject.toml").write_text(
        "[tool.docmethis.check]\nunknown-option = true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown-option"):
        load_check_config(tmp_path)


def test_config_loads_builtin_profile_before_code_overrides(tmp_path: Path) -> None:
    """A named profile supplies defaults while the project table remains authoritative."""
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.docmethis.check]
profile = "strict"

[tool.docmethis.check.severity]
DMT-2001 = "warning"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config = load_check_config(tmp_path)

    assert config.profile == "strict"
    assert config.severity_for("DMT-2001", "warning") == "warning"
    assert config.severity_for("DMT-2003", "warning") == "error"


@pytest.mark.parametrize(
    ("profile", "code", "expected"),
    [
        ("loose", "DMT-2002", "error"),
        ("loose", "DMT-2001", "warning"),
        ("loose", "DMT-2003", "warning"),
        ("loose", "DMT-2120", "disabled"),
        ("loose", "DMT-3010", "disabled"),
        ("loose", "DMT-1120", "disabled"),
        ("loose", "DMT-1220", "disabled"),
        ("loose", "DMT-4202", "warning"),
        ("standard", "DMT-1120", "error"),
        ("standard", "DMT-1201", "warning"),
        ("standard", "DMT-1301", "disabled"),
        ("standard", "DMT-1320", "disabled"),
        ("standard", "DMT-2003", "warning"),
        ("standard", "DMT-2120", "warning"),
        ("standard", "DMT-3001", "error"),
        ("standard", "DMT-4201", "warning"),
        ("standard", "DMT-6201", "warning"),
        ("standard", "DMT-6051", "warning"),
        ("strict", "DMT-1120", "error"),
        ("strict", "DMT-4201", "error"),
        ("strict", "DMT-4102", "error"),
        ("strict", "DMT-6051", "warning"),
    ],
)
def test_builtin_profiles_classify_codes(profile: str, code: str, expected: str) -> None:
    """Built-in profiles provide complete policy values for representative code families."""
    config = CheckConfig(profile=profile)

    assert config.severity_for(code, "warning") == expected


def test_config_defaults_to_standard_profile() -> None:
    """Standard is the default adoption target for Check."""
    assert CheckConfig().profile == "standard"


def test_profile_cli_exposes_builtin_choices_without_setting_an_override(tmp_path: Path) -> None:
    """The CLI lists built-ins and leaves a project profile untouched when the option is omitted."""
    (tmp_path / "pyproject.toml").write_text('[tool.docmethis.check]\nprofile = "strict"\n', encoding="utf-8")
    parser = cli.create_parser()
    help_text = " ".join(parser.format_help().split())
    args = parser.parse_args([str(tmp_path)])
    config = load_check_config(tmp_path, profile=args.profile)

    assert "--profile {loose,standard,strict}" in help_text
    assert "default: standard" in help_text
    assert args.profile is None
    assert config.profile == "strict"


def test_profile_cli_rejects_unknown_choice() -> None:
    """The CLI rejects profile names that are not built in before running Check."""
    with pytest.raises(SystemExit, match="2"):
        cli.create_parser().parse_args([".", "--profile", "custom"])


def test_code_override_can_promote_a_disabled_profile_code() -> None:
    """An explicit code override can enable a rule omitted by the selected profile."""
    config = CheckConfig(profile="loose", severity={"DMT-1120": "error"})

    assert config.severity_for("DMT-1120", "warning") == "error"


def test_builtin_profiles_are_complete_policies() -> None:
    """Every built-in profile classifies the same current code set."""
    path = Path(__file__).resolve().parents[2] / "src/docmethis_check/defaults/severity_profiles.toml"
    profiles = tomllib.loads(path.read_text(encoding="utf-8"))

    assert set(profiles) == {"loose", "standard", "strict"}
    code_sets = [set(profile) for profile in profiles.values()]
    assert code_sets[0] == code_sets[1] == code_sets[2]
    assert "disabled" not in set(profiles["strict"].values())


def test_config_rejects_unknown_profile(tmp_path: Path) -> None:
    """Unknown built-in profiles fail before the check runs."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.docmethis.check]\nprofile = "unknown"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown profile"):
        load_check_config(tmp_path)


def test_base_diagnostics_returns_none_on_git_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A Git show timeout leaves the base unavailable without crashing the check."""

    def fake_run(_cmd: list[str], **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd=["git", "show"], timeout=10)

    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)

    outcome = diagnostics.base_diagnostics("HEAD~1", tmp_path / "module.py", tmp_path, CheckConfig())

    assert outcome is None


class TestConfigEnum:
    """Check that StrEnum fields are read and validated from pyproject.toml."""

    def test_check_mode_defaults(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.docmethis.check]\n", encoding="utf-8")
        config = load_check_config(tmp_path)
        assert config.check_mode is CheckMode.REGRESSION
        assert config.check_mode == CheckMode.REGRESSION

    def test_check_mode_from_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[tool.docmethis.check]\ncheck-mode = "catchup"\n', encoding="utf-8")
        config = load_check_config(tmp_path)
        assert config.check_mode is CheckMode.CATCHUP

    def test_check_mode_value_invalid(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[tool.docmethis.check]\ncheck-mode = "regresion"\n', encoding="utf-8")
        with pytest.raises(ValueError, match="regresion"):
            load_check_config(tmp_path)

    def test_annotation_placement_defaults(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.docmethis.check]\n", encoding="utf-8")
        config = load_check_config(tmp_path)
        assert config.annotation_placement is AnnotationPlacement.SIGNATURE

    def test_annotation_placement_from_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[tool.docmethis.check]\nannotation-placement = "precise"\n', encoding="utf-8")
        config = load_check_config(tmp_path)
        assert config.annotation_placement is AnnotationPlacement.PRECISE

    def test_annotation_placement_value_invalid(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[tool.docmethis.check]\nannotation-placement = "wrong"\n', encoding="utf-8")
        with pytest.raises(ValueError, match="wrong"):
            load_check_config(tmp_path)

    def test_on_missing_base_defaults(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.docmethis.check]\n", encoding="utf-8")
        config = load_check_config(tmp_path)
        assert config.on_missing_base is OnMissingBase.EMIT_ALL

    def test_on_missing_base_from_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[tool.docmethis.check]\non-missing-base = "fail"\n', encoding="utf-8")
        config = load_check_config(tmp_path)
        assert config.on_missing_base is OnMissingBase.FAIL

    def test_on_missing_base_value_invalid(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[tool.docmethis.check]\non-missing-base = "catchup"\n', encoding="utf-8")
        with pytest.raises(ValueError, match="catchup"):
            load_check_config(tmp_path)

    def test_check_mode_from_cli(self) -> None:
        config = load_check_config(Path("/inexistant"), check_mode="catchup")
        assert config.check_mode is CheckMode.CATCHUP

    def test_on_missing_base_from_cli(self) -> None:
        config = load_check_config(Path("/inexistant"), on_missing_base="fail")
        assert config.on_missing_base is OnMissingBase.FAIL

    def test_method_exception_contract_defaults_to_callable(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.docmethis.check]\n", encoding="utf-8")
        config = load_check_config(tmp_path)
        assert config.method_exception_contract is MethodExceptionContract.CALLABLE

    def test_method_exception_contract_from_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.docmethis.check]\nmethod-exception-contract = "class_aggregate"\n',
            encoding="utf-8",
        )
        config = load_check_config(tmp_path)
        assert config.method_exception_contract is MethodExceptionContract.CLASS_AGGREGATE

    def test_method_exception_contract_value_invalid(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.docmethis.check]\nmethod-exception-contract = "method"\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="method"):
            load_check_config(tmp_path)

    def test_method_exception_contract_from_cli(self) -> None:
        config = load_check_config(Path("/inexistant"), method_exception_contract="class_aggregate")
        assert config.method_exception_contract is MethodExceptionContract.CLASS_AGGREGATE


def test_cli_fail_on_warning_controls_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """fail_on_warning changes both the displayed status and exit code."""
    outcome = CheckResult()
    outcome.summary.warning_count = 1
    outcome.checks.append(
        CheckEntry(
            severity="warning",
            code="DMT-3001",
            symbol="module.computes",
            file=str(tmp_path / "module.py"),
            line_start=1,
            col_start=1,
            message="Return not documented.",
        )
    )
    monkeypatch.setattr(cli, "run_check", lambda **_: outcome)

    assert cli.main([str(tmp_path)]) == 0
    assert "DocMeThis Check — WARN" in capsys.readouterr().out
    assert cli.main([str(tmp_path), "--fail-on-warning"]) == 1
    assert "DocMeThis Check — FAIL" in capsys.readouterr().out


def test_cli_no_fail_on_warning_overrides_pyproject(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The action can disable fail_on_warning even when pyproject.toml enables it."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.docmethis.check]\nfail-on-warning = true\n",
        encoding="utf-8",
    )
    outcome = CheckResult()
    outcome.summary.warning_count = 1
    outcome.checks.append(
        CheckEntry(
            severity="warning",
            code="DMT-3001",
            symbol="module.computes",
            file=str(tmp_path / "module.py"),
            line_start=1,
            col_start=1,
            message="Return not documented.",
        )
    )
    monkeypatch.setattr(cli, "run_check", lambda **_: outcome)

    assert cli.main([str(tmp_path)]) == 1
    assert "DocMeThis Check — FAIL" in capsys.readouterr().out
    assert cli.main([str(tmp_path), "--no-fail-on-warning"]) == 0
    assert "DocMeThis Check — WARN" in capsys.readouterr().out


def test_cli_analysis_errors_fail_the_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Analysis failures are blocking even when no diagnostic has been emitted."""
    outcome = CheckResult()
    outcome.analysis_errors = [{"file": "broken.py", "type": "parse_error", "message": "Unable to parse the file."}]
    monkeypatch.setattr(cli, "run_check", lambda **_: outcome)

    assert cli.main([str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "DocMeThis Check — FAIL" in output
    assert "Skipped: broken.py" in output


def test_run_check_filters_by_include_visibility(tmp_path: Path) -> None:
    """Protected functions are checked only when the policy includes them."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        """
        def base() -> int:
            return 1


        def _protected() -> int:
            return 2
        """,
    )

    assert run_check(tmp_path).checks == []

    config = CheckConfig(include_visibility=frozenset({Visibility.PUBLIC, Visibility.PROTECTED}))
    outcome = run_check(tmp_path, config=config)

    assert {check.code for check in outcome.checks} == {"DMT-1220", "DMT-3001"}
    assert outcome.checks[0].symbol == "module._protected"


def test_run_check_checks_test_modules_unless_excluded(tmp_path: Path) -> None:
    """Test modules are checked by default and can be excluded explicitly.

    Parameters
    ----------
    tmp_path : Path
        Temporary Git repository containing the changed test module.

    """
    _initialize_repo(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    source_file = tests_dir / "test_module.py"
    source_file.write_text('"""Module summary."""\n', encoding="utf-8")
    _commit(tmp_path, "initial")
    source_file.write_text("VALUE = 1\n", encoding="utf-8")

    config = CheckConfig(dia=False, symbol_kinds=frozenset({SymbolKind.MODULE}))
    outcome = run_check(tmp_path, config=config)

    assert outcome.checks
    assert outcome.checked_files == [str(source_file)]

    excluded = run_check(
        tmp_path,
        config=CheckConfig(dia=False, symbol_kinds=frozenset({SymbolKind.MODULE}), exclude_paths=("tests",)),
    )

    assert excluded.checks == []
    assert excluded.checked_files == []
    assert excluded.diff_files == [{"path": str(source_file), "changed_lines": [1], "deleted_lines": [1]}]


def test_dia_path_exclusion_keeps_direct_checks(tmp_path: Path) -> None:
    """DIA-only exclusions suppress impacts without suppressing direct Check diagnostics.

    Parameters
    ----------
    tmp_path : Path
        Temporary Git repository containing the changed test module.

    """
    _initialize_repo(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    source_file = tests_dir / "test_module.py"
    source_file.write_text(
        dedent(
            '''
            def process(value: int) -> int:
                """Process a value."""
                return value
            '''
        ).lstrip(),
        encoding="utf-8",
    )
    _commit(tmp_path, "initial")
    source_file.write_text(
        dedent(
            '''
            def process(value: int) -> int:
                """Process a value."""
                if value < 0:
                    raise ValueError("negative")
                return value
            '''
        ).lstrip(),
        encoding="utf-8",
    )

    outcome = run_check(tmp_path, config=CheckConfig(profile="strict", dia_exclude_paths=("tests",)))

    assert outcome.checks
    assert all(check.dia is None for check in outcome.checks)
    assert outcome.impact_analysis is not None
    assert outcome.impact_analysis.reason == "all_paths_excluded"


def test_run_check_effective_visibility_for_internal_module(tmp_path: Path) -> None:
    """A public function in an internal module is protected (DMT-1220)."""
    _initialize_repo(tmp_path)
    subdirectory = tmp_path / "_internal"
    subdirectory.mkdir()
    source_file = subdirectory / "module.py"
    source_file.write_text("def base() -> int:\n    return 1\n", encoding="utf-8")
    _commit(tmp_path, "initial")
    source_file.write_text("def base() -> int:\n    return 1\n\n\ndef public() -> int:\n    return 2\n", encoding="utf-8")

    assert run_check(tmp_path).checks == []

    config = CheckConfig(include_visibility=frozenset({Visibility.PUBLIC, Visibility.PROTECTED}))
    outcome = run_check(tmp_path, config=config)

    assert {check.code for check in outcome.checks} == {"DMT-1220", "DMT-3001"}
    assert outcome.checks[0].symbol == "_internal.module.public"


def test_run_check_property_without_docstring(tmp_path: Path) -> None:
    """A getter @property without docstring receives DMT-1140 (block property)."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        class Service:
            def __init__(self) -> None:
                self._value = 1

            @property
            def value(self) -> int:
                return self._value
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        """
        class Service:
            def __init__(self) -> None:
                self._value = 1

            @property
            def value(self) -> int:
                return self._value

            @property
            def other(self) -> int:
                return 0
        """,
    )

    outcome = run_check(tmp_path)

    codes = [check.code for check in outcome.checks]
    assert "DMT-1140" in codes
    assert "DMT-1130" not in codes


def test_property_accessor_policy_filters_getter_and_setter(tmp_path: Path) -> None:
    content = dedent(
        """
    class Service:
        @property
        def value(self):
            return self._value

        @value.setter
        def value(self, value):
            self._value = value
        """
    ).strip()

    both, both_keys = diagnostics.diagnostics_for_file(content, tmp_path / "module.py", tmp_path, CheckConfig())
    getter_only, getter_keys = diagnostics.diagnostics_for_file(
        content,
        tmp_path / "module.py",
        tmp_path,
        CheckConfig(property_accessors=frozenset({PropertyAccessor.GETTER})),
    )

    missing_both = [entry for entry in both if entry.code == "DMT-1140"]
    missing_getter = [entry for entry in getter_only if entry.code == "DMT-1140"]
    assert {entry.accessor_kind for entry in missing_both} == {"getter", "setter"}
    assert [entry.accessor_kind for entry in missing_getter] == ["getter"]
    assert {key.accessor_kind for key in both_keys if key.code == "DMT-1140"} == {"getter", "setter"}
    assert {key.accessor_kind for key in getter_keys if key.code == "DMT-1140"} == {"getter"}


def test_empty_property_accessor_policy_keeps_normal_methods(tmp_path: Path) -> None:
    content = dedent(
        """
    class Service:
        def normal(self):
            return 1

        @property
        def value(self):
            return self._value

        @value.setter
        def value(self, value):
            self._value = value
        """
    ).strip()

    entries, _keys = diagnostics.diagnostics_for_file(
        content,
        tmp_path / "module.py",
        tmp_path,
        CheckConfig(property_accessors=frozenset()),
    )

    assert any(entry.code == "DMT-1130" for entry in entries)
    assert not any(entry.code == "DMT-1140" for entry in entries)


def test_cli_empty_property_accessor_option_disables_accessors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_load_check_config(_project: Path, **kwargs: object) -> CheckConfig:
        captured.update(kwargs)
        return CheckConfig()

    monkeypatch.setattr(cli, "load_check_config", fake_load_check_config)
    monkeypatch.setattr(cli, "run_check", lambda **_: CheckResult())

    assert cli.main([str(tmp_path), "--property-accessors", ""]) == 0
    assert captured["property_accessors"] == frozenset()


def test_run_check_public_method_on_private_class(tmp_path: Path) -> None:
    """A public method of a private class is private (DMT-1330)."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        class __Service:
            def __init__(self) -> None:
                pass
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        """
        class __Service:
            def __init__(self) -> None:
                pass

            def public(self) -> int:
                return 1
        """,
    )

    config = CheckConfig(
        include_visibility=frozenset({Visibility.PUBLIC, Visibility.PROTECTED, Visibility.PRIVATE}),
        profile="strict",
    )
    outcome = run_check(tmp_path, config=config)

    codes = {check.code for check in outcome.checks}
    assert "DMT-1330" in codes
    assert "DMT-1130" not in codes


def test_run_check_selection_combines_class_visibility(tmp_path: Path) -> None:
    """Selection combines module, class, and method visibility."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        class __Service:
            def __init__(self) -> None:
                pass
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        """
        class __Service:
            def __init__(self) -> None:
                pass

            def public(self) -> int:
                return 1
        """,
    )

    result_private = run_check(
        tmp_path,
        config=CheckConfig(include_visibility=frozenset({Visibility.PRIVATE}), profile="strict"),
    )
    assert any(check.code == "DMT-1330" for check in result_private.checks)

    result_public = run_check(tmp_path)
    assert result_public.checks == []
    assert result_public.summary.pass_count == 0


def test_run_check_public_attribute_on_private_class(tmp_path: Path) -> None:
    """A public attribute of a private class receives DMT-1350 (private field)."""
    _initialize_repo(tmp_path)
    _write_module(
        tmp_path,
        """
        class __Service:
            'Docstring of the class.'

            def __init__(self) -> None:
                self._cache = 1
        """,
    )
    _commit(tmp_path, "initial")
    _write_module(
        tmp_path,
        """
        class __Service:
            'Docstring of the class.'

            def __init__(self) -> None:
                self._cache = 1
                self.value = 2
        """,
    )

    config = CheckConfig(
        include_visibility=frozenset({Visibility.PUBLIC, Visibility.PROTECTED, Visibility.PRIVATE}),
        profile="strict",
    )
    outcome = run_check(tmp_path, config=config)

    codes = {check.code for check in outcome.checks}
    assert "DMT-1350" in codes
    assert "DMT-1150" not in codes


def test_run_check_module_internal_without_docstring(tmp_path: Path) -> None:
    """An internal module without a docstring receives DMT-1201 (protected module)."""
    _initialize_repo(tmp_path)
    subdirectory = tmp_path / "_internal"
    subdirectory.mkdir()
    source_file = subdirectory / "module.py"
    source_file.write_text('"""Summary of module."""\n', encoding="utf-8")
    _commit(tmp_path, "initial")
    source_file.write_text("VALUE = 1\n", encoding="utf-8")

    config = CheckConfig(
        include_visibility=frozenset({Visibility.PUBLIC, Visibility.PROTECTED, Visibility.PRIVATE}),
        symbol_kinds=frozenset({SymbolKind.MODULE}),
    )
    outcome = run_check(tmp_path, config=config)

    assert any(check.code == "DMT-1201" for check in outcome.checks)


def test_cli_github_output_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI writes GitHub annotations to the requested file."""
    outcome = CheckResult()
    outcome.summary.error_count = 1
    outcome.checks.append(
        CheckEntry(
            severity="error",
            code="DMT-1120",
            symbol="module.undocumented",
            file=str(tmp_path / "module.py"),
            line_start=3,
            col_start=1,
            message="Docstring absent.",
        )
    )
    monkeypatch.setattr(cli, "run_check", lambda **_: outcome)
    annotations = tmp_path / "annotations.txt"

    code = cli.main([str(tmp_path), "--github-output-file", str(annotations)])

    assert code == 1
    assert capsys.readouterr().out == ""
    assert annotations.read_text(encoding="utf-8").startswith("::error file=")


def test_cli_text_stdout_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Without an explicit destination or format, the CLI produces text on stdout."""
    outcome = CheckResult()
    outcome.summary.error_count = 1
    outcome.checks.append(
        CheckEntry(
            severity="error",
            code="DMT-1120",
            symbol="module.undocumented",
            file=str(tmp_path / "module.py"),
            line_start=3,
            col_start=1,
            message="Docstring absent.",
        )
    )
    monkeypatch.setattr(cli, "run_check", lambda **_: outcome)

    code = cli.main([str(tmp_path)])
    output = capsys.readouterr().out

    assert code == 1
    assert output.startswith("DocMeThis Check — FAIL\nScope: unknown")
    assert "module.py" in output
    assert str(tmp_path) not in output


def test_cli_color_never_disables_text_colors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The CLI forwards the explicit color mode to the text formatter."""
    outcome = CheckResult()
    outcome.summary.error_count = 1
    outcome.checks.append(
        CheckEntry(
            severity="error",
            code="DMT-1120",
            symbol="module.undocumented",
            file=str(tmp_path / "module.py"),
            line_start=3,
            col_start=1,
            message="Docstring absent.",
        )
    )
    received: dict[str, object] = {}

    def fake_format(_result: CheckResult, **kwargs: object) -> str:
        received.update(kwargs)
        return ""

    monkeypatch.setattr(cli, "run_check", lambda **_: outcome)
    monkeypatch.setattr(cli, "format_text", fake_format)

    code = cli.main([str(tmp_path), "--color=never", "--ascii", "--annotation-placement", "docstring"])

    assert code == 1
    assert received["color"] == "never"
    assert received["ascii_mode"] is True
    assert received["annotation_placement"] is AnnotationPlacement.DOCSTRING


def test_cli_json_stdout_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The explicit JSON format keeps the canonical report on stdout."""
    monkeypatch.setattr(cli, "run_check", lambda **_: CheckResult())

    code = cli.main([str(tmp_path), "--format", "json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["version"] == 1


def test_cli_verbose_adds_text_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The verbose flag adds diagnostic facts to the human-readable output."""
    outcome = CheckResult()
    outcome.summary.error_count = 1
    outcome.checks.append(
        CheckEntry(
            severity="error",
            code="DMT-3001",
            symbol="module.computes",
            file=str(tmp_path / "module.py"),
            line_start=1,
            col_start=1,
            message="Return not documented.",
            visibility="public",
            observed="actual result",
        )
    )
    monkeypatch.setattr(cli, "run_check", lambda **_: outcome)

    code = cli.main([str(tmp_path), "--verbose"])
    output = capsys.readouterr().out

    assert code == 1
    assert "Visibility: public" in output
    assert "Observed: actual result" in output


def test_cli_writes_both_outputs_explicitly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Explicit destinations produce both protocol outputs without stdout."""
    outcome = CheckResult()
    outcome.summary.error_count = 1
    outcome.checks.append(
        CheckEntry(
            severity="error",
            code="DMT-1120",
            symbol="module.undocumented",
            file=str(tmp_path / "module.py"),
            line_start=3,
            col_start=1,
            message="Docstring absent.",
        )
    )
    monkeypatch.setattr(cli, "run_check", lambda **_: outcome)
    annotations = tmp_path / "annotations.txt"
    report = tmp_path / "report.json"

    code = cli.main(
        [
            str(tmp_path),
            "--github-output-file",
            str(annotations),
            "--json-output-file",
            str(report),
        ]
    )

    assert code == 1
    assert capsys.readouterr().out == ""
    assert annotations.read_text(encoding="utf-8").startswith("::error file=")
    assert json.loads(report.read_text(encoding="utf-8"))["checks"][0]["code"] == "DMT-1120"


def test_cli_rejects_identical_output_files(tmp_path: Path) -> None:
    """GitHub and JSON outputs cannot overwrite the same file."""
    output = tmp_path / "output.txt"

    with pytest.raises(SystemExit, match="2"):
        cli.main(
            [
                str(tmp_path),
                "--github-output-file",
                str(output),
                "--json-output-file",
                str(output),
            ]
        )


def test_cli_rejects_removed_format_option(tmp_path: Path) -> None:
    """Unsupported output formats remain rejected by the CLI."""
    with pytest.raises(SystemExit, match="2"):
        cli.main([str(tmp_path), "--format", "github"])


def test_entrypoint_publishes_annotations_from_file() -> None:
    """The Docker action publishes annotations through its explicit file output."""
    payload = Path("entrypoint.sh").read_text(encoding="utf-8")

    assert '--github-output-file "$annotations_file"' in payload
    assert 'cat "$annotations_file"' in payload
    assert "--format github" not in payload


@pytest.mark.docker
def test_dockerfile_builds_and_runs_on_test_project(tmp_path: Path) -> None:
    """The action's Docker image builds and emits an annotation for a test project."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker is not installed.")

    info = subprocess.run([docker, "info"], capture_output=True, text=True, check=False)
    if info.returncode != 0:
        pytest.skip("The Docker daemon is not available.")

    root = Path(__file__).resolve().parents[2]
    tag = f"docmethis-check-action-test:{os.getpid()}"
    build = subprocess.run([docker, "build", "-t", tag, str(root)], capture_output=True, text=True, check=False)
    assert build.returncode == 0, build.stdout + build.stderr

    try:
        _initialize_repo(tmp_path)
        _write_module(
            tmp_path,
            """
            def base() -> int:
                return 1
            """,
        )
        _commit(tmp_path, "initial")
        _write_module(
            tmp_path,
            """
            def base() -> int:
                return 1


            def undocumented() -> int:
                return 2
            """,
        )

        outcome = subprocess.run(
            [
                docker,
                "run",
                "--rm",
                "-v",
                f"{tmp_path}:/github/workspace",
                "-e",
                "GITHUB_WORKSPACE=/github/workspace",
                "-e",
                "GIT_CONFIG_COUNT=1",
                "-e",
                "GIT_CONFIG_KEY_0=safe.directory",
                "-e",
                "GIT_CONFIG_VALUE_0=/github/workspace",
                tag,
                "--project-path",
                ".",
                "--fail-on-warning",
                "false",
                "--include-visibility",
                "",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert outcome.returncode == 1
        assert "::error file=" in outcome.stdout
        assert "DMT-1120" in outcome.stdout
    finally:
        subprocess.run([docker, "rmi", tag], capture_output=True, text=True, check=False)
