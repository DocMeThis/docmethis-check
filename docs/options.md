# Options

DocMeThis Check can be configured from the command line, a GitHub Action, or
the target project's `pyproject.toml`. The same logical option names and values
are available in each case.

The option names below are written without a leading `--`. Use the name as a
configuration key or Action input, and add `--` when using the command line.
Each channel uses its native representation:

- scalar values use `--profile standard`, `profile: standard`, or
  `profile = "standard"`;
- collections are comma-separated in CLI and Action values, and TOML arrays in
  `pyproject.toml`;
- booleans use positive and negative CLI flags, exact lowercase `true` or
  `false` Action values, and native TOML booleans;
- per-code severity maps use comma-separated `CODE=LEVEL` pairs in CLI and
  Action values, and `[tool.docmethis.check.severity]` in TOML.

For options that allow an empty collection, an empty CLI value or TOML array
explicitly clears it. Because an empty Action input means "not supplied," use
the quoted Action value `'[]'` to clear `property-accessors`, either path list,
or the severity map. `include-visibility` and `symbol-kinds` must remain
non-empty in every channel.

Most options answer one of three questions:

- Which project and Git change should be checked?
- Where should the result go?
- Which findings and severities should be enforced?

## Project Configuration

Shared Check policy belongs in the target project's `pyproject.toml`, under
`[tool.docmethis.check]`:

```toml
[tool.docmethis.check]
profile = "standard"
check-mode = "regression"
exclude-paths = ["tests"]

[tool.docmethis.check.severity]
DMT-4201 = "warning"
```

The effective value is resolved in this order:

1. An explicit command-line option or Action input.
2. The value in `pyproject.toml`.
3. The built-in default.

An empty Action input is not an override. It leaves the project configuration
in control. TOML configuration keys use `kebab-case`.

For example, the same `profile` setting is represented as follows:

```text
--profile standard
profile: standard
profile = "standard"
```

The first form is a command-line argument, the second an Action input, and the
third a TOML value. The sections below describe each setting once,
independently of that syntax.

Unknown CLI or TOML option names and invalid types, enum values, collection
members, diagnostic codes, severity values, or Action booleans are
configuration errors and return exit status `2`; they never silently fall back
to another value.

See [Diagnostic Codes](diagnostic-codes.md) for the complete severity matrix.
The optional `config/forbidden_terms.txt` vocabulary file is loaded from the
selected project by convention; it is an auxiliary project file, not a Check
option.

## Project

### `project-path`

Path to the Python project to analyze. The project path is used as the root for
Git commands, source discovery, relative path filters, and the project's
`pyproject.toml`.

The default is the current directory. Use `--project-path PATH` in the CLI,
`project-path: PATH` in the Action, or `project-path = "PATH"` in TOML. A
relative CLI or Action path is resolved from the current workspace, which is
normally `GITHUB_WORKSPACE` in the Action. A TOML path is resolved from the
directory containing that `pyproject.toml`.

TOML project selection uses one bootstrap step. Check first reads the
`pyproject.toml` in the invocation workspace to resolve `project-path`, then
loads policy from the selected project's `pyproject.toml`. The selected
project cannot redirect to a second project. CLI and non-empty Action values
take precedence over the bootstrap value; the Action leaves `project-path`
empty when no override is requested.

The target should be a Git working tree when using the default diff discovery.
Use an explicit Git range only when the required commits are available in that
working tree.

## Git Diff And Base Handling

These options determine which change is examined before Check evaluates any
docstring contract. Check remains diff-scoped: it may parse other files for
context, but it does not turn a normal pull request into a repository-wide
audit.

### `git-diff`

Analyze an explicit Git revision or range, such as `HEAD`, `HEAD~1..HEAD`, or
`origin/main...HEAD`. This is useful when running outside a CI event, when a
workflow needs a precise comparison, or when reproducing a CI result locally.

The accepted revision forms mean different things:

- `REV` compares the tracked index and working tree with that revision.
- `A..B` compares the revisions directly.
- `A...B` compares `B` with the merge base of `A` and `B`, which is usually the
  right shape for a pull-request comparison.

An explicit revision or range takes precedence over automatic local or CI diff
discovery. Every referenced revision must be valid. A misspelled or unavailable
revision fails during diff resolution; `on-missing-base` cannot repair it.

Without this option, a local run uses the equivalent of `git diff HEAD`: it
includes tracked staged and unstaged changes, but not files that are still
untracked. Check recognizes GitHub pull-request and push metadata and GitLab
push metadata. Other environments fall back to local discovery. Check uses
only locally available repository history; it does not fetch references or
deepen a shallow checkout.

