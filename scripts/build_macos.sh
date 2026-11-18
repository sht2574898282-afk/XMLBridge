#!/bin/zsh
set -euo pipefail
task_root="${0:A:h:h}"
cd "$task_root"
task_dest="${1:-$task_root/dist}"
task_arch="${XMLBRIDGE_BUILD_ARCH:-arm64}"
case "$task_arch" in
  arm64) task_env=.venv; task_docs_default=docs ;;
  x86_64) task_env=.venv-intel; task_docs_default=docs-intel ;;
  *) print -u2 "Unsupported architecture: $task_arch"; exit 2 ;;
esac
task_python="${XMLBRIDGE_BUILD_PYTHON:-$task_root/$task_env/bin/python}"
task_release_version="${XMLBRIDGE_RELEASE_VERSION:-$(cat "$task_root/RELEASE_VERSION")}"
task_ffprobe="${XMLBRIDGE_FFPROBE:-$task_root/vendor/ffprobe/$task_arch/ffprobe}"
if [[ ! -x "$task_ffprobe" ]]; then
  print -u2 "Missing ffprobe: set XMLBRIDGE_FFPROBE or run scripts/build_ffprobe_macos.sh."
  exit 2
fi
lipo -verify_arch "$task_arch" "$task_ffprobe"
task_docs="${XMLBRIDGE_BUILD_DOCS:-$task_root/$task_docs_default}"
# Bundle the project's own license as well as the independent upstream notices.
cp "$task_root/LICENSE" "$task_docs/XMLBridge_LICENSE.txt"
cp "$task_root/README.md" "$task_docs/XMLBridge_README.md"
if [[ -f "$task_docs/XMLBridge-Manual-$task_release_version-zh-CN.pdf" ]]; then
  cp "$task_docs/XMLBridge-Manual-$task_release_version-zh-CN.pdf" "$task_docs/XMLBridge_使用说明书.pdf"
fi
task_runtime_arch="$("$task_python" -c 'import platform; print(platform.machine())')"
if [[ "$task_runtime_arch" != "$task_arch" ]]; then
  print -u2 "Build Python is $task_runtime_arch; use a $task_arch Python environment."
  exit 2
fi
"$task_python" scripts/audit_distribution_privacy.py --build-environment
task_minimum="${XMLBRIDGE_MACOS_MIN:-$("$task_python" scripts/audit_macos_bundle.py --arch "$task_arch" --runtime-minimum)}"
task_build="$task_root/build/$task_arch"
mkdir -p "$task_dest"
"$task_python" scripts/make_icon.py
mkdir -p "$task_build"
xcrun swiftc scripts/native/XMLBridge.swift scripts/native/ProxyRelink.swift -parse-as-library -O \
  -target "$task_arch-apple-macos$task_minimum" -framework SwiftUI -framework AppKit -o "$task_build/XMLBridgeNative"
"$task_python" -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name XMLBridge --osx-bundle-identifier studio.xmlbridge.app \
  --target-architecture "$task_arch" \
  --icon "$task_root/xmlbridge/assets/XMLBridge.icns" \
  --add-data "$task_root/xmlbridge/schemas:xmlbridge/schemas" \
  --add-data "$task_root/xmlbridge/assets:xmlbridge/assets" \
  --add-data "$task_docs:docs" \
  --add-binary "$task_ffprobe:." \
  --additional-hooks-dir "$task_root/scripts/hooks" \
  --distpath "$task_dest" --workpath "$task_build/pyinstaller" --specpath "$task_build" \
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets \
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick \
  --exclude-module PySide6.QtQuickWidgets --exclude-module PySide6.QtPdf \
  --exclude-module PySide6.QtPdfWidgets --exclude-module PySide6.QtSvg \
  --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtOpenGLWidgets \
  --exclude-module tkinter --exclude-module pytest "$task_root/launcher.py"
"$task_python" - "$task_dest/XMLBridge.app" "$task_build/XMLBridgeNative" "$task_minimum" "$task_release_version" <<'PY'
from pathlib import Path
import plistlib
import sys
import shutil
from xmlbridge import __version__

app = Path(sys.argv[1])
plist_path = app / "Contents" / "Info.plist"
with plist_path.open("rb") as stream:
    info = plistlib.load(stream)
