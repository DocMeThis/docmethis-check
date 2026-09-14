# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Build and assemble selected public source-access distributions."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Final

__all__ = ["main"]

INDEX_URL: Final = "https://pkg.docmethis.com"
RELEASE_SCHEMA: Final = "public-source-release-v1"
CHECK_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE: Final = CHECK_ROOT.parent
DEFAULT_OUTPUT: Final = CHECK_ROOT / "dist" / "public-release"
PUBLIC_PACKAGES: Final[tuple[tuple[str, str], ...]] = (
    ("docmethis-extract-python", "https://github.com/DocMeThis/docmethis-extract-python"),
    ("docmethis-verify", "https://github.com/DocMeThis/docmethis-verify"),
    ("docmethis-check", "https://github.com/DocMeThis/docmethis-check"),
)


@dataclasses.dataclass(frozen=True, slots=True)
class PackageBuild:
    """Describe one built public distribution."""

    name: str
    version: str
    repository: str
    source_commit: str
    wheel: Path
    sdist: Path
    owner_manifest: Path


def _sha256(path: Path) -> str:
    """Return the SHA-256 digest of one artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_identity(repository: Path) -> tuple[str, str]:
    """Read the distribution name and version from one repository."""
    pyproject = repository / "pyproject.toml"
    try:
        with pyproject.open("rb") as file_handle:
            project = tomllib.load(file_handle).get("project")
    except FileNotFoundError as exc:
        message = f"project metadata is missing: {pyproject}"
        raise RuntimeError(message) from exc
    if not isinstance(project, dict):
        message = f"project metadata is invalid: {pyproject}"
        raise TypeError(message)
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        message = f"project name/version is missing: {pyproject}"
        raise TypeError(message)
    return name, version


def _artifacts(directory: Path, name: str, version: str) -> tuple[Path, Path]:
    """Return the single wheel and sdist produced for one distribution."""
    artifacts = sorted(
        path for path in directory.iterdir() if path.is_file() and (path.name.endswith(".whl") or path.name.endswith(".tar.gz"))
    )
    wheels = [path for path in artifacts if path.name.endswith(".whl")]
    sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        message = f"expected one wheel and one sdist for {name}=={version}, found {len(wheels)} and {len(sdists)}"
        raise RuntimeError(message)
    return wheels[0], sdists[0]


def _build_package(
    *,
    uv: str,
    expected_name: str,
    repository: Path,
    repository_url: str,
    staging: Path,
) -> PackageBuild:
    """Build one source-access distribution and write its owner manifest."""
    name, version = _project_identity(repository)
    if name != expected_name:
        message = f"unexpected distribution in {repository}: expected {expected_name}, found {name}"
        raise RuntimeError(message)
    output = staging / name
    output.mkdir(parents=True)
    subprocess.run([uv, "build", "--out-dir", str(output)], cwd=repository, check=True)

    manifest_script = repository / "scripts" / "write_owner_artifact_manifest.py"
    if not manifest_script.is_file():
        message = f"owner manifest script is missing: {manifest_script}"
        raise RuntimeError(message)
    subprocess.run(
        [
            sys.executable,
            str(manifest_script),
            "--repository",
            repository_url,
            "--dist",
            str(output),
        ],
        cwd=repository,
        check=True,
    )

    wheel, sdist = _artifacts(output, name, version)
    owner_manifest = output / "r6-owner-artifact.json"
    manifest = json.loads(owner_manifest.read_text(encoding="utf-8"))
    if manifest.get("distribution") != {"name": name, "version": version}:
        message = f"owner manifest distribution mismatch: {owner_manifest}"
        raise RuntimeError(message)
    source_commit = manifest.get("source_commit")
    if not isinstance(source_commit, str) or not source_commit:
        message = f"owner manifest source commit is missing: {owner_manifest}"
        raise RuntimeError(message)
    return PackageBuild(name, version, repository_url, source_commit, wheel, sdist, owner_manifest)


def _ensure_empty_output(output: Path) -> Path:
    """Reject accidental mixing with an existing release directory."""
    output = output.expanduser().resolve()
    if output.exists():
        if not output.is_dir():
            message = f"release output is not a directory: {output}"
            raise RuntimeError(message)
        if any(output.iterdir()):
            message = f"release output must be empty: {output}"
            raise RuntimeError(message)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _artifact_entry(path: Path, kind: str) -> dict[str, str]:
    """Return the public manifest entry for one artifact."""
    return {"filename": path.name, "kind": kind, "sha256": _sha256(path)}


def _assemble(output: Path, builds: list[PackageBuild]) -> None:
    """Copy public artifacts and provenance into one release directory."""
    packages = output / "packages"
    packages.mkdir(parents=True, exist_ok=True)

    source_access: list[dict[str, object]] = []
    package_artifacts: list[dict[str, str]] = []
    for build in builds:
        manifest_relative = Path("provenance/source") / build.name / build.owner_manifest.name
        destination_manifest = output / manifest_relative
        destination_manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build.owner_manifest, destination_manifest)

        artifacts = (
            _artifact_entry(build.wheel, "wheel"),
            _artifact_entry(build.sdist, "sdist"),
        )
        for artifact in (build.wheel, build.sdist):
            shutil.copy2(artifact, packages / artifact.name)
        source_access.append(
            {
                "distribution": build.name,
                "version": build.version,
                "repository": build.repository,
                "source_commit": build.source_commit,
                "manifest": manifest_relative.as_posix(),
                "artifacts": list(artifacts),
            }
        )
        package_artifacts.extend(artifacts)

    release_manifest = {
        "schema_version": RELEASE_SCHEMA,
        "index": INDEX_URL,
        "source_access": source_access,
        "packages": sorted(package_artifacts, key=lambda artifact: artifact["filename"]),
    }
    (output / "public-source-release-manifest.json").write_text(
        json.dumps(release_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _ensure_matching_versions(builds: list[PackageBuild]) -> None:
    """Require one coordinated version across the public distributions."""
    versions = {build.version for build in builds}
    if len(versions) != 1:
        message = f"public distributions have mismatched versions: {sorted(versions)}"
        raise RuntimeError(message)


def main(argv: list[str] | None = None) -> int:
    """Build and assemble the public source-access release."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "package",
        nargs="?",
        choices=tuple(directory for directory, _repository_url in PUBLIC_PACKAGES),
        help="Public package to rebuild; all three are built by default",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=DEFAULT_WORKSPACE,
        help="Directory containing the three package repositories",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Empty directory receiving the assembled release",
    )
    args = parser.parse_args(argv)

    uv = shutil.which("uv")
    if uv is None:
        message = "uv is required to build the public distributions"
        raise RuntimeError(message)
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        message = f"workspace directory is missing: {workspace}"
        raise RuntimeError(message)
    output = _ensure_empty_output(args.output)

    with tempfile.TemporaryDirectory(prefix="docmethis-public-build-") as temporary_directory:
        staging = Path(temporary_directory)
        package_specs = (
            PUBLIC_PACKAGES
            if args.package is None
            else tuple(package for package in PUBLIC_PACKAGES if package[0] == args.package)
        )
        builds = [
            _build_package(
                uv=uv,
                expected_name=directory,
                repository=workspace / directory,
                repository_url=repository_url,
                staging=staging,
            )
            for directory, repository_url in package_specs
        ]
        _ensure_matching_versions(builds)
        _assemble(output, builds)

    print(f"Built public source release: {output}")
    for build in builds:
        print(f"  {build.name}=={build.version} ({build.source_commit})")
    print(f"  artifacts: {output / 'packages'}")
    print(f"  manifest: {output / 'public-source-release-manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
