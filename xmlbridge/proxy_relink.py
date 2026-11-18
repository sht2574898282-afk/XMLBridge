# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
"""Conservative xmeml media relinking. Never infer frame offsets or rename media."""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urlsplit

from lxml import etree

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mxf", ".m4v", ".avi", ".mkv", ".braw", ".r3d"}
LABELS = {"matched": "参数核对通过", "missing": "未找到原片", "ambiguous": "多个候选",
          "blocked": "需另行处理", "unverified": "信息不足", "needs_scope": "待选择采用", "ready_warning": "可回套·有差异", "skipped": "不在本次规则内"}
EXPORTABLE = {"matched", "ready_warning"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def key(text):
    return unicodedata.normalize("NFC", text).casefold()


def rate(node):
    tb = node.findtext("rate/timebase")
    if not tb:
        return None
    value = Fraction(tb)
    return value * Fraction(1000, 1001) if node.findtext("rate/ntsc", "FALSE").upper() == "TRUE" else value


def timecode_frames(text, fps):
    """Convert SMPTE labels, including 29.97/59.94 DF, without float rounding."""
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})([:;])(\d{2})", text or "")
    if not match or fps is None:
        raise ValueError("时间码或帧率不可解析")
    hh, mm, ss, separator, ff = match.groups()
    hh, mm, ss, ff = map(int, (hh, mm, ss, ff))
    nominal = round(fps)
    if hh > 23 or mm > 59 or ss > 59 or ff >= nominal:
        raise ValueError("时间码超出范围")
    count = ((hh * 60 + mm) * 60 + ss) * nominal + ff
    if separator == ";":
        if fps not in (Fraction(30000, 1001), Fraction(60000, 1001)):
            raise ValueError("该帧率不支持此丢帧时间码")
        drop = 2 if nominal == 30 else 4
        if mm % 10 and ss == 0 and ff < drop:
            raise ValueError("无效的丢帧时间码标签")
        minutes = hh * 60 + mm
        count -= drop * (minutes - minutes // 10)
    return count


def parser():
    return etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                           remove_blank_text=False, strip_cdata=False)


@dataclass
class Source:
    id: str
    uri: str
    name: str
    fps: Fraction | None
    frames: int | None
    tc: int | None
    tc_label: str | None
    width: int | None
    height: int | None
    audio_channels: int
    definitions: list = field(default_factory=list, repr=False)
    errors: list[str] = field(default_factory=list)


@dataclass
class Match:
    source: Source
    target_stem: str | None
    suffix: str | None
    status: str
    candidates: list[str] = field(default_factory=list)
    target: str | None = None
    reasons: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    target_stat: tuple | None = None
    scope_confirmed: bool = False

    def report(self):
        return {"source": self.source.name, "source_id": self.source.id,
                "source_uri": self.source.uri, "target": self.target,
                "rule": self.suffix, "status": self.status,
                "scope_confirmed": self.scope_confirmed,
                "status_label": LABELS[self.status], "reasons": self.reasons,
                "candidates": self.candidates, "metadata": self.metadata,
                "expected": {"fps": str(self.source.fps), "frames": self.source.frames,
                             "tc": self.source.tc_label, "audio_channels": self.source.audio_channels}}