### `base-ref`

Use a locally available Git reference when Check must resolve a CI push diff,
especially for a non-linear push. A non-linear push is one where the event's
previous commit is not an ancestor of the current commit, so the previous
commit alone is not a safe comparison point.

When a usable reference is available, Check finds its merge base with the
current commit and compares that merge base with the current commit. Branches,
remote-tracking references, tags, and commit SHAs are accepted and used as
provided. This avoids silently checking the wrong change after a branch rewrite
or an unusual push sequence.

The default is unset. The reference must be available in the local checkout;
a shallow checkout that does not contain the required history can still fail
to resolve it.

### `on-missing-base`

Choose what to do when a valid diff has been found but the base snapshot of a
changed file cannot be read. This is a file-level problem during regression
filtering of direct diagnostics, not a failure to resolve the Git range itself.
It does not alter `catchup` selection or DIA reconstruction.

- `emit_all`: report the current diagnostics without subtracting diagnostics
  found in the unavailable base. For that file, this behaves like a catch-up
  report and is marked as incomplete.
- `fail`: stop the check instead of reporting a result whose regression filter
  is incomplete.

The default is `emit_all`. A newly added file is treated as having an empty
base, so it is not considered a missing snapshot. This lets a new undocumented
function be reported even when `on-missing-base = "fail"` is selected.

### `on-nonlinear-push-without-base`

Choose what to do when a CI push is non-linear and no reliable base can be
found. This decision happens before Check has a usable range for the push.

- `fail`: stop the check. This is the safest default for a required CI gate.
- `head_commit`: compare the current commit with its parent. This gives a
  useful local approximation, but the result is marked `partial` because it
  may not represent the whole push.
- `warn`: emit a no-reliable-base warning and skip direct and DIA analysis
  rather than inventing a range. The result has no complete diff scope. It
  exits `0` by default and `1` when `fail-on-warning` is enabled.

Use `base-ref` when a reliable reference is available. Do not use
`on-missing-base` for this situation: that option applies only after a valid
range has already been resolved.

## Output And Execution

The output options do not change which diagnostics are produced. They control
how the result is displayed or stored.

### `format`

Choose the report format written to standard output:

- `text`: a human-readable report grouped by file and symbol. This is the
  default for local use and logs.
- `json`: the versioned machine-readable report for bots and remediation
  tools. It includes a checked-file count and path list, structured source
  analysis failures, diff metadata, regression status, diagnostics, and DIA
  results.

When either explicit output file is configured, standard output is suppressed.
`format` selects standard-output presentation only; each file channel has its
fixed format. The Action emits GitHub annotations by default. An explicit
`format`, `json-output-file`, or `github-output-file` replaces that default and
uses the same routing rules as the CLI. `annotation-placement` only changes
where annotations point. `color`, `ascii`, and `verbose` apply when text output
is selected and do not change the output channel by themselves.

### `json-output-file`

Write the versioned JSON report to the given path. This is useful when a
workflow uploads a report, when another job consumes the result, or when a
local run needs a stable artifact. The default is unset.

The path must be different from `github-output-file`. Setting this option also
suppresses the normal standard-output report. Relative paths are resolved from
the process working directory, not `project-path`, and the parent directory
must already exist.

### `github-output-file`

Write GitHub annotation output to the given path. The file contains the
workflow annotation channel rather than the canonical JSON model. It is useful
when a wrapper or Action needs to publish diagnostics in the GitHub UI. The
user-configured default is unset; the Action's default routing creates an
internal annotation channel when no explicit `format` or output file is set.

The path is an output channel, not a second report format: when it is set,
standard output is suppressed unless another process explicitly prints it.
Relative paths and parent-directory requirements are the same as for
`json-output-file`. Both output files may be written in one run when their
resolved paths differ.

Source-analysis failures are emitted as file-level GitHub errors. They are
also included in JSON as structured `file`, `type`, and `message` values, are
not counted as successfully checked files, and remain blocking independently
of the selected output channel.

### `color`

Control ANSI colors in text output:

- `auto`: use colors when standard output is an interactive terminal. This is
  the default.
- `never`: disable colors, which is useful for plain log files and snapshots.
- `always`: force colors when a terminal is not detected.

JSON and GitHub annotation files are always color-free. Output files never
receive ANSI escape sequences, even when color is enabled for terminal text.