info["CFBundleShortVersionString"] = sys.argv[4]
info["CFBundleVersion"] = info["CFBundleShortVersionString"]
info["NSHumanReadableCopyright"] = "Copyright © 2026 迪儿 Dear · https://github.com/sht2574898282-afk"
info["LSMinimumSystemVersion"] = sys.argv[3]
info["NSHighResolutionCapable"] = True
info["CFBundleDevelopmentRegion"] = "zh-Hans"
info["CFBundleLocalizations"] = ["zh-Hans", "en"]
info["CFBundleDocumentTypes"] = [{
    "CFBundleTypeName": "剪辑 XML", "CFBundleTypeRole": "Editor", "LSHandlerRank": "Alternate",
    "CFBundleTypeExtensions": ["xml", "fcpxml", "fcpxmld"],
    "LSItemContentTypes": ["public.xml", "studio.xmlbridge.fcpxml", "studio.xmlbridge.fcpxmld"],
}]
info["UTExportedTypeDeclarations"] = [
    {"UTTypeIdentifier": "studio.xmlbridge.fcpxml", "UTTypeDescription": "Final Cut Pro XML",
     "UTTypeConformsTo": ["public.xml"], "UTTypeTagSpecification": {"public.filename-extension": ["fcpxml"]}},
    {"UTTypeIdentifier": "studio.xmlbridge.fcpxmld", "UTTypeDescription": "Final Cut Pro XML 目录包",
     "UTTypeConformsTo": ["com.apple.package", "public.directory"],
     "UTTypeTagSpecification": {"public.filename-extension": ["fcpxmld"]}},
]
launcher = app / "Contents/MacOS/XMLBridge"
launcher.rename(app / "Contents/MacOS/XMLBridgeCore")
shutil.copy2(sys.argv[2], launcher)
with plist_path.open("wb") as stream:
    plistlib.dump(info, stream)
resources = app / "Contents" / "Resources"
for relative in (
    "docs/USAGE.zh-CN.md", "docs/CAPABILITIES.zh-CN.md", "docs/THIRD_PARTY_NOTICES.md",
    "docs/licenses/QtForPython/LICENSES/LGPL-3.0-only.txt",
    "docs/licenses/QtForPython/LICENSES/GPL-3.0-only.txt",
    "docs/licenses/QtForPython/LICENSES/Qt-GPL-exception-1.0.txt",
    "docs/licenses/installed/CPython/LICENSE.txt",
    "docs/licenses/installed/lxml/lxml-6.1.3.dist-info/licenses/LICENSES.txt",
):
    if not (resources / relative).is_file():
        raise SystemExit(f"Missing bundled documentation/license: {relative}")
# These modules must not reappear via optional QtGui plugins.
unwanted = ("QtQml", "QtQuick", "QtPdf", "QtVirtualKeyboard")
frameworks = app / "Contents" / "Frameworks"
found = [str(path.relative_to(app)) for path in frameworks.rglob("*")
         if (path.name.startswith(unwanted)
             and (path.name.endswith((".framework", ".so", ".dylib")) or path.parent == frameworks))
         or path.name in {"libqpdf.dylib", "libqtvirtualkeyboardplugin.dylib"}]
if found:
    raise SystemExit("Unused Qt components were collected: " + ", ".join(found[:12]))
print("Bundled documentation/licenses and Widgets-only Qt selection: passed")
PY
"$task_python" scripts/audit_macos_bundle.py --app "$task_dest/XMLBridge.app" \
  --arch "$task_arch" --minimum-os "$task_minimum" --output "$task_build/bundle-audit.json"
"$task_python" scripts/sanitize_frozen_build_paths.py "$task_dest/XMLBridge.app/Contents/MacOS/XMLBridgeCore"
codesign --force --deep --sign - "$task_dest/XMLBridge.app"
codesign --verify --deep --strict "$task_dest/XMLBridge.app"
"$task_dest/XMLBridge.app/Contents/MacOS/XMLBridge" --self-test
"$task_python" scripts/audit_distribution_privacy.py "$task_dest/XMLBridge.app" \
  --require-frozen-python --output "$task_build/privacy-audit.json"
task_version="$task_release_version"
task_archive="$task_dest/XMLBridge-$task_version-macOS-$task_arch.zip"
task_pending="$(mktemp -d "$task_dest/.privacy-package.XXXXXX")"
trap 'rm -rf "$task_pending"' EXIT
ditto -c -k --norsrc --noextattr --noqtn --noacl --keepParent \
  "$task_dest/XMLBridge.app" "$task_pending/archive.zip"
"$task_python" scripts/audit_distribution_privacy.py "$task_pending/archive.zip" \
  --require-frozen-python --output "$task_build/distribution-privacy-audit.json"
mv "$task_pending/archive.zip" "$task_archive"
print "Privacy-checked distribution: $task_archive"
