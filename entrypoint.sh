#!/bin/sh
set -eu

project_path="."
fail_on_warning=""
include_visibility=""
profile=""
base_ref=""
on_nonlinear_push_without_base=""
check_mode=""
annotation_placement=""
on_missing_base=""
exclude_paths=""
dia_exclude_paths=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --project-path)
      project_path="${2:?--project-path requires a value}"
      shift 2
      ;;
    --fail-on-warning)
      fail_on_warning="${2:-}"
      shift 2
      ;;
    --include-visibility)
      include_visibility="${2:-}"
      shift 2
      ;;
    --profile)
      profile="${2:-}"
      shift 2
      ;;
    --base-ref)
      base_ref="${2:-}"
      shift 2
      ;;
    --on-nonlinear-push-without-base)
      on_nonlinear_push_without_base="${2:-}"
      shift 2
      ;;
    --check-mode)
      check_mode="${2:-}"
      shift 2
      ;;
    --annotation-placement)
      annotation_placement="${2:-}"
      shift 2
      ;;
    --on-missing-base)
      on_missing_base="${2:-}"
      shift 2
      ;;
    --exclude-paths)
      exclude_paths="${2:-}"
      shift 2
      ;;
    --dia-exclude-paths)
      dia_exclude_paths="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "$project_path" in
  /*) ;;
  *)
    if [ -n "${GITHUB_WORKSPACE:-}" ]; then
      project_path="$GITHUB_WORKSPACE/$project_path"
    fi
    ;;
esac

annotations_file=$(mktemp)
trap 'rm -f "$annotations_file"' 0
set -- "$project_path" --github-output-file "$annotations_file"

if [ "$fail_on_warning" = "true" ]; then
  set -- "$@" --fail-on-warning
elif [ "$fail_on_warning" = "false" ]; then
  set -- "$@" --no-fail-on-warning
fi

if [ -n "$include_visibility" ]; then
  set -- "$@" --include-visibility "$include_visibility"
fi

if [ -n "$profile" ]; then
  set -- "$@" --profile "$profile"
fi

if [ -n "$base_ref" ]; then
  set -- "$@" --base-ref "$base_ref"
fi

if [ -n "$on_nonlinear_push_without_base" ]; then
  set -- "$@" --on-nonlinear-push-without-base "$on_nonlinear_push_without_base"
fi

if [ -n "$check_mode" ]; then
  set -- "$@" --check-mode "$check_mode"
fi

if [ -n "$annotation_placement" ]; then
  set -- "$@" --annotation-placement "$annotation_placement"
fi

if [ -n "$on_missing_base" ]; then
  set -- "$@" --on-missing-base "$on_missing_base"
fi

if [ -n "$exclude_paths" ]; then
  set -- "$@" --exclude-paths "$exclude_paths"
fi

if [ -n "$dia_exclude_paths" ]; then
  set -- "$@" --dia-exclude-paths "$dia_exclude_paths"
fi

if [ -n "${GITHUB_WORKSPACE:-}" ]; then
  git config --global safe.directory "$GITHUB_WORKSPACE"
fi

# Call the check and capture exit code (set -e must not skip chown)
exit_code=0
python -m docmethis_check "$@" || exit_code=$?

if [ -s "$annotations_file" ]; then
  cat "$annotations_file"
fi

# Fix cache ownership if running in Docker with a mapped workspace
if [ -n "${GITHUB_WORKSPACE:-}" ] && [ -f "$project_path/.docmethis_cache.json" ]; then
  workspace_uid=$(stat -c "%u" "$GITHUB_WORKSPACE" 2>/dev/null || true)
  if [ -n "$workspace_uid" ] && [ "$workspace_uid" -ne 0 ]; then
    chown "$workspace_uid" "$project_path/.docmethis_cache.json" 2>/dev/null || true
  fi
fi

exit "$exit_code"