class Document:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.hash = digest(self.path)
        self.tree = etree.parse(str(self.path), parser())
        if self.tree.getroot().tag != "xmeml" or self.tree.getroot().get("version") not in {"4", "5"}:
            raise ValueError("本测试版支持 Premiere 导出的 Final Cut Pro XML（xmeml 4/5）；暂不处理 FCPXML。")
        if self.tree.docinfo.internalDTD and self.tree.docinfo.internalDTD.entities():
            raise ValueError("不处理含自定义实体的 XML")
        self.sources = self._sources()

    def _sources(self):
        groups = {}
        ids = {}
        for node in self.tree.xpath("//file[pathurl]"):
            uri = node.findtext("pathurl") or ""
            node_id = node.get("id")
            if not node_id or not uri:
                raise ValueError("素材定义缺少 id 或 pathurl")
            if node_id in ids and ids[node_id] != uri:
                raise ValueError(f"素材 id {node_id} 指向多个不同路径，需先修复 XML")
            ids[node_id] = uri
            groups.setdefault(uri, []).append(node)
        result = []
        for uri, nodes in groups.items():
            n = nodes[0]
            name = unquote(urlsplit(uri).path).replace("\\", "/").rsplit("/", 1)[-1]
            errors = []
            try:
                fps = rate(n)
                if fps is not None and fps <= 0:
                    raise ValueError("帧率必须大于零")
                frames = int(n.findtext("duration")) if n.findtext("duration") else None
                tc_label = n.findtext("timecode/string")
                tc = int(n.findtext("timecode/frame")) if n.findtext("timecode/frame") else None
                if tc_label:
                    parsed = timecode_frames(tc_label, fps)
                    if tc is not None and tc != parsed:
                        errors.append("XML 的时间码文本与帧数互相矛盾")
                    tc = parsed
                if n.find("timecode/rate") is not None and rate(n.find("timecode")) != fps:
                    errors.append("XML 素材帧率与时间码帧率不同")
                w = n.findtext("media/video/samplecharacteristics/width")
                h = n.findtext("media/video/samplecharacteristics/height")
                width, height = int(w) if w else None, int(h) if h else None
                audio = sum(int(a.findtext("channelcount", "0")) for a in n.findall("media/audio"))
            except (ValueError, TypeError, ZeroDivisionError) as exc:
                fps, frames, tc, tc_label, width, height, audio = None, None, None, None, None, None, 0
                errors.append(f"XML 素材参数不可解析：{exc}")
            # Shared source paths must agree; do not pick one of conflicting definitions.
            fields = ("rate/timebase", "rate/ntsc", "duration", "timecode/string", "timecode/frame",
                      "media/video/samplecharacteristics/width", "media/video/samplecharacteristics/height")
            if any(any(m.findtext(f) != n.findtext(f) for f in fields) for m in nodes[1:]):
                errors.append("同一路径存在参数不一致的素材定义")
            result.append(Source(n.get("id"), uri, name, fps, frames, tc, tc_label,
                                 width, height, audio, nodes, errors))
        return result

    def summary(self):
        return {"xml_version": self.tree.getroot().get("version"),
                "sequences": len(self.tree.xpath("//sequence")),
                "clipitems": len(self.tree.xpath("//clipitem")),
                "unique_media": len(self.sources),
                "s03_media": sum(Path(s.name).stem.upper().endswith("S03") for s in self.sources),
                "speed_effects": len(self.tree.xpath('//filter[effect/effectid="timeremap"]'))}


def ffprobe_path():
    if getattr(sys, "frozen", False):
        bundled = Path(sys._MEIPASS) / "ffprobe"
        if bundled.exists():
            return str(bundled)
    for candidate in (shutil.which("ffprobe"), "/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"):
        if candidate and Path(candidate).is_file():
            return candidate
    raise ValueError("未找到 ffprobe，无法核对原片参数")


