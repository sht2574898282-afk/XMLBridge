"""Proxy reconform actions shared by the native window and the Qt workbench."""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
from pathlib import Path

from .core import check_cancel
from . import proxy_relink as engine


def candidate_info(path, metadata=None):
    try:
        metadata = metadata or engine.probe(path)
        fps = Fraction(metadata["fps"])
        duration = float(Fraction(metadata["frames"], 1) / fps) if metadata.get("frames") is not None else None
        return {"path": str(path), "name": Path(path).name,
                "fps": metadata.get("fps", ""), "frames": metadata.get("frames"),
                "duration": f"{duration:.3f} 秒" if duration is not None else "时长未知",
                "timecode": metadata.get("timecode") or "时间码未知",
                "size": f"{metadata.get('width', '?')} × {metadata.get('height', '?')}",
                "error": ""}
    except Exception as exc:
        return {"path": str(path), "name": Path(path).name, "fps": "", "frames": None,
                "duration": "", "timecode": "", "size": "", "error": str(exc)}


def row_info(match, cache=None):
    cache = cache if cache is not None else {}
    source = match.source
    paths = list(match.candidates)
    if match.target and match.target not in paths:
        paths.append(match.target)
    candidates = []
    for path in paths:
        if path not in cache:
            cache[path] = candidate_info(path, match.metadata if path == match.target else None)
        candidates.append(cache[path])
    return {"id": source.uri, "source_name": source.name, "source_path": source.uri,
            "source_fps": str(source.fps) if source.fps else "未知", "source_frames": source.frames,
            "source_timecode": source.tc_label or "未知", "status": match.status,
            "status_label": engine.LABELS[match.status], "adopted": match.scope_confirmed,
            "exportable": match.scope_confirmed and match.status in engine.EXPORTABLE,
            "target_path": match.target or "", "target_name": Path(match.target).name if match.target else "",
            "target_stat": list(match.target_stat) if match.target_stat else None,
            "reasons": match.reasons, "rule": match.suffix or "", "candidates": candidates}


def checked_document(request):
    doc = engine.Document(request["path"])
    if request.get("input_hash") and request["input_hash"] != doc.hash:
        raise ValueError("XML 在分析后已改变，请重新扫描")
    return doc


def scan_plan(request, cancel):
    doc = checked_document(request)
    check_cancel(cancel)
    matches = engine.scan(doc, request["media_root"], request.get("suffixes", ["S03"]),
                          progress=lambda *_: check_cancel(cancel))
    cache = {}
    rows = []
    for match in matches:
        check_cancel(cancel)
        if match.status != "skipped":
            rows.append(row_info(match, cache))
    return {"path": str(doc.path), "input_hash": doc.hash, "media_root": request["media_root"],
            "suffixes": request.get("suffixes", ["S03"]), "rows": rows,
            "counts": dict(Counter(m.status for m in matches)), "summary": doc.summary()}


def choose_source(request, cancel):
    doc = checked_document(request)
    check_cancel(cancel)
    source = next((s for s in doc.sources if s.uri == request["source_id"]), None)
    if source is None:
        raise ValueError("XML 中没有找到所选素材，请重新扫描")
    target = request.get("target_path")
    candidates = request.get("candidate_paths", [])
    selected = bool(request.get("adopted", True))
    rule = request.get("rule", "")
    if not target:
        raise ValueError("请先选择具体的原片文件")
    match = engine.Match(source, None, rule, "unverified", candidates=candidates, scope_confirmed=selected)
    match = engine.verify(match, target)
    check_cancel(cancel)
    return row_info(match)


def export_plan(request, cancel):
    doc = checked_document(request)
    check_cancel(cancel)
    matches = engine.scan(doc, request["media_root"], request.get("suffixes", ["S03"]),
                          progress=lambda *_: check_cancel(cancel))
    by_id = {match.source.uri: match for match in matches}
    selected_ids = set()
    for choice in request.get("selections", []):
        check_cancel(cancel)
        if not choice.get("adopted"):
            continue
        source_id = choice["source_id"]
        old = by_id.get(source_id)
        if old is None:
            raise ValueError("已选择的素材不在本次 XML 中，请重新扫描")
        if not choice.get("target_path") or not choice.get("target_stat"):
            raise ValueError("采用的素材缺少核对记录，请重新选择原片")
        if list(engine.file_stat(choice["target_path"])) != choice["target_stat"]:
            raise ValueError(f"原片在核对后已改变，请重新扫描：{Path(choice['target_path']).name}")
        old.scope_confirmed = True
        new = engine.verify(old, choice["target_path"])
        if new.status not in engine.EXPORTABLE:
            raise ValueError(f"所选原片当前不能回套：{new.source.name}；{'；'.join(new.reasons)}")
        by_id[source_id] = new
        selected_ids.add(source_id)
    if not selected_ids:
        raise ValueError("请先采用至少一个确认对应的原片")
    matches = [by_id[m.source.uri] for m in matches]
    check_cancel(cancel)
    output_path = Path(request["output_path"])
    output, report_html, report_json = engine.export(doc, matches, output_path.parent, xml_output=output_path)
    remaining = sum(m.status not in engine.EXPORTABLE | {"skipped"} for m in matches)
    return {"status": "success" if not remaining else "exported_with_issues",
            "output_path": str(output), "report_json": str(report_json), "report_html": str(report_html),
            "checks": {"structure": "passed", "timeline": "passed", "host": "not_run"},
            "message": f"已生成原片回套 XML，采用 {len(selected_ids)} 个原片。" + (f"另有 {remaining} 项保留原引用。" if remaining else ""),
            "issues": []}
