# GitLab CI

DocMeThis Check can run on GitLab.com and GitLab Self-Managed without a
GitLab API client. The reference job runs the CLI from the official Docker
image, produces both Check reports, and uses the same policy and exit codes as
the local CLI and GitHub integration.

## Quick Start

Copy the reference job from the release of `docmethis-check` into the target
repository as `.gitlab-ci.yml`:

```text
https://github.com/DocMeThis/docmethis-check/blob/vX.Y.Z/.gitlab-ci.yml
```

The repository copy is also available at
[`../.gitlab-ci.yml`](../.gitlab-ci.yml). Keep that file as the canonical job
definition instead of maintaining a second GitLab workflow by hand. The
release file provided by the maintainers contains the official immutable image
reference.

## Image Access

The official image is hosted in GHCR and must be pullable by the target runner.
If the runner cannot access GHCR, override `DOCMETHIS_CHECK_IMAGE` with an
equivalent image from an accessible internal registry:

```yaml
variables:
  DOCMETHIS_CHECK_IMAGE: "registry.example.com/platform/docmethis-check@sha256:<digest>"
```

The internal image must be addressed by an immutable digest, and the runner
must already have any registry access required by that installation. The job
does not mirror images or configure registry authentication. Contact the
DocMeThis maintainers when the official release job does not contain a usable
image reference.

The image contains Python, DocMeThis Check, its dependencies, Git, and a
POSIX-compatible shell. The GitLab job clears the image entrypoint and invokes
`python -m docmethis_check` directly. It does not install Check at pipeline
runtime.

## Pipeline Rules

The reference workflow uses these rules:

- A pipeline with `CI_PIPELINE_SOURCE=merge_request_event` runs the MR job.
- A branch pipeline with a non-empty `CI_OPEN_MERGE_REQUESTS` is suppressed.
- A branch without an open MR runs the branch job.
- Other pipeline sources are not enabled by the reference job.

This prevents a push to a branch with an open MR from running Check twice.

## Git Checkout

The job sets:

```yaml
variables:
  GIT_DEPTH: "0"
```

Full history is the safe default because Check must resolve the MR base, the
previous push commit, merge bases, and BASE-to-HEAD impact analysis. Check only
uses revisions already available in the checkout and never fetches or deepens
the repository implicitly.

The job also runs:

```bash
git config --global --add safe.directory "$CI_PROJECT_DIR"
```

`CI_PROJECT_DIR` is the runner checkout directory. This explicit Git setting
avoids `dubious ownership` failures when the runner user and the image user do
not have the same ownership metadata.

## GitLab Variables

| Variable | Role |
| --- | --- |
| `CI_COMMIT_SHA` | Current commit for a push or detached MR pipeline. |
| `CI_COMMIT_BEFORE_SHA` | Previous commit for a push; an all-zero value means no usable base. |
| `CI_MERGE_REQUEST_DIFF_BASE_SHA` | Required MR base revision. |
| `CI_MERGE_REQUEST_SOURCE_BRANCH_SHA` | Source branch HEAD for merged-result and merge-train pipelines. |
| `CI_MERGE_REQUEST_EVENT_TYPE` | Identifies `detached`, `merged_result`, or `merge_train`. |
| `CI_OPEN_MERGE_REQUESTS` | Used by the workflow rules to suppress duplicate branch pipelines. |
| `CI_PROJECT_DIR` | Absolute checkout path used by Git and the job. |
| `GIT_DEPTH` | Checkout depth; `0` is the reliable default. |
| `DOCMETHIS_CHECK_IMAGE` | Versioned image reference, overridable for an internal registry. |

## Diff Resolution

The MR pipeline type determines the exact range. `CI_COMMIT_BEFORE_SHA` never
replaces `CI_MERGE_REQUEST_DIFF_BASE_SHA` in an MR pipeline.