### `ascii`

Use ASCII-only tree, separator, and status characters instead of the default
Unicode layout. This is useful for terminals, log consumers, or snapshots that
cannot safely preserve Unicode glyphs. It changes presentation only, not the
diagnostics. The default is `false`; use `no-ascii` to override a configured
`true` value.

### `verbose`

Include additional metadata in text output, such as observed values, symbol
visibility, and the diff strategy used. Use it when a finding needs more
context during local investigation. It does not enable additional checks and
does not change the JSON model. The default is `false`; use `no-verbose` to
override a configured `true` value.

### `no-cache`

Ignore `.docmethis_cache.json` for this run and do not update it. An existing
cache is neither read nor modified. The cache stores extraction records, not
findings or policy decisions, and is safe to delete when it is stale. It should
stay out of Git.

The cache is enabled by default because reusing unchanged extraction records
can make repeated CI and local runs faster. Disable it when a full reanalysis
is required or when the workspace must remain unchanged. `no-cache` defaults
to `false`; use the CLI flag `--cache` to override a configured `true` value.

## Check Policy

These options determine which symbols are selected, which rules are enabled,
and whether a finding blocks the run.

### `check-mode`

Choose which direct findings to report:

- `regression`: compare current diagnostics with the base diagnostics and
  report only findings introduced by the change. This is the default and keeps
  existing documentation debt out of ordinary pull requests.
- `catchup`: report every current direct finding on diff-selected symbols,
  including findings that already existed in the base.

Both modes remain diff-scoped. `catchup` is not a repository-wide audit unless
the supplied Git range selects the whole repository. For an initial audit, a
common pattern is an empty-tree range together with `catchup`.

`check-mode` is independent of `profile`: the mode controls which findings are
selected, while the profile controls their severity and whether a rule is
disabled. It does not change DIA: when enabled, DIA performs the same
BASE-to-HEAD impact analysis in either mode.

### `profile`

Select the built-in severity policy:

- `loose`: report only contract mismatches likely to mislead readers. It is a
  lower-noise adoption profile and does not report missing docstrings or
  presentation findings.
- `standard`: check normal documentation and contract quality. This is the
  default balance for regular development.
- `strict`: enable every current Check rule and promote more contract and
  behavior gaps to errors while leaving presentation findings as warnings.
  Combine it with `fail-on-warning` when every enabled rule must block.

A profile is a complete policy mapping diagnostic codes to `error`, `warning`,
or `disabled`; it is not just a list of enabled checks. Values in
`[tool.docmethis.check.severity]` override the selected profile for individual
codes. `include-visibility`, `symbol-kinds`, and `check-mode` remain separate
controls. Profiles are monotonic: moving from Loose to Standard to Strict never
disables a code or lowers its severity. In particular, `DMT-4201` is a warning
under Loose and Standard and an error under Strict.

### `severity`

Override individual diagnostic codes after selecting a profile. Valid levels
are `error`, `warning`, and `disabled`. For CLI and Action use comma-separated
`CODE=LEVEL` pairs; in TOML use a table:

```toml
[tool.docmethis.check.severity]
DMT-4201 = "error"
```

Overrides are authoritative for direct and DIA findings. Provenance,
confidence, completeness, and an added or removed direction do not silently
downgrade an explicit `error`. Unknown or retired codes, including `DMT-1301`,
and invalid levels are configuration errors. Maps merge by code: CLI and Action
entries override matching TOML entries while unspecified TOML entries remain.
An empty CLI value or an Action value of `'[]'` is the explicit exception and
clears the complete TOML map. Duplicate codes in one CLI or Action value are
errors. The default override map is empty.

### `fail-on-warning`

Make warnings fail the check. Without this option, warnings are still emitted
and counted, but they do not change the exit status. Errors and source analysis
failures remain blocking regardless of this setting.

The default is not to fail on warnings. Use `no-fail-on-warning` to explicitly
keep warnings non-blocking when a project configuration enables the policy.

This option is useful for a strict CI gate, while leaving it disabled is useful
when a team wants to see new warnings before making them release-blocking. The
synthetic warning emitted by `on-nonlinear-push-without-base = "warn"` follows
the same policy.

### `include-visibility`

Select the visibilities to check, separated by commas: `public`, `protected`,
and `private`.

The default is `public`, which keeps ordinary checks focused on the documented
surface of the package. Add `protected` or `private` when the project treats
internal APIs as contracts too. Visibility filtering is independent of the
severity profile: a strict profile does not automatically select private
symbols.

