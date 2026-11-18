"""One local JSON request per engine process; no server or network transport."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .core import Cancelled, convert, effective_decisions, export_blockers, inspect, plan_conversion
from .models import ConversionResult, Issue, Target, json_safe


class FileCancellation:
    def __init__(self, path=None):
        self.path = Path(path) if path else None

    def is_set(self):
        return self.path is not None and self.path.exists()


def _target(request):
    return Target(**request.get("target", {}))


def describe(path, request):
    doc = inspect(path)
    plan = plan_conversion(doc, _target(request), request.get("project_ids"),
                           allow_risky=request.get("allow_risky", False))
    choices = effective_decisions(plan.issues, request.get("decisions"), request.get("allow_risky", False))
    blockers = export_blockers(plan.issues, choices, allow_risky=request.get("allow_risky", False),
                               same_family=doc.family == plan.target.family)
    return {
        "id": str(doc.path), "path": str(doc.path), "name": doc.path.stem,
        "family": doc.family, "version": doc.version, "input_hash": doc.input_hash,
        "projects": json_safe(doc.projects), "media_count": len(doc.media),
        "offline_count": sum(m.exists is False for m in doc.media),
        "issues": json_safe(plan.issues), "steps": plan.steps,
        "blocking_count": len(blockers), "readable": doc.tree is not None,
        "media": json_safe(doc.media), "decisions": choices,
    }


def handle(request):
    cancel = FileCancellation(request.get("cancel_path"))
    if cancel.is_set():
        return {"cancelled": True}
    action = request.get("action")
    if isinstance(action, str) and action.startswith("proxy_"):
        from . import proxy_bridge
        try:
            if action == "proxy_scan":
                return {"proxy_plan": proxy_bridge.scan_plan(request, cancel)}
            if action == "proxy_choose":
                return {"proxy_row": proxy_bridge.choose_source(request, cancel)}
            if action == "proxy_export":
                return {"result": proxy_bridge.export_plan(request, cancel)}
        except Cancelled:
            return {"cancelled": True}
        raise ValueError("未知的代理回套操作")
    if action == "scan":
        # Reuse the established deduplication/package boundary rules.
        from .gui import collect_candidates
        paths = collect_candidates(request.get("paths", []), cancel)
        entries = []
        for path in paths:
            if cancel.is_set():
                break
            entries.append(describe(path, request))
        return {"entries": entries, "cancelled": cancel.is_set(),
                "error": "没有找到 .xml、.fcpxml 文件或 .fcpxmld 目录包" if not paths and not cancel.is_set() else None}
    if action == "plan":
        return {"entry": describe(request["path"], request)}
    if action == "convert":
        doc = inspect(request["path"])
        expected = request.get("input_hash")
        if expected and expected != doc.input_hash:
            return {"result": json_safe(ConversionResult(
                "blocked", message="源文件已改变，请重新分析后再导出",
                issues=[Issue("SOURCE_CHANGED", "源文件在分析后已改变", "error")],
                checks={"structure": "not_run", "timeline": "not_run", "host": "not_run"}))}
        risky = request.get("allow_risky", False)
        plan = plan_conversion(doc, _target(request), request.get("project_ids"), allow_risky=risky)
        result = convert(plan, request.get("decisions", {}), request["output_dir"], cancel,
                         allow_risky=risky)
        return {"result": json_safe(result), "cancelled": result.status == "cancelled"}
    raise ValueError("未知的本地操作")


def main(path):
    try:
        request_path = Path(path)
        if request_path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("本地请求过大")
        response = handle(json.loads(request_path.read_text(encoding="utf-8")))
    except Exception as exc:
        response = {"error": str(exc)}
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    sys.stdout.flush()
    return 0 if not response.get("error") else 2
