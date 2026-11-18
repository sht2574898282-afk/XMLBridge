"""Check every bundled Mach-O slice, deployment target and external library path."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess


MACHO_MAGIC = {
    bytes.fromhex(value) for value in (
        "feedface", "cefaedfe", "feedfacf", "cffaedfe",
        "cafebabe", "bebafeca", "cafebabf", "bfbafeca",
    )
}


def version(value: str) -> tuple[int, int, int]:
    parts = tuple(map(int, value.split(".")))
    return (parts + (0, 0, 0))[:3]


def minimum_macos(path: Path, arch: str) -> str:
    output = subprocess.check_output(
        ["otool", "-arch", arch, "-l", str(path)], text=True,
    )
    match = re.search(r"^\s+minos\s+([\d.]+)", output, re.MULTILINE)
    if match is None:
        match = re.search(r"cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([\d.]+)", output)
    if match is None:
        raise ValueError(f"No macOS deployment target: {path}")
    return match.group(1)


def runtime_minimum(arch: str) -> str:
    from PySide6 import QtCore
    import shiboken6

    root = Path(QtCore.__file__).parent
    binaries = [root / "Qt/lib/QtCore.framework/Versions/A/QtCore",
                Path(importlib.util.find_spec("shiboken6.Shiboken").origin)]
    for name in ("QtCore", "QtDBus", "QtGui", "QtWidgets"):
        binaries.append(Path(importlib.util.find_spec("PySide6." + name).origin))
    binaries.extend(root.glob("libpyside6*.dylib"))
    binaries.extend(Path(shiboken6.__file__).parent.glob("libshiboken6*.dylib"))
    # SwiftUI's existing application entry point uses macOS 13 APIs.
    return max(["13.0", *(minimum_macos(path, arch) for path in binaries)], key=version)


def audit(app: Path, arch: str, minimum: str) -> dict:
    app = app.resolve()
    errors = []
    binaries = []
    seen = set()
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if version(info.get("LSMinimumSystemVersion", "0")) != version(minimum):
        errors.append("Info.plist minimum system version does not match build target")
    for name in ("XMLBridge", "XMLBridgeCore"):
        if not os.access(app / "Contents/MacOS" / name, os.X_OK):
            errors.append(f"Missing executable: {name}")
    for path in sorted(app.rglob("*")):
        relative = path.relative_to(app).as_posix()
        if path.is_symlink() and not path.resolve().is_relative_to(app):
            errors.append(f"Symlink leaves application: {relative}")
            continue
        if not path.is_file() or path.resolve() in seen:
            continue
        seen.add(path.resolve())
        with path.open("rb") as stream:
            if stream.read(4) not in MACHO_MAGIC:
                continue
        try:
            slices = subprocess.check_output(["lipo", "-archs", str(path)], text=True).split()
            if arch not in slices:
                errors.append(f"Missing {arch} slice: {relative} ({', '.join(slices)})")
                continue
            required = minimum_macos(path, arch)
            if version(required) > version(minimum):
                errors.append(f"Requires macOS {required}, above {minimum}: {relative}")
            linked = subprocess.check_output(["otool", "-arch", arch, "-L", str(path)], text=True)
            for line in linked.splitlines():
                if not line.startswith("\t"):
                    continue
                dependency = line.strip().split(" (compatibility version", 1)[0]
                if dependency.startswith("/") and not dependency.startswith(("/System/Library/", "/usr/lib/")):
                    errors.append(f"External library {dependency}: {relative}")
            binaries.append({"path": relative, "architectures": slices, "minimum_macos": required})
        except (subprocess.CalledProcessError, ValueError) as exc:
            errors.append(f"Cannot audit {relative}: {exc}")
    if not binaries:
        errors.append("No Mach-O binaries found")
    return {"architecture": arch, "minimum_macos": minimum,
            "binary_count": len(binaries), "binaries": binaries,
            "errors": errors, "passed": not errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--runtime-minimum", action="store_true")
    parser.add_argument("--app", type=Path)
    parser.add_argument("--minimum-os")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runtime_minimum:
        print(runtime_minimum(args.arch))
        return 0
    if args.app is None or args.minimum_os is None:
        parser.error("--app and --minimum-os are required for bundle audit")
    report = audit(args.app, args.arch, args.minimum_os)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "binaries"}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
