"""Conservative, exact editorial interchange between FCPXML and legacy XMEML.

This module deliberately does not treat a parsed XML document as proof of a
complete project translation.  Only the supported editorial subset is emitted;
unsupported image/audio processing requires an explicit, reported decision or
blocks conversion.  All editorial times are rational seconds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .media_paths import foreign_absolute_reference, local_media_path

from lxml import etree

from .models import Document, Issue, Target


class InterchangeError(ValueError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("；".join(i.message for i in issues))


@dataclass
class ExactMarker:
    offset: Fraction
    duration: Fraction
    name: str = ""
    note: str = ""


@dataclass
class ExactMedia:
    id: str
    uri: str
    rate: Fraction
    start: Fraction
    duration: Fraction
    width: int = 0
    height: int = 0
    video: bool = True
    channels: int = 0
    sample_rate: int = 48000


@dataclass
class ExactClip:
    id: str
    name: str
    media: ExactMedia
    kind: str
    track: int
    offset: Fraction
    source: Fraction
    duration: Fraction
    channel: int = 0
    group: str = ""
    enabled: bool = True
    scale: Fraction = Fraction(1)
    opacity: Fraction = Fraction(1)
    markers: list[ExactMarker] = field(default_factory=list)
    rotation: Fraction = Fraction(0)
    volume_db: Decimal = Decimal(0)


@dataclass
class ExactTimeline:
    id: str
    name: str
    rate: Fraction
    duration: Fraction
    start: Fraction
    width: int
    height: int
    tc_format: str = "NDF"
    audio_rate: int = 48000
    clips: list[ExactClip] = field(default_factory=list)
    markers: list[ExactMarker] = field(default_factory=list)


def _text(node, path: str, default: str = "") -> str:
    value = node.findtext(path) if node is not None else None
    return value.strip() if value is not None else default


def _time(value: str | None, default: Fraction = Fraction(0)) -> Fraction:
    if value is None:
        return default
    return Fraction(value.removesuffix("s"))


def _s(value: Fraction) -> str:
    return f"{value.numerator}s" if value.denominator == 1 else f"{value}s"


def _decimal(value: Fraction) -> str:
    # Supported transform values must have an exact finite decimal expansion.
    denominator = value.denominator
    for prime in (2, 5):
        while denominator % prime == 0:
            denominator //= prime
    if denominator != 1:
        raise ValueError("变换参数不能表示为有限小数")
    from decimal import Decimal, localcontext
    with localcontext() as ctx:
        ctx.prec = max(40, len(str(abs(value.numerator))) + len(str(value.denominator)) + 8)
        result = format(Decimal(value.numerator) / Decimal(value.denominator), "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _db(value: Decimal) -> Decimal:
    if value == Decimal("-Infinity"):
        return value
    if not value.is_finite():
        raise ValueError("音量不是有限值或静音")
    return value.quantize(Decimal("0.000000001"))


def _gain(db: Decimal) -> str:
    if db == Decimal("-Infinity"):
        return "0"
    with localcontext() as ctx:
        ctx.prec = 40
        return format((db * Decimal(10).ln() / 20).exp(), ".30f").rstrip("0").rstrip(".")


def _gain_db(gain: Decimal) -> Decimal:
    if gain < 0 or not gain.is_finite():
        raise ValueError("音量增益必须是非负有限值")
    if gain == 0:
        return Decimal("-Infinity")
    with localcontext() as ctx:
        ctx.prec = 40
        return _db(Decimal(20) * gain.log10())


def _db_text(value: Decimal) -> str:
    if value == Decimal("-Infinity"):
        return "-infdB"
    text = format(_db(value), "f").rstrip("0").rstrip(".")
    return (text or "0") + "dB"


def _rate(node, default: Fraction | None = None) -> Fraction:
    local = node.find("rate") if node is not None else None
    if local is None:
        if default is None:
            raise ValueError("缺少帧率")
        return default
    base = Fraction(_text(local, "timebase"))
    return base * Fraction(1000, 1001) if _text(local, "ntsc").upper() == "TRUE" else base


def _int(value: Fraction) -> str:
    if value.denominator != 1:
        raise ValueError(f"时间不能表示为整数帧：{value}")
    return str(value.numerator)


def _sub(node, tag: str, text=None, **attrs):
    child = etree.SubElement(node, tag, **attrs)
    if text is not None:
        child.text = str(text)
    return child


def _write_rate(node, rate: Fraction):
    rate_node = _sub(node, "rate")
    if rate.denominator == 1:
        base, ntsc = rate, "FALSE"
    elif (rate * Fraction(1001, 1000)).denominator == 1:
        base, ntsc = rate * Fraction(1001, 1000), "TRUE"
    else:
        raise ValueError(f"XMEML 不支持此帧率：{rate}")
    _sub(rate_node, "timebase", _int(base))
    _sub(rate_node, "ntsc", ntsc)


def _tc(node, rate: Fraction) -> tuple[Fraction, str]:
    tc = node.find("timecode")
    if tc is None:
        return Fraction(0), "NDF"
    tc_rate = _rate(tc, rate)
    display = _text(tc, "displayformat", "NDF")
    frame = _text(tc, "frame")
    if frame:
        return Fraction(frame) / tc_rate, display
    value = _text(tc, "string")
    if not value:
        return Fraction(0), display
    pieces = value.replace(";", ":").split(":")
    if len(pieces) != 4:
        raise ValueError(f"无法解析时间码：{value}")
    h, m, s, f = map(int, pieces)
    nominal = round(tc_rate)
    frames = ((h * 3600 + m * 60 + s) * nominal) + f
    if ";" in value or display == "DF":
        if tc_rate not in (Fraction(30000, 1001), Fraction(60000, 1001)):
            raise ValueError("此帧率不支持 drop-frame 时间码")
        drop = 2 if nominal == 30 else 4
        minutes = h * 60 + m
        if m % 10 and s == 0 and f < drop:
            raise ValueError("drop-frame 时间码使用了被跳过的帧号")
        frames -= drop * (minutes - minutes // 10)
        display = "DF"
    return Fraction(frames) / tc_rate, display


def _write_tc(node, start: Fraction, rate: Fraction, display: str = "NDF"):
    tc = _sub(node, "timecode")
    _write_rate(tc, rate)
    _sub(tc, "frame", _int(start * rate))
    _sub(tc, "displayformat", display)


class _Reader:
    def __init__(self, document: Document, project_ids: list[str] | None = None):
        self.document = document
        self.root = document.tree.getroot()
        self.issues: list[Issue] = []
        self.selected = None if project_ids is None else set(project_ids)
        self.ids = {n.get("id"): n for n in self.root.iter() if n.get("id") and (len(n) or len(n.attrib) > 1)}
        self.media_cache: dict[str, ExactMedia] = {}
        self.clip_nodes: dict[str, etree._Element] = {}
        self.current: ExactTimeline | None = None

    def issue(self, code: str, message: str, node=None, *, clip: str = "", choices=None, severity="error", details=""):
        if node is None and clip in self.clip_nodes:
            node = self.clip_nodes[clip]
        location = self.document.tree.getpath(node) if node is not None else ""
        resource = node is not None and any(parent.tag == "resources" for parent in node.iterancestors())
        project = "共享资源" if resource else self.current.name if self.current else ""
        time = ""
        if node is not None and not resource:
            story_tags = {"asset-clip", "audio", "video", "ref-clip", "mc-clip", "sync-clip", "clip", "audition", "gap", "title", "caption", "transition", "clipitem", "transitionitem", "generatoritem"}
            current = next((p for p in [node, *node.iterancestors()] if p.tag in story_tags and (self.document.family == "fcpxml" or p.tag in {"clipitem", "transitionitem", "generatoritem"})), None)
            if current is not None:
                clip = current.get("name") or _text(current, "name") or clip or current.get("id", "") or current.tag
                try:
                    if self.document.family == "fcpxml":
                        offset = _time(current.get("offset"))
                        # Connected items are expressed in their parent's source coordinates.
                        for parent in current.iterancestors():
                            if parent.tag in story_tags:
                                offset += _time(parent.get("offset")) - _time(parent.get("start"))
                        time = _s(offset)
                    elif self.current is not None:
                        time = _s(self.current.start + Fraction(_text(current, 'start', '0')) / self.current.rate)
                except (ValueError, ZeroDivisionError, TypeError):
                    time = "时间参数无法解析；见 XML 路径"
        item = Issue(code, message, severity, project, clip, time,
                     location=location, choices=choices or {}, details=details)
        self.issues.append(item)
        return item

    def loss(self, code: str, message: str, node, *, effect=False):
        action = "drop_effect" if effect else "drop_metadata"
        return self.issue(code, message, node, choices={action: "明确移除此处理；画面或声音可能改变" if effect else "不保留此元数据", "stop": "停止转换"})

    def uri(self, value: str, node) -> str:
        if not value:
            self.issue("MEDIA_URI_MISSING", "媒体引用没有路径，无法建立跨格式引用", node)
            return ""
        parsed = urlsplit(value)
        if foreign_absolute_reference(value):
            self.issue("MEDIA_FOREIGN_PATH", "素材使用其他系统的绝对路径；保留原引用，交接后需重新链接", node, severity="warning", details=value)
            return value
        path = local_media_path(value, self.document.xml_path or self.document.path)
        if path is None and parsed.scheme and parsed.scheme != "file":
            self.issue("MEDIA_URI_SCHEME", f"当前跨格式转换只接受本地媒体 URI：{parsed.scheme}", node)
            return value
        if path is None:
            self.issue("MEDIA_URI_HOST", "当前不支持带远程主机的 file URI", node)
            return value
        if not path.exists():
            self.issue("MEDIA_OFFLINE", f"媒体离线：{path.name}；仍可转换时间线", node, severity="warning", details=str(path))
        return path.as_uri()

    def read(self) -> list[ExactTimeline]:
        family = self.document.family
        if family == "fcpxml":
            projects = self.root.xpath(".//project")
            if not projects:
                projects = self.root.findall("sequence")
        elif family == "xmeml":
            projects = self.root.xpath(".//sequence[not(ancestor::sequence)]")
        else:
            self.issue("CROSS_FAMILY", f"未知 XML 家族：{family}")
            return []
        result = []
        available = {f"p{i}" for i in range(len(projects))}
        if self.selected is not None and (not self.selected or self.selected - available):
            self.issue("PROJECT_SELECTION", "项目选择为空或包含不存在的项目 ID", details=", ".join(sorted(self.selected - available)))
            return []
        for index, project in enumerate(projects):
            pid = f"p{index}"
            if self.selected is not None and pid not in self.selected:
                continue
            try:
                timeline = self.read_fcpx(project, pid) if family == "fcpxml" else self.read_xmeml(project, pid)
                if timeline is not None:
                    self.validate(timeline)
                    result.append(timeline)
            except (ValueError, TypeError, ZeroDivisionError, KeyError, AttributeError) as exc:
                self.issue("CROSS_PARSE", f"无法精确解析此项目：{exc}", project)
        if not projects:
            self.issue("NO_TIMELINE", "没有可转换的项目时间线")
        return result

    def validate(self, timeline: ExactTimeline):
        if timeline.rate <= 0 or timeline.duration < 0:
            self.issue("TIMELINE_RANGE", "帧率必须大于 0，时间线时长不能为负")
        if timeline.width <= 0 or timeline.height <= 0:
            self.issue("FRAME_SIZE_MISSING", "项目缺少可靠的画面宽高，不能猜测目标画幅")
        if timeline.audio_rate not in (32000, 44100, 48000, 88200, 96000, 176400, 192000):
            self.issue("AUDIO_SAMPLE_RATE", f"项目音频采样率 {timeline.audio_rate} 尚不能可靠映射")
        for clip in timeline.clips:
            source_node = self.clip_nodes.get(clip.id)
            remapped = source_node is not None and source_node.find("timeMap") is not None
            if clip.offset < 0 or clip.duration <= 0 or clip.source < 0 and not remapped:
                self.issue("CLIP_RANGE", f"片段 {clip.name} 存在负起点或非正时长", clip=clip.id)
            if clip.offset + clip.duration > timeline.duration:
                self.issue("CLIP_OUTSIDE_TIMELINE", f"片段 {clip.name} 超出项目声明时长", clip=clip.id)
            if not remapped and clip.media.duration and clip.source + clip.duration > clip.media.duration:
                self.issue("SOURCE_RANGE", f"片段 {clip.name} 超出媒体源范围", clip=clip.id)
            if clip.media.duration <= 0:
                self.issue("MEDIA_DURATION_MISSING", f"片段 {clip.name} 的媒体没有正时长；不能猜测 still frame 或未知时长语义", clip=clip.id)
            if clip.media.channels > 2:
                self.issue("AUDIO_CHANNEL_LAYOUT", f"片段 {clip.name} 的 {clip.media.channels} 声道布局尚不能可靠映射", clip=clip.id)
            if clip.kind == "video" and (clip.media.width <= 0 or clip.media.height <= 0):
                self.issue("MEDIA_FRAME_SIZE_MISSING", f"片段 {clip.name} 没有可靠的源画幅；不能把项目画幅猜作素材画幅", clip=clip.id)
            for marker in clip.markers:
                if marker.offset < 0 or marker.duration < 0 or marker.offset + marker.duration > clip.duration:
                    self.issue("CLIP_MARKER_RANGE", f"片段 {clip.name} 的 marker 超出已用片段范围，当前不猜测隐藏源 marker 的映射", clip=clip.id)
        for marker in timeline.markers:
            if marker.offset < 0 or marker.duration < 0 or marker.offset + marker.duration > timeline.duration:
                self.issue("TIMELINE_MARKER_RANGE", "时间线 marker 超出项目声明范围")
        for kind in ("video", "audio"):
            tracks = sorted({c.track for c in timeline.clips if c.kind == kind})
            mapping = {track: index for index, track in enumerate(tracks)}
            for clip in timeline.clips:
                if clip.kind == kind:
                    clip.track = mapping[clip.track]
            for track in mapping.values():
                clips = sorted((c for c in timeline.clips if c.kind == kind and c.track == track), key=lambda c: c.offset)
                for previous, clip in zip(clips, clips[1:]):
                    if previous.offset + previous.duration > clip.offset:
                        self.issue("TRACK_OVERLAP", f"{kind} 轨道上片段重叠；当前不将其隐式解释为转场", clip=clip.id)

    def fcpx_format(self, ident: str) -> tuple[Fraction, int, int]:
        node = self.ids.get(ident)
        if node is None or node.tag != "format":
            raise ValueError(f"无法找到 format 资源 {ident}")
        duration = _time(node.get("frameDuration"))
        if duration <= 0:
            raise ValueError("format 缺少正 frameDuration")
        for key, default in (("paspH", "1"), ("paspV", "1"), ("fieldOrder", "progressive")):
            if node.get(key, default) != default:
                self.issue("FORMAT_GEOMETRY", f"暂不支持非方形像素或交错扫描：{key}={node.get(key)}", node)
        if node.get("colorSpace"):
            self.loss("COLOR_METADATA", "目标 XMEML 不保留 FCPXML colorSpace；需要明确接受颜色元数据损失", node)
        if node.get("projection", "none") != "none" or node.get("stereoscopic", "mono") != "mono":
            self.issue("SPATIAL_FORMAT", "暂不支持 360° 或立体格式跨格式映射", node)
        return 1 / duration, int(node.get("width", "0")), int(node.get("height", "0"))

    def fcpx_media(self, ref: str, timeline: ExactTimeline) -> ExactMedia:
        if ref in self.media_cache:
            return self.media_cache[ref]
        node = self.ids.get(ref)
        if node is None or node.tag != "asset":
            raise ValueError(f"{ref} 不是可直接引用的 asset；嵌套或生成器尚未映射")
        fmt = node.get("format")
        rate, width, height = self.fcpx_format(fmt) if fmt else (timeline.rate, timeline.width, timeline.height)
        if node.get("hasVideo") == "1" and not fmt:
            self.issue("MEDIA_FORMAT_MISSING", "视频媒体没有 format 资源，不能猜测源帧率或源画幅", node)
        replicas = node.findall("media-rep")
        source = node.get("src") or next((n.get("src") for n in replicas if n.get("kind", "original-media") == "original-media"), "")
        if not source and replicas:
            self.issue("MEDIA_REPRESENTATION", "媒体只有代理或其他表示，不能自动改用其作为原始媒体", node)
        channels = int(node.get("audioChannels", "2" if node.get("hasAudio") == "1" else "0"))
        sample = node.get("audioRate", "48k")
        sample_rate = int(Fraction(sample.removesuffix("k")) * (1000 if sample.endswith("k") else 1))
        media = ExactMedia(ref, self.uri(source, node), rate, _time(node.get("start")),
                           _time(node.get("duration")), width, height, node.get("hasVideo", "0") == "1", channels, sample_rate)
        if int(node.get("audioSources", "1")) > 1 or int(node.get("videoSources", "1")) > 1:
            self.issue("MULTIPLE_MEDIA_STREAMS", "当前不支持同一资源的多视频流或多个音频源轨", node)
        known = {"id", "name", "uid", "src", "start", "duration", "hasVideo", "hasAudio", "format", "audioSources", "videoSources", "audioChannels", "audioRate", "customLUTOverride", "colorSpaceOverride", "projectionOverride", "stereoscopicOverride"}
        for key in set(node.attrib) - known:
            self.issue("ASSET_ATTRIBUTE", f"尚未映射 asset 属性 {key}；不能自动忽略媒体解释信息", node)
        for key in ("customLUTOverride", "colorSpaceOverride", "projectionOverride", "stereoscopicOverride"):
            if node.get(key):
                self.loss("ASSET_INTERPRETATION", f"不保留媒体解释参数 {key}={node.get(key)}", node, effect=True)
        for child in node:
            if child.tag == "metadata":
                self.loss("ASSET_METADATA", "目标 XMEML 不保留资源的 FCPXML 相机或用户 metadata", child)
        if node.find(".//bookmark") is not None:
            self.issue("BOOKMARK_NOT_TRANSFERRED", "macOS bookmark 不跨格式传递；目标使用已解析的绝对媒体 URI", node, severity="warning")
        self.media_cache[ref] = media
        return media

    def fcpx_markers(self, node, origin: Fraction) -> list[ExactMarker]:
        result = []
        for marker in node.findall("marker"):
            result.append(ExactMarker(_time(marker.get("start")) - origin, _time(marker.get("duration")), marker.get("value", ""), marker.get("note", "")))
            if marker.get("completed") is not None:
                self.loss("MARKER_TASK_STATE", "XMEML 不保留 marker 的待办完成状态", marker)
        return result

    def fcpx_adjustments(self, node) -> tuple[Fraction, Fraction, Fraction]:
        scale, opacity, rotation = Fraction(1), Fraction(1), Fraction(0)
        transform = node.find("adjust-transform")
        if transform is not None and transform.get("enabled", "1") == "1":
            xy = [Fraction(n) for n in transform.get("scale", "1 1").split()]
            position = [Fraction(n) for n in transform.get("position", "0 0").split()]
            anchor = [Fraction(n) for n in transform.get("anchor", "0 0").split()]
            simple = len(xy) == 2 and xy[0] == xy[1] and xy[0] > 0
            simple = simple and len(position) == 2 and all(n == 0 for n in position)
            simple = simple and len(anchor) == 2 and all(n == 0 for n in anchor)
            simple = simple and not len(transform)
            if simple and not set(transform.attrib) - {"enabled", "scale", "position", "anchor", "rotation"}:
                scale = xy[0]
                rotation = Fraction(transform.get("rotation", "0"))
            else:
                self.loss("TRANSFORM_UNSUPPORTED", "只支持中心原点的静态等比缩放和旋转；此位移、锚点、非等比缩放或动画需要明确移除", transform, effect=True)
        blend = node.find("adjust-blend")
        if blend is not None:
            if blend.get("mode", "normal") in ("normal", "1") and not len(blend) and not set(blend.attrib) - {"mode", "amount"}:
                opacity = Fraction(blend.get("amount", "1"))
                if not 0 <= opacity <= 1:
                    self.issue("OPACITY_RANGE", "不透明度必须在 0 到 1 之间", blend)
            else:
                self.loss("BLEND_UNSUPPORTED", "当前不支持此混合模式或不透明度动画", blend, effect=True)
        return scale, opacity, rotation

    def fcpx_volume(self, node) -> Decimal:
        volume = node.find("adjust-volume")
        if volume is None:
            return Decimal(0)
        amount = volume.get("amount", "0dB")
        if amount.lower() in ("-infdB".lower(), "-inf db") and not len(volume) and set(volume.attrib) <= {"amount"}:
            return Decimal("-Infinity")
        try:
            db = _db(Decimal(amount.removesuffix("dB")))
            simple = not len(volume) and set(volume.attrib) <= {"amount"} and Decimal(-120) <= db <= Decimal(12)
        except (InvalidOperation, ValueError):
            simple = False
        if simple:
            return db
        self.loss("AUDIO_VOLUME_UNSUPPORTED", "只支持 -120 至 +12 dB 或静音的静态整体音量；音量动画或其他范围需要明确移除", volume, effect=True)
        return Decimal(0)

    def fcpx_retime(self, node):
        """Diagnose timing without pretending a speed filter is a normal edit."""
        # Native Resolve 21.1.1 25fps exports already differ between dialects
        # at retime boundaries. A two-keyframe rebasing experiment matched
        # 200/50/-100/0% at N=50 with special endpoint corrections, but changed
        # PitchCorrection=False to True. Those host observations establish no
        # general, lossless rule; retain this blocker rather than special-case N.
        if node.tag == "conform-rate":
            details = ", ".join(f"{key}={value}" for key, value in sorted(node.attrib.items()))
            self.issue("RETIME_UNSUPPORTED", "conform-rate 会改变素材帧率解释；当前没有已验证的跨格式映射", node, details=details)
            return
        try:
            points = [(_time(p.get("time")), _time(p.get("value")), p.get("interp", "smooth2")) for p in node.findall("timept")]
            if len(points) < 2:
                raise ValueError("至少需要两个 timept 才能确定映射斜率")
            slopes = []
            for a, b in zip(points, points[1:]):
                if b[0] <= a[0]:
                    raise ValueError("timept 的目标时间必须严格递增")
                slopes.append((b[1] - a[1]) / (b[0] - a[0]))
            parent = node.getparent()
            used_start = _time(parent.get("start"))
            used_end = used_start + _time(parent.get("duration"))
            if used_start < points[0][0] or used_end > points[-1][0] or used_end <= used_start:
                raise ValueError("片段已用区间没有被 timeMap 完整覆盖，不能猜测外推")
            active = [(a, b, slope) for a, b, slope in zip(points, points[1:], slopes) if max(a[0], used_start) < min(b[0], used_end)]
            linear = all(a[2] == "linear" and b[2] == "linear" for a, b, _ in active)
            constant = linear and len({slope for _, _, slope in active}) == 1
            slope = active[0][2]
            kind = "定格" if constant and slope == 0 else "倒放" if constant and slope < 0 else "恒速" if constant else "曲线或分段变速"
            speed = f"，源/目标时间斜率 {slope}（{slope * 100}%）" if constant else ""
            details = "; ".join(f"time={_s(t)}, source={_s(v)}, interp={i}" for t, v, i in points)
            details += "; segment_slopes=" + ",".join(str(value) for value in slopes)
            details += f"; used_range={_s(used_start)}..{_s(used_end)}; used_slopes=" + ",".join(str(s) for _, _, s in active)
            if linear:
                first_a = active[0][0]
                last_a, _, last_slope = active[-1]
                first_source = first_a[1] + (used_start - first_a[0]) * slope
                last_source = last_a[1] + (used_end - last_a[0]) * last_slope
                details += f"; mapped_source_range={_s(first_source)}..{_s(last_source)}"
            details += "; " + ", ".join(f"{key}={value}" for key, value in sorted(node.attrib.items()))
            self.issue("RETIME_UNSUPPORTED", f"检测到{kind}{speed}；当前没有已验证的跨格式映射，不能作为普通片段导出", node, details=details)
        except (ValueError, ZeroDivisionError, TypeError) as exc:
            self.issue("RETIME_UNSUPPORTED", f"无法可靠求值此时间映射：{exc}；转换已阻止", node)

    def read_fcpx(self, project, pid: str) -> ExactTimeline | None:
        sequence = project if project.tag == "sequence" else project.find("sequence")
        if sequence is None:
            self.issue("PROJECT_SEQUENCE_MISSING", "项目没有 sequence", project)
            return None
        rate, width, height = self.fcpx_format(sequence.get("format", ""))
        tl = ExactTimeline(pid, project.get("name", "Timeline"), rate, _time(sequence.get("duration")),
                           _time(sequence.get("tcStart")), width, height, sequence.get("tcFormat", "NDF"))
        audio_rate = sequence.get("audioRate", "48k")
        tl.audio_rate = int(Fraction(audio_rate.removesuffix("k")) * (1000 if audio_rate.endswith("k") else 1))
        self.current = tl
        if sequence.get("audioLayout", "stereo") not in ("mono", "stereo"):
            self.issue("AUDIO_LAYOUT", "只支持 mono/stereo 项目音频布局", sequence)
        spine = sequence.find("spine")
        if spine is None:
            self.issue("SPINE_MISSING", "sequence 没有主 storyline", sequence)
            return tl
        audio_track_keys: dict[tuple, int] = {}
        count = 0

        def audio_track(key):
            if key not in audio_track_keys:
                audio_track_keys[key] = len(audio_track_keys)
            return audio_track_keys[key]

        def visit(node, parent_offset: Fraction, parent_source: Fraction, inherited_lane: int = 0, parent_enabled=True):
            nonlocal count
            tag = node.tag
            if not isinstance(tag, str):
                return
            structural = {"ref-clip": "嵌套/复合片段", "mc-clip": "多机位", "sync-clip": "同步片段", "clip": "复合 clip", "audition": "audition", "spine": "次级 storyline", "transition": "转场", "title": "标题", "caption": "字幕"}
            if tag in structural:
                self.issue("UNSUPPORTED_" + tag.upper().replace("-", "_"), f"当前没有可靠的 {structural[tag]} 跨格式映射", node)
                return
            if tag not in ("gap", "asset-clip", "video", "audio"):
                self.issue("UNKNOWN_STORY_ELEMENT", f"尚未映射时间线元素 {tag}", node)
                return
            count += 1
            ident = f"{pid}c{count}"
            offset = parent_offset + _time(node.get("offset")) - parent_source
            source = _time(node.get("start"))
            lane = int(node.get("lane", str(inherited_lane)))
            enabled = parent_enabled and node.get("enabled", "1") != "0"
            known = {"name", "ref", "offset", "start", "duration", "lane", "enabled", "srcEnable", "srcID", "srcCh", "outCh", "audioStart", "audioDuration", "format", "tcStart", "tcFormat", "modDate", "audioRole", "videoRole", "role"}
            for key in set(node.attrib) - known:
                self.issue("CLIP_ATTRIBUTE", f"尚未映射片段属性 {key}，不能自动忽略", node, clip=ident)
            if tag == "gap":
                duration = _time(node.get("duration"))
                tl.markers.extend(ExactMarker(offset + m.offset, m.duration, m.name, m.note) for m in self.fcpx_markers(node, source))
            else:
                media = self.fcpx_media(node.get("ref", ""), tl)
                if node.get("format") and node.get("format") != self.ids[node.get("ref")].get("format"):
                    self.issue("CLIP_FORMAT_OVERRIDE", "片段 format 覆盖与媒体资源不同，当前不猜测其空间解释", node)
                if tag == "video" and node.get("srcID", "1") != "1":
                    self.issue("VIDEO_STREAM_SELECTION", "当前不支持非默认视频源轨选择", node)
                source = _time(node.get("start"), media.start)
                duration = _time(node.get("duration"), media.duration - (source - media.start))
                scale, opacity, rotation = self.fcpx_adjustments(node)
                volume_db = self.fcpx_volume(node)
                markers = self.fcpx_markers(node, source)
                use = node.get("srcEnable", "all")
                video = tag == "video" or (tag == "asset-clip" and media.video and use != "audio")
                audio = tag == "audio" or (tag == "asset-clip" and media.channels and use != "video")
                group = ident if video and audio else ""
                if video:
                    tl.clips.append(ExactClip(ident + "v", node.get("name", "Clip"), media, "video", lane, offset, source - media.start, duration, group=group, enabled=enabled, scale=scale, opacity=opacity, markers=list(markers), rotation=rotation))
                    self.clip_nodes[ident + "v"] = node
                if audio:
                    channels = [int(n) for n in node.get("srcCh", ",".join(str(n + 1) for n in range(media.channels))).split(",") if n]
                    if not channels:
                        self.issue("AUDIO_CHANNELS_MISSING", "音频片段没有可确定的源声道", node)
                    if node.get("outCh") or node.get("srcID", "1") != "1":
                        self.issue("AUDIO_ROUTING", "当前不支持自定义音频输出路由或多音轨源", node)
                    for channel in channels:
                        if channel < 1 or channel > media.channels:
                            self.issue("AUDIO_CHANNEL_INDEX", "源音频声道索引超出媒体声明范围", node)
                        key = ("embedded" if video else "audio", lane, channel)
                        tl.clips.append(ExactClip(ident + f"a{channel}", node.get("name", "Clip"), media, "audio", audio_track(key), offset, source - media.start, duration, channel, group, enabled, markers=list(markers), volume_db=volume_db))
                        self.clip_nodes[ident + f"a{channel}"] = node
                if node.get("audioStart") is not None or node.get("audioDuration") is not None:
                    self.issue("SPLIT_EDIT", "当前不支持 audioStart/audioDuration 的 J/L split edit，不能自动合并音画范围", node)
                if any(node.get(key) for key in ("audioRole", "videoRole", "role")):
                    self.loss("ROLE_METADATA", "目标 XMEML 不保留 FCPXML 音视频角色", node)
            allowed = {"gap", "asset-clip", "video", "audio", "marker", "note", "adjust-transform", "adjust-blend"}
            for child in node:
                if child.tag in ("gap", "asset-clip", "video", "audio") or child.tag in structural:
                    visit(child, offset, source, lane, enabled)
                elif child.tag in ("timeMap", "conform-rate"):
                    self.fcpx_retime(child)
                elif child.tag in ("audio-channel-source", "audio-role-source", "sync-source"):
                    self.issue("AUDIO_COMPONENTS", "当前不支持音频组件启用、混音或通道重排", child)
                elif child.tag == "adjust-conform" and (child.get("type", "fit") == "none" or child.get("type", "fit") in ("fit", "fill") and tag != "gap" and (media.width, media.height) == (width, height)):
                    pass
                elif child.tag == "adjust-volume":
                    pass
                elif child.tag == "adjust-colorConform" and (child.get("enabled", "1") == "0" or child.get("autoOrManual", "automatic") == "manual" and child.get("conformType", "conformNone") == "conformNone"):
                    pass
                elif child.tag in ("note", "metadata", "keyword", "rating", "chapter-marker", "analysis-marker"):
                    self.loss("CLIP_METADATA", f"不保留 {child.tag} 元数据", child)
                elif child.tag not in allowed:
                    self.loss("UNSUPPORTED_PROCESSING", f"尚未映射 {child.tag}，需要明确移除此处理", child, effect=True)
            if tag != "gap" and (media.width != width or media.height != height):
                # Different-size clips require a known spatial-conform policy.
                conform = node.find("adjust-conform")
                if video and (conform is None or conform.get("type", "fit") != "none"):
                    self.issue("SPATIAL_CONFORM", "源画幅与项目不同；需要显式 adjust-conform=none 才能保持原像素缩放语义", node)

        for child in spine:
            visit(child, Fraction(0), tl.start)
        for node in (project, sequence):
            for child in node:
                if child.tag not in ("sequence", "spine"):
                    self.loss("PROJECT_METADATA", f"不保留项目 {child.tag} 元数据", child)
        for key in set(sequence.attrib) - {"format", "duration", "tcStart", "tcFormat", "audioLayout", "audioRate", "renderFormat"}:
            self.loss("SEQUENCE_ATTRIBUTE", f"不保留项目属性 {key}={sequence.get(key)}", sequence, effect=key == "renderColorSpace")
        return tl

    def xmeml_markers(self, node, rate: Fraction) -> list[ExactMarker]:
        markers = []
        for child in node.findall("marker"):
            start = Fraction(_text(child, "in", "0")) / rate
            out = Fraction(_text(child, "out", "-1")) / rate
            duration = max(Fraction(0), out - start) if out >= 0 else Fraction(0)
            markers.append(ExactMarker(start, duration, _text(child, "name"), _text(child, "comment")))
            if child.find("color") is not None or child.find("marker") is not None:
                self.loss("MARKER_METADATA", "FCPXML 不保留此 marker 颜色或嵌套状态", child)
        return markers

    def xmeml_media(self, file, timeline: ExactTimeline) -> ExactMedia:
        ref = file.get("id", "")
        if file.find("pathurl") is None and ref in self.ids:
            file = self.ids[ref]
        key = ref or self.document.tree.getpath(file)
        if key in self.media_cache:
            return self.media_cache[key]
        rate = _rate(file, timeline.rate)
        start, _ = _tc(file, rate)
        sample = file.find("media/video/samplecharacteristics")
        width = int(_text(sample, "width", _text(file, "width", "0")))
        height = int(_text(sample, "height", _text(file, "height", "0")))
        audio = file.find("media/audio")
        if len(file.findall("media/audio")) > 1 or len(file.findall("media/video")) > 1:
            self.issue("MULTIPLE_MEDIA_STREAMS", "当前不支持一个 file 内的多个音视频源轨", file)
        channels = int(_text(audio, "channelcount", "0")) if audio is not None else 0
        if audio is not None:
            labels = audio.findall("audiochannel")
            for index, label in enumerate(labels):
                if _text(label, "channellabel", "left" if index == 0 else "right") != ("left" if index == 0 else "right") or _text(label, "sourcechannel", str(index + 1)) != str(index + 1):
                    self.issue("AUDIO_CHANNEL_MAPPING", "当前不支持媒体 audiochannel 声道重排或离散布局", label)
            if _text(audio, "layout", "stereo") not in ("mono", "stereo"):
                self.issue("AUDIO_CHANNEL_LAYOUT", "当前只支持 mono/stereo 媒体声道布局", audio)
        media = ExactMedia(key, self.uri(_text(file, "pathurl"), file), rate, start,
                           Fraction(_text(file, "duration", "0")) / rate, width, height,
                           file.find("media/video") is not None, channels,
                           int(_text(audio, "samplecharacteristics/samplerate", "48000")))
        if file.find("mediaSource") is not None:
            self.issue("GENERATOR_SOURCE", "内建生成器 mediaSource 没有可引用媒体", file)
        self.media_cache[key] = media
        return media

    def xmeml_effects(self, node, kind: str) -> tuple[Fraction, Fraction, Fraction, Decimal]:
        scale, opacity, rotation, volume_db = Fraction(1), Fraction(1), Fraction(0), Decimal(0)
        for effect_filter in node.findall("filter"):
            if _text(effect_filter, "enabled", "TRUE").upper() == "FALSE":
                continue
            effect = effect_filter.find("effect")
            effect_id = _text(effect, "effectid").lower()
            if "speed" in effect_id or "timeremap" in effect_id or "time remap" in _text(effect, "name").lower():
                parameters = [f"{_text(p, 'parameterid', '?')}={_text(p, 'value', '(complex)')}" + (" [animated]" if p.find("keyframe") is not None else "") for p in effect.findall("parameter")]
                self.issue("RETIME_UNSUPPORTED", "检测到 XMEML 变速效果；当前没有已验证的源范围、速度与插值联合映射", effect_filter, details=f"effectid={effect_id}; " + "; ".join(parameters))
                continue
            filter_simple = not any(child.tag not in ("effect", "enabled", "start", "end") for child in effect_filter) and len(effect_filter.findall("effect")) == 1
            filter_start, filter_end = _text(effect_filter, "start", "-1"), _text(effect_filter, "end", "-1")
            filter_simple = filter_simple and (filter_start == "-1" or Fraction(filter_start) <= Fraction(_text(node, "in", "0"))) and (filter_end == "-1" or Fraction(filter_end) >= Fraction(_text(node, "out", "0")))
            effect_simple = effect is not None and not any(child.tag not in ("name", "effectid", "effectcategory", "effecttype", "mediatype", "parameter") for child in effect)
            if kind == "audio" and effect_id == "audiolevels":
                parameters = effect.findall("parameter")
                simple = filter_simple and effect_simple and len(parameters) == 1 and _text(parameters[0], "parameterid") == "level" and parameters[0].find("keyframe") is None
                simple = simple and not any(child.tag not in ("parameterid", "name", "value", "valuemin", "valuemax") for child in parameters[0])
                value = parameters[0].find("value") if simple else None
                simple = simple and value is not None and not len(value)
                try:
                    level_db = _gain_db(Decimal(value.text or "0")) if simple else None
                    simple = simple and (level_db == Decimal("-Infinity") or Decimal(-120) <= level_db <= Decimal(12))
                except (InvalidOperation, ValueError):
                    simple = False
                if simple:
                    combined = _db(volume_db + level_db)
                    if combined != Decimal("-Infinity") and not Decimal(-120) <= combined <= Decimal(12):
                        self.loss("AUDIO_VOLUME_UNSUPPORTED", "多个 Audio Levels 合成音量超出 -120 至 +12 dB，不能隐式截断", effect_filter, effect=True)
                    else:
                        volume_db = combined
                else:
                    self.loss("AUDIO_VOLUME_UNSUPPORTED", "Audio Levels 含音量动画、未知参数或超出支持范围，需要明确移除", effect_filter, effect=True)
                continue
            if kind != "video" or effect_id not in ("basic", "basicmotion", "basic motion", "opacity"):
                self.loss("UNSUPPORTED_EFFECT", f"尚未映射效果 {_text(effect, 'name', effect_id or '未知')}，需要明确移除", effect_filter, effect=True)
                continue
            supported = filter_simple and effect_simple
            this_scale, this_opacity, this_rotation = Fraction(1), Fraction(1), Fraction(0)
            seen_parameters = set()
            for parameter in effect.findall("parameter"):
                ident = _text(parameter, "parameterid").lower()
                value = parameter.find("value")
                if ident in seen_parameters or value is None or any(child.tag not in ("parameterid", "name", "value", "valuemin", "valuemax") for child in parameter):
                    supported = False
                    break
                seen_parameters.add(ident)
                if ident not in ("center", "anchor", "anchorpoint", "centeroffset") and len(value):
                    supported = False
                    break
                if ident == "scale":
                    this_scale = Fraction(value.text or "100") / 100
                    supported &= this_scale > 0
                elif ident == "opacity":
                    this_opacity = Fraction(value.text or "100") / 100
                    supported &= 0 <= this_opacity <= 1
                elif ident == "rotation":
                    # Resolve 21.1.1 native export of RotationAngle=+30 writes
                    # FCPXML +30, but XMEML Basic Motion -30.
                    this_rotation = -Fraction(value.text or "0")
                elif ident == "aspect":
                    supported &= Fraction(value.text or "0") == 0
                elif ident in ("center", "anchor", "anchorpoint", "centeroffset"):
                    supported &= all(n.tag in ("horiz", "vert") for n in value) and not any(Fraction(n.text or "0") != 0 for n in value)
                else:
                    supported = False
            if supported:
                scale, opacity, rotation = scale * this_scale, opacity * this_opacity, rotation + this_rotation
            else:
                self.loss("TRANSFORM_UNSUPPORTED", "只支持 Basic Motion 中心原点的静态等比缩放、旋转和不透明度；此效果含其他参数或动画", effect_filter, effect=True)
        return scale, opacity, rotation, volume_db

    def read_xmeml(self, sequence, pid: str) -> ExactTimeline:
        rate = _rate(sequence)
        start, display = _tc(sequence, rate)
        sample = sequence.find("media/video/format/samplecharacteristics")
        tl = ExactTimeline(pid, _text(sequence, "name", "Timeline"), rate,
                           Fraction(_text(sequence, "duration", "0")) / rate, start,
                           int(_text(sample, "width", "0")), int(_text(sample, "height", "0")), display)
        tl.audio_rate = int(_text(sequence, "media/audio/format/samplecharacteristics/samplerate", _text(sequence, "media/audio/samplecharacteristics/samplerate", "48000")))
        self.current = tl
        tl.markers = self.xmeml_markers(sequence, rate)
        for node in sequence.xpath("./media/video/format/samplecharacteristics | .//file/media/video/samplecharacteristics | ./media/video/track/clipitem | ./media/audio/track/clipitem"):
            if _text(node, "pixelaspectratio", "square") not in ("square", "1") or _text(node, "fielddominance", "none") not in ("none", "progressive") or _text(node, "anamorphic", "FALSE").upper() == "TRUE":
                self.issue("FORMAT_GEOMETRY", "当前不支持非方形像素、anamorphic 或交错格式", node)
        if sequence.find("filter") is not None or sequence.find("media/audio/filter") is not None:
            self.issue("SEQUENCE_PROCESSING", "当前不支持 sequence 或音频总线处理", sequence)
        outputs = sequence.find("media/audio/outputs")
        if outputs is not None:
            groups = outputs.findall("group")
            conventional = len(groups) == 1 and _text(groups[0], "index", "1") == "1" and _text(groups[0], "numchannels", "2") == "2" and _text(groups[0], "downmix", "0") == "0" and [_text(c, "index") for c in groups[0].findall("channel")] == ["1", "2"]
            if not conventional:
                self.issue("AUDIO_OUTPUT_ROUTING", "当前只支持无 downmix 的标准 1/2 立体声总线", outputs)
        groups = {}
        links = {}
        for kind in ("video", "audio"):
            for track_index, track in enumerate(sequence.findall(f"media/{kind}/track")):
                if track.find("filter") is not None:
                    self.issue("TRACK_PROCESSING", "当前不支持轨道级音视频处理", track)
                for node in track:
                    if node.tag in ("transitionitem", "generatoritem"):
                        self.issue("TRANSITION_UNSUPPORTED" if node.tag == "transitionitem" else "TITLE_GENERATOR_UNSUPPORTED", "当前不支持转场" if node.tag == "transitionitem" else "当前不支持标题或生成器", node)
                    elif node.tag not in ("clipitem", "enabled", "locked", "name", "outputchannelindex"):
                        self.loss("TRACK_METADATA", f"不保留轨道 {node.tag}", node)
                for index, node in enumerate(track.findall("clipitem")):
                    ident = node.get("id", f"{pid}{kind}{track_index}c{index}")
                    if node.find("sequence") is not None or node.find("multiclip") is not None:
                        self.issue("NESTED_MULTICAM", "当前不支持嵌套 sequence 或 multiclip", node, clip=ident)
                        continue
                    for key in ("subframeoffset", "mixedratesoffset", "mediadelay", "stillframeoffset", "syncoffset"):
                        if Fraction(_text(node, key, "0")) != 0:
                            self.issue("OFFSET_UNSUPPORTED", f"当前没有可靠的 {key} 映射", node, clip=ident)
                    if _text(node, "stillframe", "FALSE").upper() == "TRUE":
                        self.issue("STILL_FRAME_UNSUPPORTED", "当前不支持 stillframe 时长或冻结帧语义", node, clip=ident)
                    if _text(node, "compositemode", "normal") != "normal":
                        self.loss("BLEND_UNSUPPORTED", "当前不支持此 XMEML compositemode", node, effect=True)
                    clip_rate = _rate(node, rate)
                    offset = Fraction(_text(node, "start", "0")) / rate
                    end = Fraction(_text(node, "end", "0")) / rate
                    source = Fraction(_text(node, "in", "0")) / clip_rate
                    source_out = Fraction(_text(node, "out", "0")) / clip_rate
                    if offset < 0 or end < 0:
                        self.issue("TRANSITION_TIMING", "start/end=-1 需要转场求值，当前不能作为普通剪辑转换", node)
                    if source_out - source != end - offset:
                        self.issue("SOURCE_TIMELINE_DURATION", "源范围与时间线时长不一致，可能含变速或混合帧率解释；不能自动修正", node)
                    file = node.find("file")
                    if file is None:
                        self.issue("MEDIA_REFERENCE_MISSING", "片段没有 file 媒体引用", node)
                        continue
                    media = self.xmeml_media(file, tl)
                    if kind == "video":
                        media.video = True
                    channel = int(_text(node, "sourcetrack/trackindex", "1")) if kind == "audio" else 0
                    if kind == "audio":
                        media.channels = max(media.channels, channel)
                    output_index = _text(track, "outputchannelindex")
                    if kind == "audio" and output_index and int(output_index) != (channel if media.channels == 2 else 1):
                        self.issue("AUDIO_OUTPUT_ROUTING", "当前不支持将源声道改送其他输出的轨道路由", track)
                    scale, opacity, rotation, volume_db = self.xmeml_effects(node, kind)
                    clip = ExactClip(ident, _text(node, "name", "Clip"), media, kind, track_index,
                                     offset, source, end - offset, channel, enabled=_text(node, "enabled", "TRUE").upper() != "FALSE" and _text(track, "enabled", "TRUE").upper() != "FALSE", scale=scale, opacity=opacity, markers=self.xmeml_markers(node, clip_rate), rotation=rotation, volume_db=volume_db)
                    tl.clips.append(clip)
                    self.clip_nodes[ident] = node
                    groups[ident] = ident
                    links[ident] = [_text(link, "linkclipref") for link in node.findall("link")]
                    allowed = {"name", "duration", "rate", "start", "end", "in", "out", "enabled", "file", "link", "filter", "marker", "sourcetrack", "masterclipid", "ismasterclip", "anamorphic", "pixelaspectratio", "fielddominance", "compositemode", "subframeoffset", "mixedratesoffset", "mediadelay", "stillframeoffset", "syncoffset", "stillframe"}
                    for child in node:
                        if child.tag not in allowed:
                            self.loss("CLIP_METADATA", f"尚未映射 XMEML clipitem/{child.tag}", child, effect=child.tag in ("alphatype", "alphareverse", "gamma", "lut", "colorinfo"))
        def find(ident):
            while groups[ident] != ident:
                ident = groups[ident]
            return ident
        for ident, references in links.items():
            for ref in references:
                if ref not in groups:
                    self.issue("LINK_REFERENCE", f"音画 link 引用了不在当前项目的片段：{ref}", clip=ident)
                elif find(ident) != find(ref):
                    groups[find(ident)] = find(ref)
        group_counts = {}
        for clip in tl.clips:
            root = find(clip.id)
            group_counts[root] = group_counts.get(root, 0) + 1
        for clip in tl.clips:
            root = find(clip.id)
            clip.group = root if group_counts[root] > 1 else ""
        for media in {id(c.media): c.media for c in tl.clips}.values():
            if media.duration == 0:
                self.issue("MEDIA_DURATION_UNKNOWN", "媒体没有声明时长；只有明确选择后才能使用已用源范围作为最短声明时长", choices={"infer_duration": "以已使用的最末源出点作为媒体最短时长", "stop": "停止转换"}, details=media.uri)
                media.duration = max(c.source + c.duration for c in tl.clips if c.media is media)
        return tl


def _write_xmeml(timelines: list[ExactTimeline]) -> etree._ElementTree:
    root = etree.Element("xmeml", version="5")
    project = _sub(root, "project")
    _sub(project, "name", "XMLBridge")
    children = _sub(project, "children")
    file_ids = {}
    for ti, tl in enumerate(timelines):
        sequence = _sub(children, "sequence", id=f"sequence-{ti + 1}")
        _sub(sequence, "name", tl.name)
        _sub(sequence, "duration", _int(tl.duration * tl.rate))
        _write_rate(sequence, tl.rate)
        _write_tc(sequence, tl.start, tl.rate, tl.tc_format)
        media_root = _sub(sequence, "media")
        clip_nodes = {}
        track_numbers = {}
        for kind in ("video", "audio"):
            kind_node = _sub(media_root, kind)
            if kind == "video":
                fmt = _sub(kind_node, "format")
                sample = _sub(fmt, "samplecharacteristics")
                _write_rate(sample, tl.rate)
                _sub(sample, "width", tl.width)
                _sub(sample, "height", tl.height)
                _sub(sample, "anamorphic", "FALSE")
                _sub(sample, "pixelaspectratio", "square")
                _sub(sample, "fielddominance", "none")
            else:
                _sub(kind_node, "channelcount", "2")
                fmt = _sub(kind_node, "format")
                _sub(_sub(fmt, "samplecharacteristics"), "samplerate", tl.audio_rate)
                outputs = _sub(kind_node, "outputs")
                group = _sub(outputs, "group")
                _sub(group, "index", "1")
                _sub(group, "numchannels", "2")
                _sub(group, "downmix", "0")
                for channel in (1, 2):
                    _sub(_sub(group, "channel"), "index", channel)
            for track_index in sorted({c.track for c in tl.clips if c.kind == kind}):
                track = _sub(kind_node, "track")
                clips = sorted((c for c in tl.clips if c.kind == kind and c.track == track_index), key=lambda c: c.offset)
                if kind == "audio":
                    _sub(track, "outputchannelindex", clips[0].channel if clips[0].media.channels == 2 else 1)
                for ci, clip in enumerate(clips):
                    ident = f"clip-{ti}-{kind}-{track_index}-{ci}"
                    node = _sub(track, "clipitem", id=ident)
                    clip_nodes[clip.id] = node
                    track_numbers[clip.id] = (kind, track_index + 1, ci + 1)
                    _sub(node, "name", clip.name)
                    _sub(node, "duration", _int(clip.media.duration * tl.rate))
                    _write_rate(node, tl.rate)
                    _sub(node, "start", _int(clip.offset * tl.rate))
                    _sub(node, "end", _int((clip.offset + clip.duration) * tl.rate))
                    _sub(node, "in", _int(clip.source * tl.rate))
                    _sub(node, "out", _int((clip.source + clip.duration) * tl.rate))
                    _sub(node, "enabled", "TRUE" if clip.enabled else "FALSE")
                    media = clip.media
                    key = (media.uri, media.rate, media.start, media.duration)
                    if key in file_ids:
                        _sub(node, "file", id=file_ids[key])
                    else:
                        file_id = f"file-{len(file_ids) + 1}"
                        file_ids[key] = file_id
                        file_node = _sub(node, "file", id=file_id)
                        _sub(file_node, "name", Path(unquote(urlsplit(media.uri).path)).name)
                        _sub(file_node, "pathurl", media.uri)
                        _write_rate(file_node, media.rate)
                        _sub(file_node, "duration", _int(media.duration * media.rate))
                        _write_tc(file_node, media.start, media.rate)
                        file_media = _sub(file_node, "media")
                        if media.video:
                            video = _sub(file_media, "video")
                            sample = _sub(video, "samplecharacteristics")
                            _write_rate(sample, media.rate)
                            _sub(sample, "width", media.width)
                            _sub(sample, "height", media.height)
                            _sub(sample, "pixelaspectratio", "square")
                            _sub(sample, "fielddominance", "none")
                        if media.channels:
                            audio = _sub(file_media, "audio")
                            _sub(audio, "channelcount", media.channels)
                            sample = _sub(audio, "samplecharacteristics")
                            _sub(sample, "samplerate", media.sample_rate)
                    if kind == "audio":
                        source_track = _sub(node, "sourcetrack")
                        _sub(source_track, "mediatype", "audio")
                        _sub(source_track, "trackindex", clip.channel)
                    if kind == "video":
                        for parameter, value, default, effect_id, effect_name in (("scale", clip.scale * 100, 100, "basic", "Basic Motion"), ("rotation", -clip.rotation, 0, "basic", "Basic Motion"), ("opacity", clip.opacity * 100, 100, "opacity", "Opacity")):
                            if value == default:
                                continue
                            effect = _sub(_sub(node, "filter"), "effect")
                            _sub(effect, "name", effect_name)
                            _sub(effect, "effectid", effect_id)
                            _sub(effect, "effectcategory", "motion")
                            _sub(effect, "effecttype", "motion")
                            _sub(effect, "mediatype", "video")
                            param = _sub(effect, "parameter")
                            _sub(param, "parameterid", parameter)
                            _sub(param, "name", parameter.title())
                            _sub(param, "value", _decimal(value))
                    elif clip.volume_db != 0:
                        effect = _sub(_sub(node, "filter"), "effect")
                        _sub(effect, "name", "Audio Levels")
                        _sub(effect, "effectid", "audiolevels")
                        _sub(effect, "effectcategory", "audiolevels")
                        _sub(effect, "effecttype", "audiolevels")
                        _sub(effect, "mediatype", "audio")
                        param = _sub(effect, "parameter")
                        _sub(param, "parameterid", "level")
                        _sub(param, "name", "Level")
                        _sub(param, "value", _gain(clip.volume_db))
                    for marker in clip.markers:
                        _write_xmarker(node, marker, tl.rate)
        for clip in tl.clips:
            if not clip.group:
                continue
            for linked in (c for c in tl.clips if c.group == clip.group):
                link = _sub(clip_nodes[clip.id], "link")
                _sub(link, "linkclipref", clip_nodes[linked.id].get("id"))
                kind, track, index = track_numbers[linked.id]
                _sub(link, "mediatype", kind)
                _sub(link, "trackindex", track)
                _sub(link, "clipindex", index)
                if kind == "audio":
                    _sub(link, "groupindex", "1")
        for marker in tl.markers:
            _write_xmarker(sequence, marker, tl.rate)
    return etree.ElementTree(root)


def _write_xmarker(node, marker: ExactMarker, rate: Fraction):
    child = _sub(node, "marker")
    _sub(child, "name", marker.name)
    _sub(child, "comment", marker.note)
    _sub(child, "in", _int(marker.offset * rate))
    _sub(child, "out", _int((marker.offset + marker.duration) * rate) if marker.duration else "-1")


def _groups_for_fcpx(tl: ExactTimeline) -> list[list[ExactClip]]:
    groups: dict[str, list[ExactClip]] = {}
    for clip in tl.clips:
        groups.setdefault(clip.group or "single:" + clip.id, []).append(clip)
    return list(groups.values())


def _write_fcpx(timelines: list[ExactTimeline]) -> etree._ElementTree:
    root = etree.Element("fcpxml", version="1.8")
    resources = _sub(root, "resources")
    # Resolve 21.1.1 rejects an otherwise valid event-at-root document; the
    # same document imports when its event is contained by a library.
    event = _sub(_sub(root, "library"), "event", name="XMLBridge")
    media_ids = {}
    format_ids = {}

    def fmt(rate, width, height):
        key = (rate, width, height)
        if key not in format_ids:
            ident = f"r{len(format_ids) + len(media_ids) + 1}"
            format_ids[key] = ident
            display_rate = _decimal(rate) if rate.denominator == 1 else _decimal(Fraction(round(rate * 100), 100))
            _sub(resources, "format", id=ident, name=f"FFVideoFormat{width}x{height}p{display_rate}", frameDuration=_s(1 / rate), width=str(width), height=str(height), colorSpace="1-1-1 (Rec.709)")
        return format_ids[key]

    for tl in timelines:
        project = _sub(event, "project", name=tl.name)
        sequence = _sub(project, "sequence", format=fmt(tl.rate, tl.width, tl.height), duration=_s(tl.duration), tcStart=_s(tl.start), tcFormat=tl.tc_format, audioLayout="stereo", audioRate=f"{_decimal(Fraction(tl.audio_rate, 1000))}k")
        spine = _sub(sequence, "spine")
        records = []
        for group in _groups_for_fcpx(tl):
            video = next((c for c in group if c.kind == "video"), None)
            first = video or group[0]
            media = first.media
            key = (media.uri, media.rate, media.start, media.duration)
            if key not in media_ids:
                ident = f"r{len(format_ids) + len(media_ids) + 1}"
                media_ids[key] = ident
                attrs = dict(id=ident, name=Path(unquote(urlsplit(media.uri).path)).name, src=media.uri,
                             start=_s(media.start), duration=_s(media.duration), hasVideo="1" if media.video else "0",
                             hasAudio="1" if media.channels else "0")
                if media.video:
                    attrs["format"] = fmt(media.rate, media.width, media.height)
                if media.channels:
                    attrs.update(audioSources="1", audioChannels=str(media.channels), audioRate=str(media.sample_rate))
                _sub(resources, "asset", **attrs)
            attrs = dict(name=first.name, ref=media_ids[key], offset=_s(first.offset), start=_s(media.start + first.source), duration=_s(first.duration), enabled="1" if first.enabled else "0")
            audio = [c for c in group if c.kind == "audio"]
            if video:
                attrs["srcEnable"] = "all" if audio else "video"
                node = etree.Element("asset-clip", **attrs)
                if video.scale != 1 or video.rotation != 0:
                    _sub(node, "adjust-conform", type="none")
                    value = _decimal(video.scale)
                    _sub(node, "adjust-transform", scale=f"{value} {value}", rotation=_decimal(video.rotation))
                elif media.width != tl.width or media.height != tl.height:
                    _sub(node, "adjust-conform", type="none")
                if video.opacity != 1:
                    _sub(node, "adjust-blend", amount=_decimal(video.opacity))
            else:
                # An explicit audio item preserves its selected source channel.
                attrs["srcCh"] = str(first.channel)
                node = etree.Element("audio", **attrs)
            volume_db = audio[0].volume_db if audio else Decimal(0)
            if volume_db != 0:
                _sub(node, "adjust-volume", amount=_db_text(volume_db))
            for marker in first.markers:
                _sub(node, "marker", start=_s(media.start + first.source + marker.offset), duration=_s(marker.duration), value=marker.name, note=marker.note)
            records.append((first, node))
        # Build the conventional primary storyline and real gaps. Other tracks
        # use the primary item's local source coordinates for their anchors.
        primary_kind = "video" if any(c.kind == "video" for c in tl.clips) else "audio"
        primary = sorted(((clip, node) for clip, node in records if clip.kind == primary_kind and clip.track == 0), key=lambda pair: pair[0].offset)
        anchors = []
        cursor = Fraction(0)

        def add_gap(start, duration):
            node = _sub(spine, "gap", name="Gap", offset=_s(tl.start + start), start="0s", duration=_s(duration))
            anchors.append((start, duration, Fraction(0), node))

        for clip, node in primary:
            if clip.offset > cursor:
                add_gap(cursor, clip.offset - cursor)
            node.set("offset", _s(tl.start + clip.offset))
            spine.append(node)
            anchors.append((clip.offset, clip.duration, clip.media.start + clip.source, node))
            cursor = clip.offset + clip.duration
        if cursor < tl.duration:
            add_gap(cursor, tl.duration - cursor)

        def anchor_at(offset):
            return next((record for record in anchors if record[0] <= offset < record[0] + record[1]), anchors[-1])

        for clip, node in records:
            if any(node is primary_node for _, primary_node in primary):
                continue
            offset, _, source_start, anchor = anchor_at(clip.offset)
            node.set("offset", _s(source_start + clip.offset - offset))
            node.set("lane", str(clip.track) if clip.kind == "video" else str(-(clip.track + 1)))
            # Anchored items precede marker items in the Apple DTD.
            insertion = next((i for i, child in enumerate(anchor) if child.tag == "marker"), len(anchor))
            anchor.insert(insertion, node)
        for marker in tl.markers:
            offset, _, source_start, anchor = anchor_at(marker.offset)
            _sub(anchor, "marker", start=_s(source_start + marker.offset - offset), duration=_s(marker.duration), value=marker.name, note=marker.note)
    return etree.ElementTree(root)


def _marker_signature(markers):
    return sorted((m.offset, m.duration, m.name, m.note) for m in markers)


def _clip_signature(clip: ExactClip):
    return (clip.kind, clip.track, clip.offset, clip.source, clip.duration, clip.media.uri,
            clip.media.rate, clip.media.start, clip.media.duration, clip.media.sample_rate, clip.channel, clip.enabled,
            clip.scale if clip.kind == "video" else Fraction(1),
            clip.opacity if clip.kind == "video" else Fraction(1),
            clip.rotation if clip.kind == "video" else Fraction(0),
            clip.volume_db if clip.kind == "audio" else Decimal(0))


def _effective_markers(timeline: ExactTimeline):
    """Compare exact displayed marker positions across different parent models."""
    from collections import Counter
    result = Counter(_marker_signature(timeline.markers))
    for group in _groups_for_fcpx(timeline):
        group_markers = Counter()
        for clip in group:
            markers = Counter((clip.offset + m.offset, m.duration, m.name, m.note) for m in clip.markers)
            group_markers |= markers
        result += group_markers
    return sorted(result.elements())


def _signature(timeline: ExactTimeline):
    clips = sorted(_clip_signature(c) for c in timeline.clips)
    links = sorted(tuple(sorted(_clip_signature(c) for c in timeline.clips if c.group == group))
                   for group in {c.group for c in timeline.clips if c.group})
    return (timeline.name, timeline.rate, timeline.duration, timeline.start, timeline.width, timeline.height,
            timeline.tc_format, timeline.audio_rate, clips, links, _effective_markers(timeline))


def _target_issues(timelines: list[ExactTimeline], target: Target) -> list[Issue]:
    issues = []
    for tl in timelines:
        def block(code, message, clip="", details=""):
            item = next((c for c in tl.clips if c.id == clip), None)
            issues.append(Issue(code, message, "error", tl.name, item.name if item else clip,
                                _s(tl.start + item.offset) if item else "", location=tl.id, details=details))
        if tl.duration == 0:
            block("EMPTY_TIMELINE", "零时长时间线尚不能可靠跨格式导入")
        if target.family == "xmeml":
            if tl.rate.denominator != 1 and (tl.rate * Fraction(1001, 1000)).denominator != 1:
                block("TARGET_FRAME_RATE", f"XMEML 不支持帧率 {tl.rate}")
            values = [("项目时长", tl.duration, tl.rate), ("项目起始时间码", tl.start, tl.rate)]
            for clip in tl.clips:
                values.extend((f"{clip.name} {name}", value, rate) for name, value, rate in (
                    ("时间线起点", clip.offset, tl.rate), ("时长", clip.duration, tl.rate),
                    ("源入点", clip.source, tl.rate), ("媒体时长", clip.media.duration, clip.media.rate),
                    ("媒体时间码", clip.media.start, clip.media.rate)))
                if clip.media.rate.denominator != 1 and (clip.media.rate * Fraction(1001, 1000)).denominator != 1:
                    block("TARGET_MEDIA_RATE", f"XMEML 不能精确表示媒体帧率 {clip.media.rate}", clip.id)
                for marker in clip.markers:
                    values.extend(((f"{clip.name} marker", marker.offset, tl.rate), (f"{clip.name} marker 时长", marker.duration, tl.rate)))
            for marker in tl.markers:
                values.extend((("项目 marker", marker.offset, tl.rate), ("项目 marker 时长", marker.duration, tl.rate)))
            for name, value, rate in values:
                if (value * rate).denominator != 1:
                    block("TARGET_SUBFRAME", f"{name} 不能精确落在 XMEML 的整数帧上；不自动取整", details=f"seconds={value}, fps={rate}")
        elif target.family == "fcpxml":
            issues.append(Issue("COLOR_SPACE_DEFAULT", "源 XMEML 没有可可靠映射的 FCPXML 颜色空间；当前输出采用 Rec.709。请在目标软件核对素材颜色解释；其他色彩工作流程请在原软件重新导出", "error", tl.name,
                                location=tl.id, choices={"rec709": "采用 Rec.709，并在目标软件核对素材颜色解释", "stop": "停止转换"}))
            if tl.markers:
                issues.append(Issue("TIMELINE_MARKER_ATTACHMENT", "FCPXML 时间线 marker 必须附着在主片段或空隙上；绝对位置和内容保持，独立时间线归属会改变", "error", tl.name,
                                    location=tl.id, choices={"attach_markers": "保留绝对位置，将时间线 marker 附着在对应主片段/空隙", "stop": "停止转换"}))
            for group in _groups_for_fcpx(tl):
                if len(group) < 2:
                    continue
                video = [c for c in group if c.kind == "video"]
                audio = [c for c in group if c.kind == "audio"]
                first = group[0]
                same = all((c.media.uri, c.offset, c.source, c.duration, c.enabled, c.name, _marker_signature(c.markers)) ==
                           (first.media.uri, first.offset, first.source, first.duration, first.enabled, first.name, _marker_signature(first.markers)) for c in group)
                channels = sorted(c.channel for c in audio)
                if len(video) != 1 or channels != list(range(1, first.media.channels + 1)) or not same:
                    block("LINK_GROUP_UNREPRESENTABLE", "此 link 组含 J/L cut、不一致 marker、异源或非完整声道；当前不能可靠映射为 FCPXML 复合音画片段", first.id)
                if len({c.volume_db for c in audio}) > 1:
                    block("LINK_GROUP_AUDIO_VOLUME", "link 组的各声道音量不同；整体 adjust-volume 无法保留差异，当前不合并声道", first.id)
            for clip in tl.clips:
                for value in (clip.offset, clip.source, clip.duration, clip.media.start, clip.media.duration):
                    if abs(value.numerator) >= 2**63 or value.denominator >= 2**32:
                        block("FCP_TIME_RANGE", "时间超过 FCPXML 的 64-bit 分子/32-bit 分母范围", clip.id)
        else:
            block("CROSS_TARGET", f"未知目标格式：{target.family}")
    # IDs intentionally stable, but distinct subframe failures need distinct locations.
    for index, issue in enumerate(issues):
        if issue.code == "TARGET_SUBFRAME":
            issue.location = issue.message
            issue.id = ""
            issue.__post_init__()
    return issues


def _load(document, target, project_ids):
    reader = _Reader(document, project_ids)
    timelines = reader.read()
    issues = reader.issues + _target_issues(timelines, target)
    return timelines, issues


def analyze_cross(document: Document, target: Target, project_ids: list[str] | None = None) -> list[Issue]:
    """Preflight only. Every lossy choice must be supplied again at conversion."""
    timelines, issues = _load(document, target, project_ids)
    if any(i.severity == "error" and not i.choices for i in issues):
        return _unique(issues)
    # Verify representability even when the user has not yet accepted effects loss.
    try:
        _emit_checked(document, target, timelines)
    except InterchangeError as exc:
        issues.extend(exc.issues)
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        issues.append(Issue("CROSS_REPRESENTATION", f"目标格式无法精确表达此时间线：{exc}", "error"))
    return _unique(issues)


def _unique(issues):
    return list({i.id: i for i in issues}.values())


def _emit_checked(document: Document, target: Target, timelines: list[ExactTimeline]):
    tree = _write_xmeml(timelines) if target.family == "xmeml" else _write_fcpx(timelines)
    if target.family == "fcpxml":
        for node in tree.getroot().iter():
            for name in {"offset", "start", "duration", "tcStart", "frameDuration"} & set(node.attrib):
                value = _time(node.get(name))
                if abs(value.numerator) >= 2**63 or value.denominator >= 2**32:
                    raise InterchangeError([Issue("FCP_TIME_RANGE", "时间超过 FCPXML 的 64-bit 分子/32-bit 分母范围", "error", location=tree.getpath(node), details=f"{name}={value}")])
    output = Document(path=document.path, xml_path=document.xml_path, family=target.family, tree=tree)
    reader = _Reader(output)
    reread = reader.read()
    errors = [i for i in reader.issues if i.severity == "error" and not i.choices]
    if errors:
        raise InterchangeError(errors)
    if len(timelines) != len(reread):
        raise InterchangeError([Issue("TIMELINE_PROJECT_COUNT", "输出复核项目数量不一致", "error")])
    for before, after in zip(timelines, reread):
        if _signature(before) != _signature(after):
            raise InterchangeError([Issue("TIMELINE_SEMANTIC_MISMATCH", "输出复核发现轨道、源范围、音画关联、静态音视频参数或 marker 不一致；此次转换被阻止", "error", before.name,
                                          details=f"before={_signature(before)!r}\nafter={_signature(after)!r}")])
    checks = {
        "timeline": "passed",
        "arithmetic": "exact_fraction",
        "audio_volume_db_precision": "0.000000001",
        "projects_checked": len(timelines),
        "projects": [{"id": tl.id, "name": tl.name, "fps": str(tl.rate), "duration_seconds": str(tl.duration),
                      "start_seconds": str(tl.start), "video_clips": sum(c.kind == "video" for c in tl.clips),
                      "audio_clips": sum(c.kind == "audio" for c in tl.clips),
                      "linked_groups": len({c.group for c in tl.clips if c.group}),
                      "markers": len(tl.markers) + sum(len(c.markers) for c in tl.clips),
                      "media_uris": sorted({c.media.uri for c in tl.clips})} for tl in timelines],
        "verified_fields": ["project_count", "frame_rate", "duration", "start_timecode", "frame_size", "track_order", "clip_timeline_range", "clip_source_range", "media_uri", "media_start", "source_channel", "enabled", "uniform_scale", "rotation", "opacity", "static_audio_volume_db", "markers", "audio_video_links"],
        "host_import": "not_tested",
    }
    return tree, checks


def convert_cross(document: Document, target: Target, project_ids: list[str] | None = None,
                  decisions: dict[str, str] | None = None) -> tuple[etree._ElementTree, list[Issue], dict]:
    """Convert the declared subset, blocking unresolved or unsupported semantics."""
    timelines, issues = _load(document, target, project_ids)
    unresolved = [i for i in issues if i.unresolved(decisions or {})]
    if unresolved:
        raise InterchangeError(_unique(unresolved))
    try:
        tree, checks = _emit_checked(document, target, timelines)
    except InterchangeError:
        raise
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        raise InterchangeError([Issue("CROSS_REPRESENTATION", f"目标格式无法精确表达此时间线：{exc}", "error")]) from exc
    checks["accepted_losses"] = [{"id": i.id, "code": i.code, "action": (decisions or {}).get(i.id), "message": i.message}
                                 for i in issues if i.choices]
    return tree, _unique(issues), checks
