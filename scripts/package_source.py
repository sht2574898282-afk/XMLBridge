# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
"""Package only reviewable XMLBridge source and exact local dependency metadata."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
import sysconfig
import zipfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from xmlbridge import __version__


ROOT = Path(__file__).resolve().parents[1]
ROOT_DEPENDENCIES = ("lxml", "PySide6", "PyInstaller", "pytest", "Pillow", "setuptools")
SOURCE_DIRECTORIES = {"xmlbridge", "tests", "scripts", "docs", "docs-intel"}
SOURCE_SUFFIXES = {".py", ".swift", ".c", ".manifest", ".dtd", ".md", ".sh", ".json", ".png", ".jpg", ".pdf", ".icns"}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".venv", "build", "dist"}


def source_files() -> dict[str, bytes]:
    files = {"pyproject.toml": (ROOT / "pyproject.toml").read_bytes(),
             "launcher.py": (ROOT / "launcher.py").read_bytes()}
    for name in ("LICENSE", "NOTICE", "README.md", "RELEASE_VERSION", "logo.png"):
        if (ROOT / name).is_file():
            files[name] = (ROOT / name).read_bytes()
    for lock in ("requirements-build.lock", "requirements-arm64.lock", "requirements-intel.lock", "requirements-windows.lock", "requirements-windows-build.lock"):
        if (ROOT / lock).is_file():
            files[lock] = (ROOT / lock).read_bytes()
    for directory in sorted(SOURCE_DIRECTORIES):
        for path in sorted((ROOT / directory).rglob("*")):
            relative = path.relative_to(ROOT)
            if (path.is_file() and not path.is_symlink()
                    and not EXCLUDED_PARTS.intersection(relative.parts)
                    and (path.suffix.lower() in SOURCE_SUFFIXES
                         or (relative.parts[0] in {"docs", "docs-intel"} and relative.parts[1:2] == ("licenses",)))):
                files[relative.as_posix()] = path.read_bytes()
    return files


def dependencies() -> dict[str, importlib.metadata.Distribution]:
    """Resolve the installed runtime/build/test closure for this Python/platform."""
    result = {}
    pending = list(ROOT_DEPENDENCIES)
    while pending:
        name = canonicalize_name(pending.pop())
        if name in result:
            continue
        distribution = importlib.metadata.distribution(name)
        result[name] = distribution
        for text in distribution.requires or []:
            requirement = Requirement(text)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return dict(sorted(result.items()))


def license_files(distributions):
    output = {}
    provenance = []
    for name, distribution in distributions.items():
        copied = []
        for relative in sorted(distribution.files or [], key=str):
            parts = tuple(str(p).lower() for p in relative.parts)
            filename = relative.name.lower()
            if not ("licenses" in parts or filename.startswith(("license", "copying", "notice"))):
                continue
            path = Path(distribution.locate_file(relative))
            if not path.is_file():
                continue
            destination = "third-party-licenses/" + name + "/" + str(relative).replace("\\", "/")
            if ".." in Path(destination).parts:
                continue
            output[destination] = path.read_bytes()
            copied.append({"source": str(relative), "archive": destination})
        provenance.append({"component": distribution.metadata["Name"], "version": distribution.version,
                           "license_expression": distribution.metadata.get("License-Expression"),
                           "license_metadata": distribution.metadata.get("License"),
                           "project_urls": distribution.metadata.get_all("Project-URL", []),
                           "copied_files": copied,
                           "status": "copied" if copied else "no local license text found; see THIRD_PARTY_NOTICES.md"})
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if python_license.is_file():
        output["third-party-licenses/CPython/LICENSE.txt"] = python_license.read_bytes()
    provenance.append({"component": "CPython", "version": platform.python_version(),
                       "source": "sysconfig.get_path('stdlib')/LICENSE.txt",
                       "status": "copied" if python_license.is_file() else "not found"})
    output["third-party-licenses/sources.json"] = json.dumps(provenance, ensure_ascii=False, indent=2).encode()
    return output


def contents():
    files = source_files()
    installed = dependencies()
    lines = [f"# Exact installed versions for this macOS {platform.machine()} / Python {platform.python_version()} build.",
             "# Runtime, packaging, icon generation, and test dependencies; no wheels included.",
             "# Versions are pinned; this is not a hash-verified offline dependency bundle."]
    lines.extend(f"{d.metadata['Name']}=={d.version}" for d in installed.values())
    files["requirements.lock"] = ("\n".join(lines) + "\n").encode()
    environment = {"python": platform.python_version(), "implementation": platform.python_implementation(),
                   "platform": platform.platform(), "machine": platform.machine(),
                   "dependencies": {d.metadata["Name"]: d.version for d in installed.values()}}
    files["build-environment.json"] = json.dumps(environment, ensure_ascii=False, indent=2).encode()
    files.update(license_files(installed))
    manifest = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items()))
    files["MANIFEST.sha256"] = manifest.encode()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT.parents[1] / "outputs",
                        help="Output directory; an existing archive is never overwritten")
    parser.add_argument("--dry-run", action="store_true", help="List archive members without writing files")
    args = parser.parse_args()
    files = contents()
    version_path = ROOT / "RELEASE_VERSION"
    version = version_path.read_text().strip() if version_path.is_file() else __version__
    basename = f"XMLBridge-source-{version}"
    destination = args.output.resolve() / (basename + ".zip")
    if args.dry_run:
        print(json.dumps({"archive": str(destination), "files": sorted(files),
                          "uncompressed_bytes": sum(map(len, files.values()))}, ensure_ascii=False, indent=2))
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental replacement of a previously reviewed delivery.
    with destination.open("xb") as stream:
        try:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for name, data in sorted(files.items()):
                    archive.writestr(f"{basename}/{name}", data)
        except BaseException:
            stream.close()
            destination.unlink(missing_ok=True)
            raise
    print(json.dumps({"archive": str(destination), "files": len(files),
                      "bytes": destination.stat().st_size,
                      "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