| Pipeline context | BASE | HEAD analyzed |
| --- | --- | --- |
| Push | `CI_COMMIT_BEFORE_SHA` | `CI_COMMIT_SHA` |
| Detached MR | `CI_MERGE_REQUEST_DIFF_BASE_SHA` | `CI_COMMIT_SHA` |
| Merged-result MR | `CI_MERGE_REQUEST_DIFF_BASE_SHA` | `CI_MERGE_REQUEST_SOURCE_BRANCH_SHA` |
| Merge train | `CI_MERGE_REQUEST_DIFF_BASE_SHA` | `CI_MERGE_REQUEST_SOURCE_BRANCH_SHA` when available |

For a merged-result pipeline, `CI_COMMIT_SHA` can identify a synthetic merge
commit and is deliberately not used as the source HEAD. A merge train without
`CI_MERGE_REQUEST_SOURCE_BRANCH_SHA` is reported as incomplete or fails under
the configured policy; it never silently falls back to the synthetic commit.

For pushes, `CI_COMMIT_BEFORE_SHA` is used when it is a local ancestor of
`CI_COMMIT_SHA`. A non-linear push uses a common ancestor when possible. The
`base-ref` option can provide a locally available reference for this
calculation. Check does not fetch that reference for you.

## Check Configuration

The reference job invokes the module without duplicating policy flags. Put
project policy in the target repository's `pyproject.toml`:

```toml
[tool.docmethis.check]
base-ref = "main"
on-nonlinear-push-without-base = "fail"
fail-on-warning = true
```

The equivalent CLI options are `--base-ref`,
`--on-nonlinear-push-without-base`, and `--fail-on-warning`. The project
configuration remains the source of policy for the GitLab job, while
`DOCMETHIS_CHECK_IMAGE` is a CI variable rather than a Check policy setting.

`on-nonlinear-push-without-base` accepts `fail`, `head_commit`, or `warn`:

- `fail` blocks the job when no reliable base exists.
- `head_commit` checks only the current commit's parent and marks the scope partial.
- `warn` emits an incomplete-scope warning and skips analysis that requires a reliable range.

`fail-on-warning` controls whether warnings, including an incomplete-scope
warning, make the job fail. Errors and source-analysis failures remain
blocking.

See [`docs/options.md`](options.md) for the complete CLI and TOML option
reference.

## Reports And Exit Codes

The job writes both files and keeps them as artifacts even when Check returns a
non-zero status:

### `docmethis-report.json`

This is the canonical, versioned DocMeThis report. It contains the complete
summary, diagnostics, source-analysis errors, resolved diff metadata,
regression information, and DIA results.

### `gl-code-quality-report.json`

This is a GitLab Code Quality projection. It contains GitLab findings with
relative paths, locations, severities, descriptions, check names, and stable
fingerprints. It is not a replacement for the canonical DocMeThis report;
source-analysis errors remain in the pivot JSON rather than becoming fake Code
Quality findings.

The reference job declares the Code Quality report under
`artifacts:reports:codequality` and keeps the pivot report under
`artifacts:paths`, both with `when: always`.

Check returns:

- `0` when the configured policy has no blocking result;
- `1` for error diagnostics, source-analysis failures, or blocking warnings;
- `2` for invocation, configuration, Git, output, or other execution errors.

## Self-Managed GitLab

The same job works on GitLab Self-Managed. Override only the image variable
when the runner cannot pull from GHCR:

```yaml
variables:
  DOCMETHIS_CHECK_IMAGE: "registry.example.com/platform/docmethis-check@sha256:<digest>"
```

The runner must already be able to pull that image, including any registry
authentication required by the installation. The Check job itself does not use
`CI_JOB_TOKEN`, a personal token, or the GitLab API.

C11 does not automate mirroring from GHCR to an internal registry. The
internal image must be equivalent to the official release image and must be
addressed by its own immutable digest.

## Scope And Limitations

C11 deliberately provides a copyable GitLab job, not a GitLab distribution
service. It does not provide reusable CI/CD Components, Catalog entries,
automatic MR comments or discussions, GitLab API annotations, or GitLab Fix
integration. The job also does not configure shared GitLab caches or install
itself on a Self-Managed instance.
