# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
"""Local media references, using the current filesystem's path conventions."""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def foreign_absolute_reference(value: str, *, windows: bool | None = None) -> bool:
    windows = os.name == "nt" if windows is None else windows
    parts = urlsplit(value)
    if parts.scheme != "file" or parts.netloc not in ("", "localhost"):
        return False
    path = unquote(parts.path)
    drive = bool(re.match(r"^/[A-Za-z]:[/\\]",path))
    return path.startswith("/") and (not drive if windows else drive)


def local_path_text(value: str, *, windows: bool | None = None) -> str | None:
    windows = os.name == "nt" if windows is None else windows
    # urlsplit treats a bare drive letter as a URL scheme.
    if windows and re.match(r"^[A-Za-z]:[\\/]", value):
        return value.replace("/", "\\")
    if windows and value.startswith(("\\\\", "//")):
        return value.replace("/", "\\")
    if foreign_absolute_reference(value,windows=windows):
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("", "file"):
        return None
    if parts.netloc not in ("", "localhost"):
        if not windows or parts.scheme != "file" or any(c in parts.netloc for c in "@:\\"):
            return None
        return "\\\\" + parts.netloc + unquote(parts.path).replace("/", "\\")
    path = unquote(parts.path)
    if windows:
        if re.match(r"^/[A-Za-z]:[/\\]", path):
            path = path[1:]
        path = path.replace("/", "\\")
    return path


def local_media_path(value: str, xml_path: Path) -> Path | None:
    text = local_path_text(value)
    if text is None:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = xml_path.parent / path
    return path.resolve()
