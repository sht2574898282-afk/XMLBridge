# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
"""Local XML inspection and explicit version migration, with opt-in risk export.

The source tree is never edited. A target DTD is not a host guarantee.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import shutil
import tempfile
import threading
import uuid
from datetime import datetime
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from lxml import etree

from .media_paths import foreign_absolute_reference, local_media_path

from .models import (ConversionPlan, ConversionResult, Document, FCP_VERSIONS,
                     Issue, MediaReference, ProjectInfo, Target, XMEML_VERSIONS,
                     json_safe)

SCHEMAS = Path(__file__).parent / "schemas"
TIME_ATTRS = {"start", "offset", "duration", "tcStart", "frameDuration",
              "audioStart", "audioDuration", "videoStart", "videoDuration",
              "anchorOffset"}
STORY_TAGS = {"asset-clip", "clip", "ref-clip", "mc-clip", "sync-clip", "gap",
              "video", "audio", "title", "caption", "transition", "spine"}
# These removals have defined, local effects. Timing-bearing objects are never
# removed as a generic fallback.
REMOVABLE = {
    "adjust-colorConform": "移除自动色彩匹配，画面色彩可能改变",
    "adjust-voiceIsolation": "移除人声隔离处理，声音会改变",
    "adjust-stereo-3D": "移除立体画面调整，立体呈现会改变",
    "adjust-cinematic": "移除电影效果模式调整，焦点与景深可能改变",
    "adjust-360-transform": "移除 360° 变换，画面会改变",
    "adjust-reorient": "移除重定向调整，画面会改变",
    "adjust-orientation": "移除方向调整，画面会改变",
    "hidden-clip-marker": "移除此隐藏片段标记",
    "match-analysis-type": "移除此智能集合条件，集合结果会改变",
    "object-tracker": "移除对象跟踪分析；关联效果需要另行处理",
    "info-asc-cdl": "移除旧式 CDL 附加描述；请人工核对色彩",
}
DROP_ATTRIBUTES = {"nameOverride": "移除效果的自定义显示名称"}

RISK_FATAL_CODES = {"READ_ERROR", "UNKNOWN_FORMAT", "UNSUPPORTED_VERSION", "UNSUPPORTED_TARGET",
                    "APPLICATION_FORMAT", "PROJECT_SELECTION", "PLAN_ERROR", "SOURCE_CHANGED"}


def effective_decisions(issues, decisions=None, allow_risky=False):
    selected = dict(decisions or {})
    if allow_risky:
        for issue in issues:
            if issue.choices and issue.id not in selected:
                action = next((key for key in issue.choices if key != "stop"), None)
                if action:
                    selected[issue.id] = action
    return selected


def export_blockers(issues, decisions=None, *, allow_risky=False, same_family=True):
    decisions = effective_decisions(issues, decisions, allow_risky)
    unresolved = [i for i in issues if i.unresolved(decisions)]
    if allow_risky and same_family:
        return [i for i in unresolved if i.code in RISK_FATAL_CODES or decisions.get(i.id) == "stop"]
    return unresolved


class Cancelled(Exception):
    pass


def check_cancel(event):
    if event is not None and event.is_set():
        raise Cancelled()


def parse_time(value: str | None, default: Fraction = Fraction(0)) -> Fraction:
    if value is None:
        return default
    if not re.fullmatch(r"-?\d+(?:/\d+)?s", value.strip()):
        raise ValueError(f"无效的精确时间：{value}")
    raw = value.strip()[:-1]
    bits = raw.split("/")
    if abs(int(bits[0])) > 2**63 - 1 or (len(bits) == 2 and not 0 < int(bits[1]) <= 2**32 - 1):
        raise ValueError("时间超出 FCPXML 整数范围")
    return Fraction(raw)


def time_string(value: Fraction) -> str:
    return f"{value}s"


def _parser():
    return etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                           remove_blank_text=False, strip_cdata=False)


@lru_cache(maxsize=16)
def schema(family: str, version: str):
    filename = f"FCPXMLv{version.replace('.', '_')}.dtd" if family == "fcpxml" else f"XMEMLv{version}.dtd"
    path = SCHEMAS / filename
    return etree.DTD(str(path)) if path.is_file() else None


def _schema_errors(tree, family, version) -> list[str]:
    # A fresh DTD keeps per-validation error logs isolated across worker threads.
    cached = schema(family, version)
    if cached is None:
        return ["尚未提供该版本的格式定义"]
    with _DTD_LOCK:
        if cached.validate(tree):
            return []
        return [f"第 {e.line} 行：{e.message}" for e in list(cached.error_log)[:20]]


_DTD_LOCK = threading.RLock()


def project_nodes(tree, family):
    if family == "fcpxml":
        projects = tree.xpath("//project")
        if projects:
            return projects
        return tree.xpath("//sequence[not(ancestor::sequence) and not(ancestor::resources)]")
    return tree.xpath("//sequence[not(ancestor::sequence)]")


def _xrate(node) -> Fraction:
    tb = node.findtext("rate/timebase", "25")
    value = Fraction(tb)
    return value * Fraction(1000, 1001) if node.findtext("rate/ntsc", "FALSE").upper() == "TRUE" else value


def _projects(tree, family) -> list[ProjectInfo]:
    info = []
    formats = {n.get("id"): n for n in tree.xpath("/fcpxml/resources/format")}
    for index, node in enumerate(project_nodes(tree, family)):
        if family == "fcpxml":
            sequence = node if node.tag == "sequence" else node.find("sequence")
            if sequence is None:
                continue
            fmt = formats.get(sequence.get("format"))
            frame = parse_time(fmt.get("frameDuration")) if fmt is not None and fmt.get("frameDuration") else None
            fps = str(1 / frame) if frame and frame > 0 else "未知"
            duration = sequence.get("duration", "0s")
            clips = sequence.xpath(".//asset-clip | .//clip | .//ref-clip | .//mc-clip | .//sync-clip | .//title | .//caption")
            refs = {n.get("ref") for n in sequence.iter() if isinstance(n.tag, str) and n.get("ref")}
            name = node.get("name", f"项目 {index + 1}")
        else:
            fps_value = _xrate(node)
            fps = str(fps_value)
            duration = time_string(Fraction(node.findtext("duration", "0")) / fps_value)
            clips = node.xpath("./media/video/track/clipitem | ./media/audio/track/clipitem | .//generatoritem")
            refs = {n.get("id") for n in node.xpath(".//file")}
            name = node.findtext("name", f"序列 {index + 1}")
        info.append(ProjectInfo(f"p{index}", name, fps, duration, len(clips), len(refs)))
    return info


def resolve_uri(uri: str, xml_path: Path) -> Path | None:
    return local_media_path(uri, xml_path)


def _url_fields(tree, family):
    if family == "fcpxml":
        for node in tree.xpath("//asset[@src] | //media-rep[@src] | //effect[@src] | //locator[@url]"):
            attr = "url" if node.tag == "locator" else "src"
            yield node, attr, node.get(attr)
    else:
        for node in tree.xpath("//pathurl"):
            if node.text:
                yield node, None, node.text.strip()


def _loc(node):
    project = next((n.get("name", "") for n in node.iterancestors("project")), "")
    clip_node = next((n for n in [node, *node.iterancestors()] if n.tag in STORY_TAGS and n.get("name")), None)
    clip = clip_node.get("name", "") if clip_node is not None else ""
    time = clip_node.get("offset", "") if clip_node is not None else ""
    return dict(project=project, clip=clip, time=time, location=node.getroottree().getpath(node))


def _semantic_issues(tree, family) -> list[Issue]:
    issues = []
    if family == "fcpxml":
        ids = {n.get("id") for n in tree.iter() if isinstance(n.tag, str) and n.get("id")}
        for node in tree.iter():
            if not isinstance(node.tag, str):
                continue
            for key, value in node.attrib.items():
                if key in {"ref", "format", "projectRef", "dataRef"} and value not in ids:
                    issues.append(Issue("MISSING_REFERENCE", f"引用的资源不存在：{value}", "error", **_loc(node)))
                if key in TIME_ATTRS or (node.tag == "timept" and key in {"time", "value"}):
                    try:
                        time = parse_time(value)
                        if key in {"duration", "audioDuration", "videoDuration"} and time < 0:
                            raise ValueError("时长不能为负数")
                        if key == "frameDuration" and time <= 0:
                            raise ValueError("帧时长必须大于零")
                    except (ValueError, ZeroDivisionError) as exc:
                        issues.append(Issue("INVALID_TIME", f"{key}：{exc}", "error", **_loc(node)))
        assets = {n.get("id"): n for n in tree.xpath("/fcpxml/resources/asset")}
        for clip in tree.xpath("//asset-clip[not(timeMap) and not(conform-rate)]"):
            asset = assets.get(clip.get("ref"))
            if asset is None or asset.get("duration") is None:
                continue
            try:
                media_start = parse_time(asset.get("start"))
                media_duration = parse_time(asset.get("duration"))
                source_start = parse_time(clip.get("start"))
                duration = parse_time(clip.get("duration"), media_duration)
                if source_start < media_start or source_start + duration > media_start + media_duration:
                    issues.append(Issue("SOURCE_RANGE", "片段源范围超出已声明的素材范围", "error",
                                        details=f"片段 {source_start}s – {source_start + duration}s；素材 {media_start}s – {media_start + media_duration}s", **_loc(clip)))
            except (ValueError, ZeroDivisionError):
                pass  # The individual invalid time is already reported above.
    else:
        for node in tree.xpath("//rate"):
            try:
                rate = Fraction(node.findtext("timebase", "0"))
                if rate <= 0:
                    raise ValueError()
            except (ValueError, ZeroDivisionError):
                issues.append(Issue("INVALID_RATE", "帧率 timebase 必须大于零", "error", **_loc(node)))
        file_ids = {n.get("id") for n in tree.xpath("//file[pathurl or media or name]")}
        for node in tree.xpath("//file[@id and not(*)]"):
            if node.get("id") not in file_ids:
                issues.append(Issue("MISSING_REFERENCE", f"媒体引用不存在：{node.get('id')}", "error", **_loc(node)))
        for node in tree.xpath("//clipitem | //sequence | //file[duration]"):
            values = {}
            for key in ("start", "end", "in", "out", "duration"):
                text = node.findtext(key)
                if text is None:
                    continue
                try:
                    if not re.fullmatch(r"-?\d+", text.strip()):
                        raise ValueError()
                    values[key] = int(text)
                except ValueError:
                    issues.append(Issue("INVALID_FRAME", f"{key} 必须是整数帧数", "error", **_loc(node)))
            if node.tag == "clipitem":
                if any(values.get(k, 0) < 0 for k in ("in", "out")) or ("in" in values and "out" in values and values["out"] < values["in"]):
                    issues.append(Issue("SOURCE_RANGE", "片段源入出点范围无效", "error", **_loc(node)))
                transition = node.getparent() is not None and node.getparent().find("transitionitem") is not None
                invalid = any(values.get(k, 0) < 0 and not (transition and values[k] == -1) for k in ("start", "end"))
                if invalid or (values.get("start", -1) >= 0 and values.get("end", -1) >= 0 and values["end"] < values["start"]):
                    issues.append(Issue("TIMELINE_RANGE", "片段时间线范围无效", "error", **_loc(node)))
            if values.get("duration", 0) < 0:
                issues.append(Issue("INVALID_FRAME", "时长不能为负数", "error", **_loc(node)))
    return issues


def inspect(source: str | Path) -> Document:
    path = Path(source).expanduser().resolve()
    doc = Document(path)
    try:
        if path.is_dir():
            if path.suffix.lower() != ".fcpxmld":
                raise ValueError("请选择 XML 文件或 .fcpxmld 目录包")
            doc.bundle_root = path
            doc.xml_path = path / "Info.fcpxml"
        else:
            doc.xml_path = path
        if doc.xml_path.stat().st_size > 256 * 1024 * 1024:
            raise ValueError("XML 超过 256 MB，请先按项目拆分")
        data = doc.xml_path.read_bytes()
        doc.input_hash = hashlib.sha256(data).hexdigest()
        if re.search(br"<!ENTITY\s", data, re.I):
            raise ValueError("文件包含实体声明；请使用不含自定义实体的剪辑 XML")
        doc.tree = etree.ElementTree(etree.fromstring(data, _parser(), base_url=doc.xml_path.as_uri()))
        if doc.tree.docinfo.internalDTD and list(doc.tree.docinfo.internalDTD.iterentities()):
            raise ValueError("不支持自定义 XML 实体")
        root = doc.tree.getroot()
        doc.family = root.tag if root.tag in ("fcpxml", "xmeml") else "unknown"
        doc.version = root.get("version", "")
        versions = FCP_VERSIONS if doc.family == "fcpxml" else XMEML_VERSIONS
        if doc.family == "unknown":
            doc.issues.append(Issue("UNKNOWN_FORMAT", "这不是 FCPXML 或旧式 Final Cut XML", "error"))
            return doc
        if doc.version not in versions:
            doc.issues.append(Issue("UNSUPPORTED_VERSION", f"已识别 {doc.family.upper()} {doc.version}；当前转换范围为 FCPXML 1.8–1.14 / XMEML 4–5", "error"))
        else:
            errors = _schema_errors(doc.tree, doc.family, doc.version)
            if errors:
                doc.issues.append(Issue("SOURCE_SCHEMA", "源文件未通过对应版本的结构检查", "error", details="\n".join(errors)))
        doc.issues.extend(_semantic_issues(doc.tree, doc.family))
        try:
            doc.projects = _projects(doc.tree, doc.family)
        except (ValueError, ZeroDivisionError) as exc:
            doc.issues.append(Issue("PROJECT_TIME", f"无法读取项目时间信息：{exc}", "error"))
        if not doc.projects:
            doc.issues.append(Issue("NO_TIMELINE", "文件没有项目时间线；同格式转换仍可保留素材与事件", "info"))
        for index, (node, attr, uri) in enumerate(_url_fields(doc.tree, doc.family)):
            resolved = resolve_uri(uri, doc.xml_path)
            # Effect/plugin locations and analysis locators are not source media.
            exists = resolved.exists() if resolved is not None else None
            ref_id = node.get("id") or node.getparent().get("id", f"media{index}")
            if node.tag not in {"effect", "locator"}:
                doc.media.append(MediaReference(ref_id, uri, str(resolved or ""), exists))
            if exists is False:
                kind = "关联资源" if node.tag in {"effect", "locator"} else "素材"
                doc.issues.append(Issue("OFFLINE_MEDIA", f"{kind}当前离线：{resolved.name}", "warning", details=str(resolved), **_loc(node)))
            elif foreign_absolute_reference(uri):
                doc.issues.append(Issue("FOREIGN_MEDIA_PATH", "素材使用其他系统的绝对路径；保留原引用，交接后需重新链接", "warning", details=uri, **_loc(node)))
            elif resolved is None:
                doc.issues.append(Issue("REMOTE_MEDIA", "包含远程或网络卷引用，工具不会下载或验证该资源", "warning", **_loc(node)))
    except (OSError, ValueError, etree.XMLSyntaxError, etree.DTDParseError) as exc:
        doc.issues.append(Issue("READ_ERROR", f"无法读取文件：{exc}", "error"))
    return doc


def _select(tree, family, selected):
    if selected is None:
        return
    nodes = project_nodes(tree, family)
    for index, node in enumerate(nodes):
        if f"p{index}" not in selected and node.getparent() is not None:
            node.getparent().remove(node)


def _timing_signature(tree, family):
    if family == "fcpxml":
        return [(node.tag, tuple(sorted((key, value) for key, value in node.attrib.items()
                                        if key in TIME_ATTRS | {"ref", "lane", "enabled", "tcFormat"})))
                for node in tree.iter() if isinstance(node.tag, str)
                and (node.tag in STORY_TAGS or node.tag in {"sequence", "timept", "conform-rate"})]
    timing_tags = {"rate", "start", "end", "in", "out", "duration", "timecode", "link", "sourcetrack"}
    return [(node.tag, etree.tostring(node, method="c14n")) for node in tree.iter()
            if isinstance(node.tag, str) and node.tag in timing_tags]


def _offer(issues, issue, decisions, preview, action):
    issues.append(issue)
    selected = decisions.get(issue.id)
    if selected and selected in issue.choices and selected != "stop":
        action(selected)
    elif selected == "preserve":
        return
    elif preview:
        allowed = next((key for key in issue.choices if key != "stop"), None)
        if allowed:
            action(allowed)


def _boundary(tree, old, new, issues, decisions, preview):
    root = tree.getroot()
    if old == "1.8" and new == "1.9":
        for asset in root.xpath("resources/asset"):
            uri = asset.attrib.pop("src", None)
            if uri is not None:
                rep = etree.Element("media-rep", kind="original-media", src=uri)
                bookmark = asset.find("bookmark")
                if bookmark is not None:
                    rep.append(bookmark)
                asset.insert(0, rep)
        for text in root.xpath(".//text[not(@placement)]"):
            text.set("placement", "bottom")
    if old == "1.9" and new == "1.8":
        for asset in root.xpath("resources/asset"):
            reps = asset.findall("media-rep")
            original = [r for r in reps if r.get("kind", "original-media") == "original-media"]
            if len(original) != 1:
                issues.append(Issue("NO_ORIGINAL_MEDIA", "旧版本需要唯一原始媒体，当前资源无法准确选择", "error", **_loc(asset)))
                continue
            rep = original[0]
            extra = any(k not in {"src", "kind"} for k in rep.attrib) or len(reps) > 1
            def migrate(_action, a=asset, r=rep, all_reps=reps):
                a.set("src", r.get("src"))
                bookmark = r.find("bookmark")
                for child in all_reps:
                    a.remove(child)
                if bookmark is not None:
                    a.insert(0, bookmark)
            if extra:
                issue = Issue("MEDIA_REP_LOSS", "FCPXML 1.8 无法保存多媒体表示或其附加信息", "error",
                              choices={"choose_original": "仅保留原始媒体（移除代理表示及附加信息）", "stop": "停止转换"}, **_loc(asset))
                _offer(issues, issue, decisions, preview, migrate)
            else:
                migrate("choose_original")
        for node in root.xpath(".//filter-video"):
            data_nodes = node.findall("data")
            if len(data_nodes) > 1:
                issues.append(Issue("MULTIPLE_EFFECT_DATA", "此效果含多个数据块，FCPXML 1.8 无法完整表达；请在原软件处理后重导出", "error", **_loc(node)))
        # An omitted placement in 1.9 has no defined equivalence to 1.8's bottom.
        for text in root.xpath(".//text[not(@placement)]"):
            issue = Issue("CAPTION_PLACEMENT", "目标版本会将未指定的字幕位置解释为底部", "error",
                          choices={"bottom": "明确使用底部位置", "stop": "停止转换"}, **_loc(text))
            _offer(issues, issue, decisions, preview, lambda _, n=text: n.set("placement", "bottom"))
    # The child was removed from the filter content model, even though a
    # similarly named annotation existed in earlier DTDs.
    if old == "1.9" and new == "1.10":
        for node in list(root.xpath(".//filter-video/info-asc-cdl")):
            issue = Issue("LEGACY_CDL", "新版本不允许效果中的旧式 CDL 子元素", "error",
                          choices={"drop": REMOVABLE["info-asc-cdl"], "stop": "停止转换"}, **_loc(node))
            _offer(issues, issue, decisions, preview, lambda _, n=node: n.getparent().remove(n))


def _target_features(tree, family, version, issues, decisions, preview):
    dtd = schema(family, version)
    if dtd is None:
        return
    declarations = {e.name: e for e in dtd.iterelements()}
    root = tree.getroot()
    nodes = [node for node in root.iter() if isinstance(node.tag, str)]
    locations = {node: _loc(node) for node in nodes}
    for node in nodes:
        if node is not root and not any(parent is root for parent in node.iterancestors()):
            continue
        location = locations[node]
        decl = declarations.get(node.tag)
        if decl is None:
            options = {"drop": REMOVABLE[node.tag], "stop": "停止转换"} if node.tag in REMOVABLE else {}
            issue = Issue("TARGET_ELEMENT", f"目标版本不支持 {node.tag}", "error", choices=options,
                          details="移除会改变对应功能。需要保留时，请在原软件中完成处理或选择较新版本。", **location)
            _offer(issues, issue, decisions, preview, lambda _, n=node: n.getparent().remove(n))
            continue
        attrs = {a.name: a for a in decl.iterattributes()}
        for key, value in list(node.attrib.items()):
            if node.tag == root.tag and key == "version":
                continue
            attr = attrs.get(key)
            if attr is None:
                # Dropping an explicit default is safe only when it is also
                # the target's documented intrinsic default, covered by tests.
                if key == "enabled" and node.tag in {"adjust-stabilization", "adjust-rollingShutter"} and value == "1":
                    node.attrib.pop(key)
                    continue
                options = {"drop": DROP_ATTRIBUTES[key], "stop": "停止转换"} if key in DROP_ATTRIBUTES else {}
                if node.tag == "asset" and key == "videoSources" and value == "1":
                    options = {"single_video": "按单视频流资源迁移（旧格式不记录流数量）", "stop": "停止转换"}
                issue = Issue("TARGET_ATTRIBUTE", f"目标版本不支持 {node.tag}@{key}", "error", choices=options, **location)
                _offer(issues, issue, decisions, preview, lambda _, n=node, k=key: n.attrib.pop(k, None))
            elif attr.type == "enumeration" and value not in set(attr.values()):
                # Sampling, frame-rate, stereo and stabilization choices have
                # real picture/timing implications; no guessed replacement.
                issues.append(Issue("TARGET_VALUE", f"目标版本不能表达 {node.tag}@{key}={value}", "error", **location))


def _migrate(tree, family, old_version, new_version, decisions=None, preview=False):
    tree = copy.deepcopy(tree)
    issues = []
    decisions = decisions or {}
    if family == "fcpxml":
        old, new = int(old_version.split(".")[1]), int(new_version.split(".")[1])
        direction = 1 if new > old else -1
        for n in range(old, new, direction):
            source, target = f"1.{n}", f"1.{n + direction}"
            _boundary(tree, source, target, issues, decisions, preview)
            tree.getroot().set("version", target)
            _target_features(tree, family, target, issues, decisions, preview)
    else:
        tree.getroot().set("version", new_version)
        _target_features(tree, family, new_version, issues, decisions, preview)
    return tree, _dedupe(issues)


def _dedupe(issues):
    seen = set()
    return [i for i in issues if not (i.id in seen or seen.add(i.id))]


def plan_conversion(doc: Document, target: Target, project_ids: list[str] | None = None,
                    *, allow_risky: bool = False) -> ConversionPlan:
    plan = ConversionPlan(doc, target, project_ids, list(doc.issues))
    versions = FCP_VERSIONS if target.family == "fcpxml" else XMEML_VERSIONS
    if target.family not in {"fcpxml", "xmeml"} or target.version not in versions:
        plan.issues.append(Issue("UNSUPPORTED_TARGET", "目标格式或版本不在支持范围", "error"))
    app = target.application.lower()
    if "premiere" in app and target.family != "xmeml":
        plan.issues.append(Issue("APPLICATION_FORMAT", "Premiere 交接需要 Final Cut 7 XML（XMEML），请选择相应类型", "error"))
    if target.application == "Final Cut Pro" and target.family != "fcpxml":
        plan.issues.append(Issue("APPLICATION_FORMAT", "现代 Final Cut Pro 交接请选择 FCPXML", "error"))
    known_ids = {p.id for p in doc.projects}
    if project_ids is not None and (not project_ids or not set(project_ids) <= known_ids):
        plan.issues.append(Issue("PROJECT_SELECTION", "请选择至少一个有效项目或序列", "error"))
    if doc.tree is None or export_blockers(plan.issues, allow_risky=allow_risky,
                                          same_family=doc.family == target.family):
        return plan
    try:
        selected_tree = copy.deepcopy(doc.tree)
        _select(selected_tree, doc.family, project_ids)
        if doc.family == target.family:
            plan.steps = ["保留原始 XML 结构", f"迁移至 {target.family.upper()} {target.version}", "校验结构、引用与精确时间"]
            preview_tree, issues = _migrate(selected_tree, doc.family, doc.version, target.version, preview=True)
            plan.issues.extend(issues)
            if not any(i.severity == "error" and not i.choices for i in issues):
                target_errors = _schema_errors(preview_tree, target.family, target.version)
                if target_errors:
                    plan.issues.append(Issue("TARGET_SCHEMA", "目标版本的内容结构无法准确迁移", "error", details="\n".join(target_errors)))
        else:
            from .interchange import analyze_cross
            plan.steps = ["读取精确时间线模型", "转换素材、轨道和已支持功能", "生成目标格式并逐项核对"]
            plan.issues.extend(analyze_cross(doc, target, project_ids))
        plan.issues = _dedupe(plan.issues)
    except Exception as exc:
        plan.issues.append(Issue("PLAN_ERROR", f"无法建立转换计划：{exc}", "error"))
    return plan


def _target_container(tree, target):
    """Resolve's importer needs a library wrapper even where the DTD does not."""
    if target.family != "fcpxml" or "resolve" not in target.application.lower():
        return
    root = tree.getroot()
    if root.find("library") is not None:
        return
    objects = [n for n in root if isinstance(n.tag, str) and n.tag not in {"import-options", "resources"}]
    library = etree.SubElement(root, "library")
    if objects and all(n.tag == "event" for n in objects):
        for node in objects:
            library.append(node)
    elif objects:
        event = etree.SubElement(library, "event", name="XMLBridge")
        for node in objects:
            event.append(node)


