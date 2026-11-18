# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
"""Reject personal home paths in final bundles, including frozen Python archives.

This is a build-time tool: archive contents are inspected, never executed.
PyInstaller readers come from the pinned build environment.
"""
from __future__ import annotations

import argparse
from collections import Counter
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import sysconfig
import tempfile
import zipfile


HOME_PATH = re.compile(rb"/(?:Users|home)/([^/\\\x00\s\"'<>]+)")
MAC_TEMP_PATH = re.compile(rb"/(?:private/)?var/folders/[A-Za-z0-9_-]+/[A-Za-z0-9_-]{15,}/")
UPSTREAM_USERS = {b"Shared", b"qt", b"runner", b"Barney", b"Guest", b"root"}
CARCHIVE_MAGIC = b"MEI\014\013\012\013\016"
MAX_DEPTH = 12


class PrivacyAudit:
    def __init__(self):
        self.counts = Counter()
        self.findings = []
        self.errors = []

    def check_bytes(self, data: bytes, location: str):
        self.counts["byte_streams"] += 1
        if MAC_TEMP_PATH.search(data):
            self.findings.append({"location": location, "kind": "mac_temporary_build_path"})
        for match in HOME_PATH.finditer(data):
            if match.group(1) not in UPSTREAM_USERS:
                # Diagnostics identify the member without repeating personal data.
                self.findings.append({"location": location, "kind": "personal_home_path"})
        home = os.fsencode(Path.home())
        for encoding in ("utf-16-le", "utf-16-be"):
            if os.fsdecode(home).encode(encoding) in data:
                self.findings.append({"location": location, "kind": "encoded_personal_home_path"})

    def scan_blob(self, data: bytes, location: str, depth: int = 0):
        self.check_bytes(data, location)
        if depth > MAX_DEPTH:
            self.errors.append({"location": location, "kind": "archive_nesting_limit"})
            return
        try:
            if (data.startswith((b"PK\x03\x04", b"PK\x05\x06"))
                    or zipfile.is_zipfile(BytesIO(data))):
                self.counts["zip_archives"] += 1
                with zipfile.ZipFile(BytesIO(data)) as archive:
                    for entry in archive.infolist():
                        self.check_bytes(entry.filename.encode(), location + "!member-name")
                        if entry.is_dir():
                            continue
                        member = location + "!" + entry.filename
                        raw = archive.read(entry)
                        if stat.S_ISLNK(entry.external_attr >> 16):
                            self.counts["symlinks"] += 1
                            self.check_bytes(raw, member)
                        else:
                            self.scan_blob(raw, member, depth + 1)
            elif data.startswith(b"PYZ\0") and CARCHIVE_MAGIC not in data:
                from PyInstaller.archive.readers import ZlibArchiveReader

                self.counts["pyz_archives"] += 1
                with tempfile.TemporaryDirectory(prefix="xmlbridge-privacy-") as directory:
                    path = Path(directory) / "modules.pyz"
                    path.write_bytes(data)
                    archive = ZlibArchiveReader(str(path), check_pymagic=True)
                    for name in archive.toc:
                        self.check_bytes(name.encode(), location + "!module-name")
                        raw = archive.extract(name, raw=True)
                        if raw is not None:
                            self.counts["pyz_modules"] += 1
                            self.scan_blob(raw, location + "!" + name, depth + 1)
            elif CARCHIVE_MAGIC in data:
                from PyInstaller.archive.readers import CArchiveReader

                self.counts["carchives"] += 1
                with tempfile.TemporaryDirectory(prefix="xmlbridge-privacy-") as directory:
                    path = Path(directory) / "frozen-core"
                    path.write_bytes(data)
                    archive = CArchiveReader(str(path))
                    for name in archive.toc:
                        self.check_bytes(name.encode(), location + "!member-name")
                        self.counts["carchive_members"] += 1
                        self.scan_blob(archive.extract(name), location + "!" + name, depth + 1)
        except Exception as exc:
            # A recognized archive which could not be read cannot pass this gate.
            self.errors.append({"location": location, "kind": type(exc).__name__})

    def scan_path(self, path: Path):
        if path.is_symlink():
            self.counts["symlinks"] += 1
            self.check_bytes(os.fsencode(os.readlink(path)), path.name)
        elif path.is_file():
            self.scan_blob(path.read_bytes(), path.name)
        elif path.is_dir():
            for item in sorted(path.rglob("*")):
                label = item.relative_to(path).as_posix()
                self.check_bytes(label.encode(), label + "!name")
                if item.is_symlink():
                    self.counts["symlinks"] += 1
                    self.check_bytes(os.fsencode(os.readlink(item)), label)
                elif item.is_file():
                    self.scan_blob(item.read_bytes(), label)
        else:
            self.errors.append({"location": path.name, "kind": "missing_input"})

    def report(self, require_frozen_python: bool = False):
        if require_frozen_python:
            for kind in ("carchives", "pyz_archives", "pyz_modules", "zip_archives"):
                if not self.counts[kind]:
                    self.errors.append({"location": "bundle", "kind": "missing_coverage_" + kind})
        return {"passed": not self.findings and not self.errors,
                "coverage": dict(self.counts), "findings": self.findings, "errors": self.errors}


def check_build_environment():
    import importlib

    audit = PrivacyAudit()
    # Do not inspect runtime-computed userbase: it is not the module frozen by PyInstaller.
    name = sysconfig._get_sysconfigdata_name()
    values = importlib.import_module(name).build_time_vars
    for key, value in values.items():
        audit.check_bytes(str(value).encode(), "build-config:" + key)
    return audit.report()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", type=Path)
    parser.add_argument("--build-environment", action="store_true")
    parser.add_argument("--require-frozen-python", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.build_environment:
        report = check_build_environment()
    elif args.target:
        audit = PrivacyAudit()
        audit.scan_path(args.target)
        report = audit.report(args.require_frozen_python)
    else:
        parser.error("a target or --build-environment is required")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