def probe(path):
    run = subprocess.run([ffprobe_path(), "-v", "error", "-show_streams", "-show_format",
                          "-of", "json", str(path)], capture_output=True, timeout=45)
    if run.returncode:
        raise ValueError("无法读取视频参数（损坏或编码暂不支持）")
    data = json.loads(run.stdout)
    videos = [s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    if len(videos) != 1:
        raise ValueError("需且只能包含一路视频")
    v = videos[0]
    fps = Fraction(v.get("avg_frame_rate", "0/1"))
    nominal = Fraction(v.get("r_frame_rate", "0/1"))
    if fps <= 0:
        raise ValueError("未读到有效视频帧率")
    frame_value = v.get("nb_frames")
    if frame_value and frame_value != "N/A":
        frames = int(frame_value)
    elif v.get("duration_ts") is not None and v.get("time_base"):
        count = int(v["duration_ts"]) * Fraction(v["time_base"]) * fps
        frames = int(count) if count.denominator == 1 else None
    else:
        frames = None
    labels = {s.get("tags", {}).get("timecode") for s in data.get("streams", [])}
    labels.add(data.get("format", {}).get("tags", {}).get("timecode"))
    labels.discard(None)
    if len(labels) > 1:
        raise ValueError("文件内有互相冲突的时间码")
    label = next(iter(labels), None)
    return {"fps": str(fps), "nominal_fps": str(nominal), "frames": frames,
            "timecode": label, "tc_frames": timecode_frames(label, fps) if label else None,
            "width": v.get("width"), "height": v.get("height"),
            "sar": v.get("sample_aspect_ratio", "1:1"),
            "field_order": v.get("field_order", "unknown"),
            "audio_channels": sum(s.get("channels", 0) for s in data.get("streams", [])
                                  if s.get("codec_type") == "audio")}


def file_stat(path):
    s = Path(path).stat()
    return s.st_size, s.st_mtime_ns, s.st_ino


def verify(match, target, probe_fn=probe):
    result = copy.copy(match)
    result.target = str(Path(target).resolve())
    result.metadata = {}
    result.reasons = []
    result.status = "unverified"
    src = result.source
    if src.errors:
        result.reasons = src.errors[:]
        return result
    try:
        before = file_stat(result.target)
        meta = probe_fn(result.target)
        after = file_stat(result.target)
        if before != after:
            raise ValueError("读取过程中原片发生变化")
        result.metadata = meta
        result.target_stat = after
        failures, missing, warnings = [], [], []
        if src.fps is None or src.frames is None or src.tc is None:
            missing.append("XML 缺少帧率、总帧数或起始时间码")
        if meta.get("frames") is None or meta.get("tc_frames") is None:
            missing.append("原片缺少可核对的总帧数或起始时间码")
        if src.fps is not None and Fraction(meta["fps"]) != src.fps:
            warnings.append(f"帧率不同：XML {src.fps}fps / 原片 {meta['fps']}fps；仅记录，保留 XML 帧率、剪辑点和素材原帧率，不转换")
        if meta.get("nominal_fps") and meta["fps"] != meta["nominal_fps"]:
            missing.append("视频标称帧率与平均帧率不同，暂不自动回套")
        if src.frames is not None and meta.get("frames") is not None and meta["frames"] != src.frames:
            message = f"总帧数不同：XML {src.frames} / 原片 {meta['frames']}"
            if abs(meta["frames"] - src.frames) == 1:
                warnings.append(message + "；相差 1 帧，保留入出点，需在宿主核对使用范围")
            else:
                failures.append(message)
        # Absolute frame counters from different frame rates are not comparable.
        if src.fps == Fraction(meta["fps"]) and src.tc is not None and meta.get("tc_frames") is not None and meta["tc_frames"] != src.tc:
            delta = meta["tc_frames"] - src.tc
            message = f"起始时间码不同：XML {src.tc_label} / 原片 {meta.get('timecode')}（{delta:+d} 帧）"
            if abs(delta) == 1:
                warnings.append(message + "；仅记录，不补偿或移动剪辑点")
            else:
                failures.append(message)
        if meta.get("audio_channels") != src.audio_channels:
            failures.append(f"音频声道总数不同：XML {src.audio_channels} / 原片 {meta.get('audio_channels')}")
        if src.width and src.height and meta.get("width") and meta.get("height"):
            sar_text = src.definitions[0].findtext("media/video/samplecharacteristics/pixelaspectratio", "square")
            if sar_text not in {"square", "1", "1:1"} or meta.get("sar") not in {"1:1", "N/A", None}:
                missing.append("非方形像素暂不自动回套")
            if Fraction(src.width, src.height) != Fraction(meta["width"], meta["height"]):
                failures.append("原片与代理的画面比例不同，需人工处理缩放关系")
        else:
            missing.append("缺少视频尺寸，不能核对画面比例")
        expected_field = src.definitions[0].findtext("media/video/samplecharacteristics/fielddominance")
        if expected_field not in (None, "none") or meta.get("field_order") not in ("progressive", "unknown", None):
            missing.append("隔行素材暂不自动回套")
        result.status = "blocked" if failures else "unverified" if missing else "ready_warning" if warnings else "matched"
        result.reasons = failures + missing + warnings
        if result.status in EXPORTABLE:
            if not warnings:
                result.reasons = ["参数核对通过；选择采用后可生成指向原片的新 XML"]
            if (src.width, src.height) != (meta["width"], meta["height"]):
                result.reasons.append("更新素材尺寸记录；缩放、构图及效果仍需在达芬奇核对")
            if not result.scope_confirmed:
                result.status = "needs_scope"
                result.reasons.insert(0, "S03 等尾缀不能证明相机品牌；尚未确认此素材适用去尾缀规则，不会改写")
    except (ValueError, OSError, subprocess.TimeoutExpired, KeyError, ZeroDivisionError) as exc:
        result.reasons = [str(exc)]
    return result


def scan(doc, root, suffixes=("S03",), probe_fn=probe, progress=None, confirmed_sources=()):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("请选择存在的原片文件夹")
    suffixes = sorted(set(s.strip() for s in suffixes if s.strip()), key=len, reverse=True)
    if not suffixes or any("/" in s or "\\" in s for s in suffixes):
        raise ValueError("至少提供一个有效尾缀；多个尾缀用逗号分开")
    index = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            index.setdefault(key(path.stem), []).append(str(path.resolve()))
    matches = []
    confirmed_sources = set(confirmed_sources)
    for i, src in enumerate(doc.sources):
        stem = Path(src.name).stem
        suffix = next((s for s in suffixes if key(stem).endswith(key(s)) and len(stem) > len(s)), None)
        if Path(src.name).suffix.lower() not in VIDEO_EXTENSIONS or not suffix:
            match = Match(src, None, None, "skipped", reasons=["保留原始引用，不改写非目标素材"])
        else:
            target_stem = stem[:-len(suffix)]
            candidates = list(dict.fromkeys(index.get(key(target_stem), [])))
            match = Match(src, target_stem, suffix, "missing", candidates=candidates)
            # Authorization is keyed by the full source URI, never by suffix, filename or guessed camera brand.
            match.scope_confirmed = src.uri in confirmed_sources
            if len(candidates) == 1:
                match = verify(match, candidates[0], probe_fn)
            elif len(candidates) > 1:
                # A matching timecode is not proof of identity across duplicate cards.
                match.status = "ambiguous"
                match.reasons = ["存在多个同名候选；请选择所属卡的原片，随后仍须核对参数"]
            else:
                match.reasons = ["没有找到符合尾缀规则的原片；未猜测文件扩展名或路径"]
        matches.append(match)
        if progress:
            progress(i + 1, len(doc.sources))
    return matches


def preserved_digest(tree):
    """Mask only the allowed edits; every other node, attribute and text must survive."""
    tree = copy.deepcopy(tree)
    for node in tree.xpath("//file[pathurl]"):
        for p in ("pathurl", "name", "media/video/samplecharacteristics/width",
                  "media/video/samplecharacteristics/height"):
            child = node.find(p)
            if child is not None:
                child.text = "__APPROVED_MEDIA_REFERENCE__"
    return hashlib.sha256(etree.tostring(tree, method="c14n", with_comments=True)).hexdigest()


def export(doc, matches, folder, *, xml_output=None):
    if digest(doc.path) != doc.hash:
        raise ValueError("XML 在扫描后发生变化，请重新扫描")
    selected = [m for m in matches if m.status in EXPORTABLE and m.scope_confirmed]
    if any(m.status in EXPORTABLE and not m.scope_confirmed for m in matches):
        raise ValueError("存在未确认规则适用范围的素材；不能仅凭尾缀回套")
    if not selected:
        raise ValueError("没有参数核对通过的素材，不能生成回套 XML")
    for m in selected:
        if file_stat(m.target) != m.target_stat:
            raise ValueError(f"原片在扫描后发生变化，请重新扫描：{Path(m.target).name}")
    tree = copy.deepcopy(doc.tree)
    by_uri = {m.source.uri: m for m in selected}
    edited = 0
    for node in tree.xpath("//file[pathurl]"):
        match = by_uri.get(node.findtext("pathurl"))
        if match is None:
            continue
        node.find("pathurl").text = Path(match.target).as_uri()
        if node.find("name") is not None:
            node.find("name").text = Path(match.target).name
        for tag in ("width", "height"):
            child = node.find(f"media/video/samplecharacteristics/{tag}")
            if child is not None:
                child.text = str(match.metadata[tag])
        edited += 1
    if preserved_digest(tree) != preserved_digest(doc.tree):
        raise ValueError("结构保护检查失败：出现素材引用之外的改动")
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    output = Path(xml_output).resolve() if xml_output else folder / f"{doc.path.stem}_原片回套.xml"
    if output.parent != folder or output.suffix.lower() != ".xml":
        raise ValueError("输出应为所选文件夹中的 .xml 文件")
    report_path = folder / f"{output.stem}_说明.html"
    json_path = folder / f"{output.stem}_明细.json"
    if any(p.exists() for p in (output, report_path, json_path)):
        raise ValueError("输出文件已存在，请选择新的输出文件夹；不会覆盖已有结果")
    payload = etree.tostring(tree, encoding="UTF-8", xml_declaration=True,
                             doctype=doc.tree.docinfo.doctype or None, pretty_print=False)
    reparsed = etree.ElementTree(etree.fromstring(payload, parser()))
    if preserved_digest(reparsed) != preserved_digest(doc.tree):
        raise ValueError("序列化后结构保护检查失败")
    counts = dict(Counter(m.status for m in matches))
    report = {"version": "0.2.0-test", "xml_output": str(output), "input_sha256": doc.hash, "output_sha256": hashlib.sha256(payload).hexdigest(),
              "summary": doc.summary(), "counts": counts, "rewritten_definitions": edited,
              "structure_preserved": True, "host_validation": "not_run", "picture_validation": "not_run",
              "partial_output": any(m.status not in EXPORTABLE | {"skipped"} for m in matches),
              "matches": [m.report() for m in matches]}
    # Exclusive creation: neither original XML nor existing deliverables are overwritten.
    with output.open("xb") as handle:
        handle.write(payload)
    with json_path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    with report_path.open("x", encoding="utf-8") as handle:
        handle.write(report_html(report))
    return output, report_path, json_path


def report_html(report):
    esc = lambda x: html.escape(str(x if x is not None else ""))
    counts = " · ".join(f"{LABELS[k]} {v}" for k, v in report["counts"].items())
    rows = "".join(f'<tr><td>{esc(m["source"])}</td><td>{esc(m["status_label"])}</td>'
                   f'<td>{esc(m["target"])}</td><td>{esc("；".join(m["reasons"]))}</td></tr>'
                   for m in report["matches"] if m["status"] != "skipped")
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>代理回套测试报告</title>
    <style>body{{font:15px -apple-system,sans-serif;color:#20262d;background:#f3f5f7;margin:40px}}
    main{{background:white;padding:32px;border-radius:16px}}h1{{font-size:27px}}p{{line-height:1.8}}
    table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{text-align:left;border-bottom:1px solid #ddd;padding:12px;word-break:break-all}}
    .note{{background:#fff2d9;padding:16px;border-radius:8px}}code{{word-break:break-all}}</style><main>
    <h1>已生成原片回套 XML</h1><p>主结果：<a href="{esc(Path(report['xml_output']).name)}">{esc(Path(report['xml_output']).name)}</a></p><p>{esc(counts)}</p>
    <p>仅对明确采用的素材进行回套；S03 不用于判断相机品牌。帧率差异和 1 帧差异记录在说明中，不转换帧率或补偿时间码。已改写 {report['rewritten_definitions']} 个素材定义。剪辑点、时间线、嵌套、音频映射和效果节点的结构保护检查通过。
    允许改动仅包括素材路径、素材文件名及素材宽高记录。未通过项保留原引用。</p>
    <p class="note">这是测试输出。参数核对通过不等于画面身份正确；实际画面、缩放、变速及 Premiere / 达芬奇导入尚未验证。
    {'当前是部分回套，仍有待处理素材。' if report['partial_output'] else ''}</p>
    <p>输入 SHA-256：<code>{esc(report['input_sha256'])}</code><br>输出 SHA-256：<code>{esc(report['output_sha256'])}</code></p>
    <table><thead><tr><th>代理素材</th><th>结果</th><th>原片</th><th>核对说明</th></tr></thead><tbody>{rows}</tbody></table></main></html>'''