def _copy_file(source, destination, event):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        while True:
            check_cancel(event)
            block = src.read(4 * 1024 * 1024)
            if not block:
                break
            dst.write(block)
    shutil.copystat(source, destination)


def _rebase_urls(tree, family, doc, stage, final, xml_relative, event):
    resource_root = None
    if doc.bundle_root:
        resource_root = xml_relative.parent if xml_relative.name == "Info.fcpxml" else Path("Resources") / doc.bundle_root.name
        # Templates can reference sidecars inside their own format. Preserve
        # the package's resource hierarchy, including such indirect resources.
        for source in doc.bundle_root.rglob("*"):
            check_cancel(event)
            if source == doc.xml_path or not source.is_file():
                continue
            relative = source.relative_to(doc.bundle_root)
            destination = stage / resource_root / relative
            if not destination.exists():
                _copy_file(source, destination, event)
    for node, attr, uri in _url_fields(tree, family):
        check_cancel(event)
        source = resolve_uri(uri, doc.xml_path)
        if source is None:
            continue
        replacement = source.as_uri()
        if doc.bundle_root is not None and source.is_relative_to(doc.bundle_root):
            relative = source.relative_to(doc.bundle_root)
            dest_relative = resource_root / relative
            if family == "fcpxml":
                relative_to_xml = dest_relative.relative_to(xml_relative.parent) if xml_relative.parent != Path(".") else dest_relative
                replacement = quote(relative_to_xml.as_posix(), safe="/")
            else:
                replacement = (final / dest_relative).as_uri()
        if attr:
            node.set(attr, replacement)
        else:
            node.text = replacement


