from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .core import convert, inspect, plan_conversion
from .models import Target, json_safe


def self_test():
    source = b'''<?xml version="1.0" encoding="UTF-8"?><fcpxml version="1.8"><resources><format id="r1" frameDuration="1/25s" width="1920" height="1080"/><asset id="r2" src="file:///XMLBridge-offline-test.mov" start="0s" duration="5s" hasVideo="1" format="r1"/></resources><library><event name="Test"><project name="Self test"><sequence format="r1" duration="2s" tcStart="0s" tcFormat="NDF"><spine><asset-clip ref="r2" name="Sample" offset="0s" start="1s" duration="2s"/></spine></sequence></project></event></library></fcpxml>'''
    with tempfile.TemporaryDirectory(prefix="xmlbridge-selftest-") as directory:
        root = Path(directory)
        path = root / "test.fcpxml"
        path.write_bytes(source)
        document = inspect(path)
        report = convert(plan_conversion(document, Target("fcpxml", "1.9")), {}, root / "out")
        if report.status != "success":
            raise RuntimeError(json.dumps(json_safe(report), ensure_ascii=False))
        output = inspect(report.output_path)
        if output.version != "1.9" or any(i.severity == "error" for i in output.issues):
            raise RuntimeError("Packaged schema or migration self-test failed")
        # Import the actual GUI dependencies to verify frozen Qt availability.
        from PySide6 import QtCore, QtGui, QtWidgets
        return {"self_test": "passed", "migration": "1.8 → 1.9", "Qt": QtCore.qVersion(), "schema": "passed"}


def main(argv=None):
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        from .gui import main as gui_main
        return gui_main()
    if len(args_list) == 2 and args_list[0] == "--native-request":
        from .native_bridge import main as bridge_main
        return bridge_main(args_list[1])
    parser = argparse.ArgumentParser(description="XMLBridge — editorial XML migration and interchange")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--inspect", metavar="FILE")
    parser.add_argument("--convert", metavar="FILE")
    parser.add_argument("--family", choices=("fcpxml", "xmeml"), default="fcpxml")
    parser.add_argument("--version", default="1.9")
    parser.add_argument("--application", default="通用")
    parser.add_argument("--output", type=Path, default=Path.cwd() / "XMLBridge Output")
    parser.add_argument("--project", action="append", default=None)
    parser.add_argument("--decision", action="append", default=[], metavar="ISSUE_ID=ACTION")
    parser.add_argument("--allow-risky", action="store_true", help="同格式转换允许带问题输出，并记录未通过检查")
    args = parser.parse_args(args_list)
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False))
        return 0
    if not (args.inspect or args.convert):
        parser.error("Specify --inspect or --convert")
    document = inspect(args.inspect or args.convert)
    if args.inspect:
        data = {"source": str(document.path), "family": document.family, "version": document.version,
                "projects": json_safe(document.projects), "media": json_safe(document.media), "issues": json_safe(document.issues)}
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 2 if any(i.severity == "error" for i in document.issues) else 0
    decisions = {}
    for value in args.decision:
        key, sep, action = value.partition("=")
        if not sep:
            parser.error("--decision must be ISSUE_ID=ACTION")
        decisions[key] = action
    result = convert(plan_conversion(document, Target(args.family, args.version, args.application), args.project,
                                     allow_risky=args.allow_risky), decisions, args.output, allow_risky=args.allow_risky)
    print(json.dumps(json_safe(result), ensure_ascii=False, indent=2))
    return 0 if result.status in {"success", "exported_with_issues"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