Visibility is effective rather than name-only. Check combines the visibility
of the module, enclosing class, and symbol and uses the most restrictive
result. A public function in an internal module is protected, and a public
method in a private class is private. The effective visibility gates both
direct diagnostics and DIA.

The same rule applies to each class or instance attribute: Check combines the
attribute's name-based visibility with its module and enclosing class before
applying this option and selecting `DMT-1150`, `DMT-1250`, or `DMT-1350`.

### `symbol-kinds`

Select the symbol kinds to check, separated by commas: `function`, `method`,
`class`, and `module`.

The default is `function,method,class`. Add `module` for module-level
docstrings, or narrow the list when a project wants to focus on a particular
kind of contract. This setting controls symbol selection; it does not change
the rules applied to selected symbols.

### `property-accessors`

Select the property accessors to check, separated by commas: `getter` and
`setter`. Both are checked by default.

Use this when a project documents one accessor role differently from the
other. An empty value disables property accessor checks without disabling
regular method checks. The selection applies to direct checks, DIA behavioral
impacts, and DIA API differences. In TOML, use
`property-accessors = []`.

### `annotation-placement`

Choose where source annotations and text frames point:

- `signature`: the symbol signature or definition. This is the default and
  gives one stable location for a symbol.
- `docstring`: the documentation block, which is useful when the finding is
  primarily a documentation correction.
- `precise`: the most precise known source location. A symbol can receive
  multiple frames when different findings target different lines.

This option changes the location shown to a developer; it does not change the
finding, its severity, or the exit status.

### `method-exception-contract`

Choose where exceptions exposed by class methods are documented:

- `callable`: document them on each method. This is the default and keeps each
  callable's contract local.
- `class_aggregate`: document the exposed exceptions in the class-level
  `Raises` section. This is useful when the class owns the aggregate contract.

The selected ownership model affects direct exception diagnostics. In
particular, the class-level aggregate rule is only relevant when
`class_aggregate` is chosen and both `class` and `method` are selected by
`symbol-kinds`. Without `class`, selected methods keep callable-level exception
diagnostics; without `method`, there are no method exceptions to aggregate.

### `dia`

Enable Documentation Impact Analysis (DIA). Direct checks compare a symbol's
current code and docstring. DIA additionally compares BASE and HEAD to detect
behavioral contract changes that a signature-only check may miss, including
changes involving exceptions, I/O, inheritance, overrides, and affected
callers.

DIA is enabled by default. Use `no-dia` when there is no meaningful base, such
as an initial audit, or when only direct documentation checks are wanted. DIA
reports whether its analysis is `complete`, `partial`, or `inconclusive` rather
than hiding missing context. DIA follows `symbol-kinds`, effective visibility,
`property-accessors`, and path-emission policy, but remains a BASE-to-HEAD
comparison independently of `check-mode`.

### `exclude-paths`

Exclude project-relative files or directory prefixes from both direct Check
diagnostics and DIA diagnostics. Pass multiple values as a comma-separated
list in CLI and Action values or as a TOML array. The default is empty.

This is useful for generated code, vendored code, or tests that should not be
treated as documentation contracts. This is an emission filter, not an
analysis-input filter: matching files and their BASE-to-HEAD changes remain in
the extraction models and call graphs. A change in an excluded helper can
therefore still produce an impact on a non-excluded caller.

Filters match exact paths and directory prefixes. They do not use glob
patterns; empty paths, absolute paths, the current directory, and paths
containing `..` are not accepted.

### `dia-exclude-paths`

Exclude project-relative files or directory prefixes from DIA only. Direct
Check diagnostics remain enabled for these paths. Use this when a path should
still satisfy its own docstring contract but should not produce behavioral
impact findings.

Pass multiple values as a comma-separated list in CLI and Action values or as
a TOML array. `exclude-paths` also suppresses DIA diagnostics on a path, so the
two filters are combined for emission. Matching files and changes remain
available as DIA context. The default is empty.

## Exit Status

The check returns:

- `0`: the check completed without blocking findings; tolerated warnings may
  still have been emitted.
- `1`: error diagnostics or source-analysis failures were found, or at least
  one warning was emitted while `fail-on-warning` was enabled.
- `2`: command-line, Action, or TOML validation failed, Git scope could not be
  resolved, output could not be written, or another execution error occurred.

Use `fail-on-warning` when warnings should be treated as blocking. Output and
verbosity options do not change the exit status.
