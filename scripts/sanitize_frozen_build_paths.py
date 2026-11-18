"""Remove macOS temporary build identifiers from frozen sysconfig metadata.

Only the compressed sysconfig module is replaced, without moving any archive
member. Python code is never unmarshaled or executed. Re-sign after this step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import marshal
from pathlib import Path
import re
import struct
import zlib

from PyInstaller.archive.readers import CArchiveReader


TEMP_PREFIX = re.compile(rb"/(?:private/)?var/folders/[A-Za-z0-9_-]+/[A-Za-z0-9_-]{15,}/")


def sanitize_module(raw: bytes) -> tuple[bytes, int]:
    def neutral(match):
        prefix = b"/opt/XMLBridgeBuild/"
        padding = len(match[0]) - len(prefix) - 1
        if padding < 0:
            raise ValueError("Temporary build prefix is too short to neutralize in place")
        return prefix + b"_" * padding + b"/"
    return TEMP_PREFIX.subn(neutral, raw)


def sanitize(path: Path) -> dict:
    original = path.read_bytes()
    archive = CArchiveReader(str(path))
    start, length, _, compressed, kind = archive.toc["PYZ.pyz"]
    if compressed or kind != "z":
        raise ValueError("Expected an uncompressed outer PYZ member")
    pyz = archive.extract("PYZ.pyz")
    if pyz[:4] != b"PYZ\0":
        raise ValueError("Invalid PYZ header")
    toc_offset = struct.unpack("!i", pyz[8:12])[0]
    toc = dict(marshal.loads(pyz[toc_offset:]))
    replacement = bytearray(pyz)
    changed = []
    for name, (typecode, offset, size) in toc.items():
        if not name.startswith("_sysconfigdata"):
            continue
        raw = zlib.decompress(pyz[offset:offset + size])
        clean, count = sanitize_module(raw)
        if not count:
            continue
        if len(clean) != len(raw):
            raise ValueError("Cannot change marshaled string lengths")
        block = zlib.compress(clean, 9)
        if len(block) > size:
            raise ValueError("Clean metadata exceeds the existing archive slot")
        replacement[offset:offset + size] = block + b"\0" * (size - len(block))
        changed.append({"module": name, "replacements": count})
    # Verify that every other module remains byte-for-byte identical.
    for name, (typecode, offset, size) in toc.items():
        if not any(item["module"] == name for item in changed):
            if replacement[offset:offset + size] != pyz[offset:offset + size]:
                raise ValueError("Unrelated frozen module changed")
        elif TEMP_PREFIX.search(zlib.decompress(replacement[offset:offset + size])):
            raise ValueError("Temporary build metadata remains")
    absolute = archive._start_offset + start
    if original[absolute:absolute + length] != pyz or len(replacement) != length:
        raise ValueError("Archive offset or length mismatch")
    result = original[:absolute] + replacement + original[absolute + length:]
    if len(result) != len(original):
        raise ValueError("Executable size changed")
    if changed:
        path.write_bytes(result)
    return {"changed_modules": changed, "other_modules_unchanged": True,
            "executable_size_unchanged": True,
            "sha256": hashlib.sha256(result).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    print(json.dumps(sanitize(args.executable), ensure_ascii=False))


if __name__ == "__main__":
    main()
