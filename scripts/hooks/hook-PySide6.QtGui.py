# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
"""Keep the QtGui plugins used by this native Widgets application.

PyInstaller 6.22.3's QtGui module table collects every installed plugin in
imageformats/platforminputcontexts/iconengines. The PDF image plugin and virtual
keyboard plugin then pull in QtPdf and QtQuick/Qml. Filter before the subsequent
binary dependency scan, rather than deleting linked frameworks from a built app.
"""
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

# PNG is built into QtGui. Cocoa supplies native windows, dialogs, drag-and-drop,
# input methods, and accessibility; offscreen/minimal support local GUI checks.
# Retain standard icon/JPEG readers without the optional PDF/SVG/WebP stack.
PLUGIN_NAMES = {
    "libqcocoa.dylib", "libqoffscreen.dylib", "libqminimal.dylib",
    "libqicns.dylib", "libqico.dylib", "libqjpeg.dylib", "libqgif.dylib",
}
binaries = [(source, destination) for source, destination in binaries
            if "/plugins/" not in destination.replace("\\", "/")
            or Path(source).name in PLUGIN_NAMES]
