# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
"""Portable Windows entry point; diagnostics stay on the user's own computer."""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args)==2 and args[0]=="--self-test-json":
            from .__main__ import self_test
            path = Path(args[1])
            path.unlink(missing_ok=True)
            result = self_test()
            from PySide6.QtWidgets import QApplication
            from .windows_gui import WindowsWindow
            app = QApplication.instance() or QApplication(["XMLBridge self-test"])
            app.setStyle("Fusion")
            window = WindowsWindow()
            window.winId()
            app.processEvents()
            result.update({"gui_construction":"passed","qt_platform":app.platformName(),"operating_system":sys.platform,"target_nle_import":"not_run"})
            window.close()
            path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            return 0
        from .windows_gui import main as gui_main
        return gui_main(args)
    except Exception:
        directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "XMLBridge" / "Logs"
        directory.mkdir(parents=True,exist_ok=True)
        log = directory / "startup-error.log"
        log.write_text(traceback.format_exc(),encoding="utf-8")
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "XMLBridge 启动未完成。\n错误记录保存在：\n"+str(log), "XMLBridge", 0x10)
        else:
            raise
        return 1


if __name__=="__main__":
    raise SystemExit(main())