def _report(plan, result, decisions, final):
    return {
        "tool": "XMLBridge", "version": "0.2.0", "created": datetime.now().astimezone().isoformat(),
        "source": {"path": str(plan.source.path), "sha256": plan.source.input_hash,
                   "family": plan.source.family, "version": plan.source.version},
        "target": json_safe(plan.target), "selected_projects": plan.project_ids,
        "projects": json_safe(plan.source.projects), "status": result.status,
        "output": result.output_path, "checks": json_safe(result.checks),
        "message": result.message, "issues": json_safe(result.issues),
        "decisions": decisions, "host_note": "未对本次输出执行剪辑软件导入；结构与时间检查不能替代宿主验证。",
    }


def _write_reports(stage, final, plan, result, decisions):
    payload = _report(plan, result, decisions, final)
    (stage / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    for i in result.issues:
        action = decisions.get(i.id, "")
        choice = ("保留并记录" if action == "preserve" else
                  i.choices.get(action, "未选择" if i.choices else
                                ("保留并记录" if result.checks.get("risk_export") else "—")))
        severity = {"error": "错误", "warning": "提醒", "info": "信息"}.get(i.severity, i.severity)
        cells = [severity, i.project, i.clip, i.time, i.message, i.details, choice]
        rows.append("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>")
    checks = []
    for key, name in [("structure", "结构检查"), ("timeline", "时间线检查"), ("host", "目标软件导入")]:
        state = str(result.checks.get(key, "not_run"))
        state_name = {"passed": "通过", "failed": "未通过", "not_run": "尚未验证"}.get(state, state)
        tone = {"passed": "passed", "failed": "failed"}.get(state, "pending")
        checks.append(f'<div class="check {tone}"><span>{name}</span><strong>{html.escape(state_name)}</strong></div>')
    status_name = {"success": "已生成", "exported_with_issues": "已生成 · 有问题", "blocked": "未生成", "cancelled": "已取消", "failed": "导出失败"}.get(result.status, result.status)
    target_name = f"{plan.target.application} · {plan.target.family.upper()} {plan.target.version}"
    body = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light">
<title>XMLBridge 交接报告</title>
<style>
:root{{color-scheme:light}}*{{box-sizing:border-box}}body{{font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;background:#f5f6fa;color:#252b38;margin:0;padding:40px 24px}}main{{max-width:1200px;margin:auto}}header{{margin-bottom:24px}}h1{{font-size:30px;line-height:1.3;margin:8px 0 16px;letter-spacing:-.5px}}h2{{font-size:17px;margin:0 0 16px}}p{{margin:8px 0}}.tag{{font-size:12px;letter-spacing:2px;font-weight:600;color:#5d61be}}.muted{{color:#687080}}.panel{{background:#ffffff;border:1px solid #e2e6ef;border-radius:14px;padding:24px;margin:16px 0}}.summary{{display:flex;gap:12px;align-items:center;flex-wrap:wrap}}.status{{background:#eef0fc;color:#4a4fa7;border-radius:6px;padding:4px 10px;font-weight:600}}dl{{margin:0;display:grid;grid-template-columns:80px minmax(0,1fr);gap:12px 16px}}dt{{color:#687080}}dd{{margin:0;overflow-wrap:anywhere}}.checks{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:12px}}.check{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;background:#f6f7fa;border:1px solid #e5e8ef;border-radius:9px;padding:14px 16px}}.passed strong{{color:#18714e}}.failed strong{{color:#b43c39}}.pending strong{{color:#737b89}}.table-wrap{{overflow-x:auto}}table{{width:100%;min-width:760px;border-collapse:collapse;font-size:13px}}td,th{{text-align:left;vertical-align:top;border-bottom:1px solid #e6e9f0;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere}}th{{background:#f4f6fa;color:#586174;font-weight:600;white-space:nowrap}}tbody tr:last-child td{{border-bottom:0}}td:nth-child(5),td:nth-child(6){{min-width:160px}}footer{{font-size:12px;overflow-wrap:anywhere;padding:8px 2px}}@media(max-width:640px){{body{{padding:24px 14px}}h1{{font-size:25px}}.panel{{padding:18px}}.checks{{grid-template-columns:1fr}}dl{{grid-template-columns:1fr;gap:4px}}dd{{margin-bottom:10px}}}}@media print{{body{{background:#fff;padding:0;font-size:11px}}.panel{{border-radius:0;padding:14px;margin:12px 0}}.table-wrap{{overflow:visible}}table{{min-width:0;table-layout:fixed;font-size:10px}}td,th{{padding:7px;white-space:normal}}td:nth-child(5),td:nth-child(6){{min-width:0}}tr,.check{{break-inside:avoid}}}}
</style></head><body><main>
<header><p class="tag">XMLBRIDGE / INTERCHANGE REPORT</p><h1>影视 XML 交接报告</h1>
<div class="summary"><span class="status">{html.escape(status_name)}</span><span>{html.escape(result.message)}</span></div></header>
<section class="panel"><h2>交接文件</h2><dl><dt>源文件</dt><dd>{html.escape(str(plan.source.path))}</dd><dt>目标</dt><dd>{html.escape(target_name)}</dd><dt>输出</dt><dd>{html.escape(result.output_path or '未生成转换文件')}</dd></dl></section>
<section class="panel"><h2>检查结果</h2><div class="checks">{''.join(checks)}</div><p class="muted">{html.escape(payload['host_note'])}</p></section>
<section class="panel"><h2>影响与处理 · {len(result.issues)} 项</h2><div class="table-wrap"><table><thead><tr><th>等级</th><th>项目</th><th>片段</th><th>时间</th><th>影响</th><th>详情</th><th>处理</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="7">没有发现待处理问题</td></tr>'}</tbody></table></div></section>
<footer class="muted">源文件 SHA-256：{html.escape(plan.source.input_hash)}</footer></main></body></html>"""
    (stage / "报告.html").write_text(body, encoding="utf-8")
    result.report_json = str(final / "report.json")
    result.report_html = str(final / "报告.html")


def convert(plan: ConversionPlan, decisions: dict[str, str] | None = None,
            output_dir: str | Path = ".", cancel_event=None, *, allow_risky: bool = False) -> ConversionResult:
    decisions = decisions or {}
    result = ConversionResult("failed", issues=list(plan.issues),
                              checks={"structure": "not_run", "timeline": "not_run", "host": "not_run"})
    stage = None
    try:
        check_cancel(cancel_event)
        # ConversionPlan is a public dataclass, not an authorization token.
        # Re-read and re-plan instead of trusting caller-editable tree/issues.
        original_hash = plan.source.input_hash
        fresh = inspect(plan.source.path)
        plan = plan_conversion(fresh, plan.target, plan.project_ids, allow_risky=allow_risky)
        result.issues = list(plan.issues)
        decisions = effective_decisions(result.issues, decisions, allow_risky)
        risk_export = allow_risky and plan.source.family == plan.target.family
        result.checks["risk_export"] = risk_export
        if original_hash and fresh.input_hash != original_hash:
            result.issues.append(Issue("SOURCE_CHANGED", "源文件在分析后已改变，请重新分析", "error"))
        blockers = export_blockers(result.issues, decisions, allow_risky=allow_risky,
                                   same_family=plan.source.family == plan.target.family)
        directory = Path(output_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", plan.source.path.stem).strip("_")[:60] or "timeline"
        job = f"{safe}_{plan.target.family}_{plan.target.version.replace('.', '-')}_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        final = directory / job
        stage = Path(tempfile.mkdtemp(prefix=".xmlbridge-", dir=directory))
        if blockers:
            result.status = "blocked"
            result.message = f"有 {len(blockers)} 项需要处理，未生成转换文件"
        else:
            check_cancel(cancel_event)
            tree = copy.deepcopy(plan.source.tree)
            _select(tree, plan.source.family, plan.project_ids)
            before = _timing_signature(tree, plan.source.family)
            if plan.source.family == plan.target.family:
                tree, more = _migrate(tree, plan.source.family, plan.source.version, plan.target.version,
                                      decisions, preview=allow_risky)
                result.issues = _dedupe(result.issues + more)
                timing_changed = _timing_signature(tree, plan.target.family) != before
                invalid_timing = any(i.code in {"INVALID_TIME", "INVALID_RATE", "INVALID_FRAME", "PROJECT_TIME",
                                                "SOURCE_RANGE", "TIMELINE_RANGE", "MISSING_REFERENCE"}
                                     and i.severity == "error" for i in result.issues)
                if timing_changed:
                    if not risk_export:
                        raise ValueError("迁移后的时间线范围与源文件不一致，已停止输出")
                    result.issues.append(Issue("TIMELINE_CHANGED", "转换后时间线字段有变化，请在目标软件核对", "error"))
                result.checks["timeline"] = "failed" if timing_changed or invalid_timing else "passed"
                result.checks["timeline_detail"] = ("源文件含时间或引用问题，不能确认时间线正确" if invalid_timing else
                                                    "时间线字段发生变化" if timing_changed else
                                                    "精确时间、片段顺序、资源关联及音画范围保持不变")
            else:
                from .interchange import convert_cross
                tree, more, checks = convert_cross(plan.source, plan.target, plan.project_ids, decisions)
                result.issues = _dedupe(result.issues + more)
                result.checks.update(checks)
                base = tree.getroot().get("version")
                tree, more = _migrate(tree, plan.target.family, base, plan.target.version, decisions,
                                      preview=allow_risky)
                result.issues = _dedupe(result.issues + more)
            decisions = effective_decisions(result.issues, decisions, allow_risky)
            if export_blockers(result.issues, decisions, allow_risky=allow_risky,
                               same_family=plan.source.family == plan.target.family):
                result.status = "blocked"
                result.message = "目标版本存在尚未解决的兼容问题，未生成转换文件"
            else:
                _target_container(tree, plan.target)
                bundle = plan.target.family == "fcpxml" and int(plan.target.version.split(".")[1]) >= 10
                extension = ".fcpxml" if plan.target.family == "fcpxml" else ".xml"
                relative = Path(f"{safe}.fcpxmld") / "Info.fcpxml" if bundle else Path(f"{safe}{extension}")
                _rebase_urls(tree, plan.target.family, plan.source, stage, final, relative, cancel_event)
                errors = _schema_errors(tree, plan.target.family, plan.target.version)
                semantic = _semantic_issues(tree, plan.target.family)
                checks_failed = bool(errors) or any(i.severity == "error" for i in semantic)
                if checks_failed:
                    result.checks["structure"] = "failed"
                    result.issues.extend(semantic)
                    if errors:
                        result.issues.append(Issue("OUTPUT_SCHEMA", "目标结构检查未通过", "error", details="\n".join(errors)))
                else:
                    result.checks["structure"] = "passed"
                if checks_failed and not risk_export:
                    result.status = "blocked"
                    result.message = "输出检查未通过，未生成转换文件"
                else:
                    check_cancel(cancel_event)
                    destination = stage / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    tree.write(str(destination), encoding="utf-8", xml_declaration=True, pretty_print=True,
                               doctype=f"<!DOCTYPE {plan.target.family}>")
                    result.output_path = str(final / (relative.parent if bundle else relative))
                    unresolved = [i for i in result.issues if i.unresolved(decisions)]
                    result.issues = _dedupe(result.issues)
                    if checks_failed or unresolved or result.checks["timeline"] == "failed":
                        result.status = "exported_with_issues"
                        result.message = "文件已生成；仍有兼容问题，全部记录在报告中，请在目标软件核对"
                    else:
                        result.status = "success"
                        result.message = "转换完成；结构与时间检查通过，目标软件导入待核对"
        check_cancel(cancel_event)
        _write_reports(stage, final, plan, result, decisions)
        check_cancel(cancel_event)
        stage.rename(final)
        stage = None
    except Cancelled:
        result = ConversionResult("cancelled", issues=list(plan.issues), message="已取消，未生成转换文件",
                                  checks={"structure": "not_run", "timeline": "not_run", "host": "not_run"})
    except Exception as exc:
        result.status = "failed"
        result.output_path = ""
        result.report_json = ""
        result.report_html = ""
        result.message = f"转换失败：{exc}"
        result.issues.append(Issue("CONVERT_ERROR", str(exc), "error"))
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
    return result
