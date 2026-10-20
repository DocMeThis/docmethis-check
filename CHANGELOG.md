# Changelog

Notable user-facing changes for DocMeThis Check.

## [0.1.2] - 2026-10-20

### Added

- `DMT-3013` reports a resolved value-return annotation when Extract proves that the function has no normal return path.
- Severity profiles and per-code overrides support `DMT-3013`.

## [0.1.1] - 2026-09-25

### Added

- Project-relative path exclusions.
- A diagnostic code reference.

### Changed

- Recalibrated built-in severity profiles.
- DIA output improvement.
- Configuration options now consistently use kebab-case.
- Property accessor checks can be disabled independently without disabling regular method checks.
- Extraction records are cached between workflow runs.

### Fixed

- New files are treated as having an empty regression baseline.
- Source analysis failures now cause the check to fail.
