"""Official-DTD-valid editorial fixtures and independent output checks."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from urllib.parse import unquote, urlparse

from lxml import etree


SCHEMAS = Path(__file__).resolve().parents[1] / "xmlbridge" / "schemas"


def dtd(version: str, family: str = "fcpxml") -> etree.DTD:
    name = f"FCPXMLv{version.replace('.', '_')}.dtd" if family == "fcpxml" else f"XMEMLv{version}.dtd"
    return etree.DTD(str(SCHEMAS / name))


def assert_valid(tree: etree._ElementTree | etree._Element, version: str) -> None:
    root = tree.getroot() if isinstance(tree, etree._ElementTree) else tree
    schema = dtd(version, root.tag)
    assert schema.validate(tree), str(schema.error_log)


def make_fcpxml(
    version: str = "1.9",
    *,
    src: str = "file:///missing/editorial-test.mov",
    projects: int = 1,
    bookmark: str | None = None,
    caption: bool = False,
    frame_duration: str = "1001/30000s",
    duration: str = "3003/100s",
    tc_format: str = "DF",
) -> etree._ElementTree:
    root = etree.Element("fcpxml", version=version)
    resources = etree.SubElement(root, "resources")
    etree.SubElement(
        resources, "format", id="r1", name="FFVideoFormat1080p2997",
        frameDuration=frame_duration, width="1920", height="1080",
    )
    asset = etree.SubElement(
        resources, "asset", id="r2", name="镜头 & source", start="3600s",
        duration="172800s", hasVideo="1", format="r1", hasAudio="1",
        audioSources="1", audioChannels="2", audioRate="48000",
    )
    if version == "1.8":
        asset.set("src", src)
        bookmark_parent = asset
    else:
        bookmark_parent = etree.SubElement(asset, "media-rep", kind="original-media", src=src)
    if bookmark is not None:
        etree.SubElement(bookmark_parent, "bookmark").text = bookmark
    library = etree.SubElement(root, "library")
    event = etree.SubElement(library, "event", name="回归样例")
    for index in range(projects):
        project = etree.SubElement(event, "project", name=f"项目 {index + 1}")
        sequence = etree.SubElement(
            project, "sequence", format="r1", duration=duration,
            tcStart="3600s", tcFormat=tc_format, audioLayout="stereo", audioRate="48k",
        )
        spine = etree.SubElement(sequence, "spine")
        clip = etree.SubElement(
            spine, "asset-clip", ref="r2", name=f"镜头 {index + 1}",
            offset="3600s", start="3600s", duration=duration,
            tcStart="3600s", tcFormat=tc_format,
        )
        if caption:
            cap = etree.SubElement(
                clip, "caption", lane="1", offset="3600s", start="0s",
                duration="1001/1000s", role="iTT.en",
            )
            etree.SubElement(cap, "text").text = "测试字幕"
        etree.SubElement(
            clip, "marker", start="3600s", duration="1001/30000s", value="核对点",
        )
    tree = etree.ElementTree(root)
    assert_valid(tree, version)
    return tree


def make_xmeml(version: str = "5", *, src: str = "file:///missing/legacy.mov") -> etree._ElementTree:
    def child(parent, tag, value):
        etree.SubElement(parent, tag).text = str(value)

    def rate(parent):
        node = etree.SubElement(parent, "rate")
        child(node, "timebase", 30)
        child(node, "ntsc", "TRUE")

    root = etree.Element("xmeml", version=version)
    sequence = etree.SubElement(root, "sequence", id="sequence-1")
    child(sequence, "name", "旧式剪辑")
    child(sequence, "duration", 900)
    rate(sequence)
    timecode = etree.SubElement(sequence, "timecode")
    rate(timecode)
    child(timecode, "string", "01:00:00;00")
    child(timecode, "frame", 107892)
    child(timecode, "displayformat", "DF")
    media = etree.SubElement(sequence, "media")
    video = etree.SubElement(media, "video")
    track = etree.SubElement(video, "track")
    clip = etree.SubElement(track, "clipitem", id="clip-1")
    for tag, value in (("name", "镜头"), ("duration", 1800), ("start", 0), ("end", 900), ("in", 0), ("out", 900)):
        child(clip, tag, value)
    rate(clip)
    file = etree.SubElement(clip, "file", id="file-1")
    child(file, "name", "legacy.mov")
    child(file, "pathurl", src)
    child(file, "duration", 1800)
    rate(file)
    marker = etree.SubElement(clip, "marker")
    child(marker, "name", "旧式标记")
    child(marker, "in", 24)
    child(marker, "out", 25)
    tree = etree.ElementTree(root)
    assert_valid(tree, version)
    return tree


def write_tree(path: Path, tree: etree._ElementTree, *, validate: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if validate:
        assert_valid(tree, tree.getroot().get("version"))
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)
    return path


def hash_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def output_xml(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_dir():
        preferred = candidate / "Info.fcpxml"
        if preferred.exists():
            return preferred
        matches = list(candidate.rglob("*.fcpxml"))
        assert len(matches) == 1, matches
        return matches[0]
    return candidate


def read_output(path: str | Path) -> etree._ElementTree:
    return etree.parse(str(output_xml(path)), etree.XMLParser(resolve_entities=False, no_network=True))


def resolve_uri(uri: str, xml_path: Path) -> Path:
    parsed = urlparse(uri)
    assert parsed.scheme in ("", "file"), uri
    path = Path(unquote(parsed.path))
    return path.resolve() if path.is_absolute() else (xml_path.parent / path).resolve()


def media_uris(tree: etree._ElementTree) -> list[str]:
    return tree.xpath("//asset/@src | //media-rep/@src")


def timing_snapshot(tree: etree._ElementTree) -> list[tuple]:
    time_attributes = {"frameDuration", "offset", "start", "duration", "tcStart", "tcFormat"}
    return [
        (node.tag, tuple(sorted((key, value) for key, value in node.attrib.items() if key in time_attributes)))
        for node in tree.getroot().iter()
        if isinstance(node.tag, str) and any(key in time_attributes for key in node.attrib)
    ]
