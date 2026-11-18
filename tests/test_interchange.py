from __future__ import annotations

from fractions import Fraction
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from xmlbridge.interchange import InterchangeError, analyze_cross, convert_cross
from xmlbridge.models import Document, Target
from helpers import assert_valid


def frame_time(frames, rate=Fraction(30000, 1001)):
    return f"{Fraction(frames) / rate}s"


def fcpx(tmp_path, rate=Fraction(30000, 1001), projects=1, av=True):
    root = etree.Element("fcpxml", version="1.8")
    resources = etree.SubElement(root, "resources")
    etree.SubElement(resources, "format", id="fmt", frameDuration=frame_time(1, rate), width="1920", height="1080")
    etree.SubElement(resources, "asset", id="media", src="media/镜头 space.mov", format="fmt",
                     start=frame_time(108000, rate), duration=frame_time(10_000_000, rate),
                     hasVideo="1", hasAudio="1" if av else "0", audioChannels="2" if av else "0", audioRate="48k")
    event = etree.SubElement(root, "event", name="样例")
    for index in range(projects):
        project = etree.SubElement(event, "project", name=f"项目 {index + 1}")
        sequence = etree.SubElement(project, "sequence", format="fmt", duration=frame_time(9_000_000, rate),
                                    tcStart=frame_time(108000, rate), tcFormat="NDF", audioLayout="stereo", audioRate="48k")
        spine = etree.SubElement(sequence, "spine")
        etree.SubElement(spine, "gap", offset=frame_time(108000, rate), start="0s", duration=frame_time(30, rate))
        clip = etree.SubElement(spine, "asset-clip", ref="media", name="剪辑一", offset=frame_time(108030, rate),
                               start=frame_time(108090, rate), duration=frame_time(900, rate))
        # An overlaid silent clip and a very late clip exercise track ordering,
        # independent offsets and long-duration rational arithmetic.
        etree.SubElement(clip, "asset-clip", ref="media", name="上层", lane="1", srcEnable="video", offset=frame_time(108120, rate), start=frame_time(108000, rate), duration=frame_time(120, rate))
        etree.SubElement(clip, "marker", start=frame_time(108180, rate), duration=frame_time(30, rate), value="检查点", note="说明")
        etree.SubElement(spine, "asset-clip", ref="media", name="长时间线末端", srcEnable="video", offset=frame_time(9_107_000, rate), start=frame_time(108000, rate), duration=frame_time(100, rate))
    tree = etree.ElementTree(root)
    assert_valid(tree, "1.8")
    return Document(tmp_path / "source.fcpxml", family="fcpxml", version="1.8", tree=tree)


def xmeml(tmp_path, rate=Fraction(25), projects=1):
    root = etree.Element("xmeml", version="5")
    project = etree.SubElement(root, "project")
    etree.SubElement(project, "name").text = "源工程"
    children = etree.SubElement(project, "children")
    for index in range(projects):
        seq = etree.SubElement(children, "sequence", id=f"sequence{index}")
        etree.SubElement(seq, "name").text = f"项目 {index + 1}"
        etree.SubElement(seq, "duration").text = "250"
        put_rate(seq, rate)
        tc = etree.SubElement(seq, "timecode")
        put_rate(tc, rate)
        etree.SubElement(tc, "frame").text = "90000"
        etree.SubElement(tc, "displayformat").text = "NDF"
        media = etree.SubElement(seq, "media")
        video = etree.SubElement(media, "video")
        fmt = etree.SubElement(video, "format")
        sample = etree.SubElement(fmt, "samplecharacteristics")
        etree.SubElement(sample, "width").text = "1920"
        etree.SubElement(sample, "height").text = "1080"
        vt = etree.SubElement(video, "track")
        audio = etree.SubElement(media, "audio")
        tracks = [(vt, "video", 0)] + [(etree.SubElement(audio, "track"), "audio", i) for i in (1, 2)]
        for track, kind, channel in tracks:
            ident = f"{index}{kind}{channel}"
            clip = etree.SubElement(track, "clipitem", id=ident)
            for tag, value in (("name", "镜头"), ("duration", "1000"), ("start", "25"), ("end", "125"), ("in", "50"), ("out", "150")):
                etree.SubElement(clip, tag).text = value
            put_rate(clip, rate)
            file = etree.SubElement(clip, "file", id=f"file{index}")
            if kind == "video":
                etree.SubElement(file, "pathurl").text = "media/镜头.mov"
                etree.SubElement(file, "duration").text = "1000"
                put_rate(file, rate)
                ftc = etree.SubElement(file, "timecode")
                put_rate(ftc, rate)
                etree.SubElement(ftc, "frame").text = "90000"
                etree.SubElement(ftc, "displayformat").text = "NDF"
                fm = etree.SubElement(file, "media")
                fv = etree.SubElement(fm, "video")
                fs = etree.SubElement(fv, "samplecharacteristics")
                etree.SubElement(fs, "width").text = "1920"
                etree.SubElement(fs, "height").text = "1080"
                fa = etree.SubElement(fm, "audio")
                etree.SubElement(fa, "channelcount").text = "2"
                fs = etree.SubElement(fa, "samplecharacteristics")
                etree.SubElement(fs, "samplerate").text = "48000"
            if kind == "audio":
                st = etree.SubElement(clip, "sourcetrack")
                etree.SubElement(st, "mediatype").text = "audio"
                etree.SubElement(st, "trackindex").text = str(channel)
            for linked_kind, linked_channel in (("video", 0), ("audio", 1), ("audio", 2)):
                link = etree.SubElement(clip, "link")
                etree.SubElement(link, "linkclipref").text = f"{index}{linked_kind}{linked_channel}"
        marker = etree.SubElement(seq, "marker")
        etree.SubElement(marker, "name").text = "场记"
        etree.SubElement(marker, "comment").text = "项目标记"
        etree.SubElement(marker, "in").text = "40"
        etree.SubElement(marker, "out").text = "45"
    return Document(tmp_path / "source.xml", family="xmeml", version="5", tree=etree.ElementTree(root))


def put_rate(node, rate):
    rate_node = etree.SubElement(node, "rate")
    base = rate if rate.denominator == 1 else rate * Fraction(1001, 1000)
    etree.SubElement(rate_node, "timebase").text = str(base)
    etree.SubElement(rate_node, "ntsc").text = "FALSE" if rate.denominator == 1 else "TRUE"


def blocking(issues):
    return [i for i in issues if i.unresolved({})]


def marker_decisions(document, target, selection=None):
    actions = {"TIMELINE_MARKER_ATTACHMENT": "attach_markers", "COLOR_SPACE_DEFAULT": "rec709"}
    return {i.id: actions[i.code] for i in analyze_cross(document, target, selection)
            if i.code in actions}


def xvalid(tree):
    path = Path(__file__).parents[1] / "xmlbridge/schemas/XMEMLv5.dtd"
    schema = etree.DTD(str(path))
    assert schema.validate(tree), str(schema.error_log)


@pytest.mark.parametrize("rate", [Fraction(25), Fraction(30000, 1001), Fraction(24000, 1001)])
def test_fcpx_to_xmeml_exact_long_multitrack_av_and_relative_media(tmp_path, rate):
    document = fcpx(tmp_path, rate)
    target = Target("xmeml", "5")
    assert not blocking(analyze_cross(document, target))
    tree, issues, checks = convert_cross(document, target)
    xvalid(tree)
    assert checks["timeline"] == "passed"
    assert checks["host_import"] == "not_tested"
    assert checks["projects"][0]["fps"] == str(rate)
    assert checks["projects"][0]["video_clips"] == 3
    assert checks["projects"][0]["audio_clips"] == 2
    clips = tree.xpath("//clipitem")
    assert sorted((c.findtext("start"), c.findtext("end")) for c in clips) == [("30", "930")] * 3 + [("60", "180"), ("8999000", "8999100")]
    assert tree.xpath("//file/pathurl/text()") == [(tmp_path / "media/镜头 space.mov").resolve().as_uri()]
    assert len(tree.xpath("//clipitem[link]")) == 3
    assert any(i.code == "MEDIA_OFFLINE" for i in issues)


@pytest.mark.parametrize("rate", [Fraction(25), Fraction(30000, 1001)])
def test_xmeml_to_fcpx_av_links_project_markers_and_dtd(tmp_path, rate):
    document = xmeml(tmp_path, rate)
    target = Target("fcpxml", "1.8")
    assert not [i for i in blocking(analyze_cross(document, target)) if i.code not in ("TIMELINE_MARKER_ATTACHMENT", "COLOR_SPACE_DEFAULT")]
    tree, _, checks = convert_cross(document, target, decisions=marker_decisions(document, target))
    assert_valid(tree, "1.8")
    assert tree.xpath("/fcpxml/library/event/project")
    assert not tree.xpath("/fcpxml/event")
    resources = tree.xpath("/fcpxml/resources/*/@id")
    assert len(resources) == len(set(resources))
    assert all(value.startswith("r") and value[1:].isdigit() for value in resources)
    assert checks["projects"][0]["linked_groups"] == 1
    assert len(tree.xpath("//asset-clip")) == 1
    assert tree.xpath("//asset-clip/@srcEnable") == ["all"]
    assert tree.xpath("//asset-clip/marker/@value") == ["场记"]
    assert tree.xpath("//spine/asset-clip")
    assert not tree.xpath("//gap/asset-clip")
    assert tree.xpath("//asset/@audioChannels") == ["2"]
    assert tree.xpath("//asset/@audioRate") == ["48000"]
    assert tree.xpath("//format/@colorSpace") == ["1-1-1 (Rec.709)"]


@pytest.mark.parametrize("family", ["fcpxml", "xmeml"])
def test_multiple_projects_and_explicit_selection_are_not_lost(tmp_path, family):
    document = fcpx(tmp_path, projects=3) if family == "fcpxml" else xmeml(tmp_path, projects=3)
    target = Target("xmeml", "5") if family == "fcpxml" else Target("fcpxml", "1.8")
    tree, _, checks = convert_cross(document, target, decisions=marker_decisions(document, target))
    assert checks["projects_checked"] == 3
    assert len(tree.xpath("//sequence[not(ancestor::sequence)]")) == 3
    selected, _, selected_checks = convert_cross(document, target, ["p1"], decisions=marker_decisions(document, target, ["p1"]))
    assert selected_checks["projects_checked"] == 1
    names = selected.xpath("//project/@name") if target.family == "fcpxml" else selected.xpath("//sequence/name/text()")
    assert names == ["项目 2"]


@pytest.mark.parametrize("selection", [[], ["p99"]])
def test_invalid_selection_blocks(tmp_path, selection):
    with pytest.raises(InterchangeError, match="项目选择"):
        convert_cross(fcpx(tmp_path), Target("xmeml", "5"), selection)


def test_xmeml_target_subframe_never_rounds(tmp_path):
    document = fcpx(tmp_path)
    document.tree.xpath("//asset-clip")[0].set("offset", "3600s")
    issues = analyze_cross(document, Target("xmeml", "5"))
    assert any(i.code == "TARGET_SUBFRAME" and not i.choices for i in issues)
    with pytest.raises(InterchangeError):
        convert_cross(document, Target("xmeml", "5"))


@pytest.mark.parametrize("tag", ["ref-clip", "mc-clip", "sync-clip", "title", "caption", "transition", "spine"])
def test_unsupported_structures_are_specific_blockers(tmp_path, tag):
    document = fcpx(tmp_path)
    node = etree.SubElement(document.tree.xpath("//asset-clip")[0], tag)
    node.set("duration", "1s")
    issues = analyze_cross(document, Target("xmeml", "5"))
    assert any(i.code == "UNSUPPORTED_" + tag.upper().replace("-", "_") and not i.choices for i in issues)


def test_effect_removal_requires_explicit_valid_decision(tmp_path):
    document = fcpx(tmp_path)
    clip = document.tree.xpath("//asset-clip")[0]
    effect = etree.SubElement(clip, "filter-video", ref="unknown")
    target = Target("xmeml", "5")
    issues = analyze_cross(document, target)
    loss = next(i for i in issues if i.code == "UNSUPPORTED_PROCESSING")
    for decisions in ({}, {loss.id: "stop"}, {loss.id: "typo"}):
        with pytest.raises(InterchangeError):
            convert_cross(document, target, decisions=decisions)
    tree, _, checks = convert_cross(document, target, decisions={loss.id: "drop_effect"})
    xvalid(tree)
    assert checks["accepted_losses"][0]["action"] == "drop_effect"
    assert not tree.xpath("//filter")


def test_static_uniform_scale_and_opacity_are_reversible(tmp_path):
    document = fcpx(tmp_path)
    clip = document.tree.xpath("//asset-clip")[0]
    clip.insert(0, etree.Element("adjust-transform", scale="1.25 1.25"))
    clip.insert(1, etree.Element("adjust-blend", amount="0.75"))
    tree, _, _ = convert_cross(document, Target("xmeml", "5"))
    assert tree.xpath("//parameter[parameterid='scale']/value/text()") == ["125"]
    backdoc = Document(tmp_path / "back.xml", family="xmeml", tree=tree)
    backtarget = Target("fcpxml", "1.8")
    back, _, checks = convert_cross(backdoc, backtarget, decisions=marker_decisions(backdoc, backtarget))
    assert_valid(back, "1.8")
    assert back.xpath("//adjust-transform/@scale") == ["1.25 1.25"]
    assert back.xpath("//adjust-blend/@amount") == ["0.75"]
    assert checks["timeline"] == "passed"


@pytest.mark.parametrize("tag", ["timeMap", "conform-rate"])
def test_retime_has_no_drop_choice(tmp_path, tag):
    document = fcpx(tmp_path)
    etree.SubElement(document.tree.xpath("//asset-clip")[0], tag)
    issues = analyze_cross(document, Target("xmeml", "5"))
    assert any(i.code == "RETIME_UNSUPPORTED" and not i.choices for i in issues)


def test_xmeml_duration_mismatch_is_not_silently_fixed(tmp_path):
    document = xmeml(tmp_path)
    document.tree.xpath("//clipitem")[0].find("out").text = "160"
    issues = analyze_cross(document, Target("fcpxml", "1.8"))
    assert any(i.code == "SOURCE_TIMELINE_DURATION" for i in issues)


def test_xmeml_split_link_group_is_blocked(tmp_path):
    document = xmeml(tmp_path)
    audio = document.tree.xpath("//media/audio/track/clipitem")[0]
    audio.find("start").text = "30"
    audio.find("end").text = "130"
    issues = analyze_cross(document, Target("fcpxml", "1.8"))
    assert any(i.code == "LINK_GROUP_UNREPRESENTABLE" for i in issues)


def test_actual_existing_relative_media_has_no_offline_warning(tmp_path):
    directory = tmp_path / "media"
    directory.mkdir()
    (directory / "镜头 space.mov").write_bytes(b"fixture path exists; media is not decoded")
    document = fcpx(tmp_path)
    issues = analyze_cross(document, Target("xmeml", "5"))
    assert not any(i.code == "MEDIA_OFFLINE" for i in issues)


def test_fcpx_source_tree_is_not_mutated(tmp_path):
    document = fcpx(tmp_path)
    before = etree.tostring(document.tree)
    convert_cross(document, Target("xmeml", "5"))
    assert etree.tostring(document.tree) == before


def test_top_level_sequence_without_project_is_supported(tmp_path):
    document = fcpx(tmp_path, av=False)
    root = document.tree.getroot()
    seq = document.tree.xpath("//sequence")[0]
    root.remove(root.find("event"))
    root.append(seq)
    tree, _, checks = convert_cross(document, Target("xmeml", "5"))
    assert checks["projects_checked"] == 1
    assert tree.xpath("//sequence/name/text()") == ["Timeline"]


def test_string_drop_frame_timecode_parses_exactly(tmp_path):
    document = xmeml(tmp_path, Fraction(30000, 1001))
    tc = document.tree.xpath("//sequence/timecode")[0]
    tc.remove(tc.find("frame"))
    etree.SubElement(tc, "string").text = "01:00:00;00"
    tc.find("displayformat").text = "DF"
    target = Target("fcpxml", "1.8")
    tree, _, checks = convert_cross(document, target, decisions=marker_decisions(document, target))
    assert tree.xpath("//sequence/@tcStart") == [frame_time(107892)]
    assert checks["projects"][0]["start_seconds"] == str(Fraction(107892 * 1001, 30000))


def test_timeline_marker_reattachment_is_explicit_and_position_stays_exact(tmp_path):
    document = xmeml(tmp_path)
    target = Target("fcpxml", "1.8")
    issue = next(i for i in analyze_cross(document, target) if i.code == "TIMELINE_MARKER_ATTACHMENT")
    with pytest.raises(InterchangeError):
        convert_cross(document, target)
    decisions = marker_decisions(document, target)
    tree, _, checks = convert_cross(document, target, decisions=decisions)
    clip = tree.xpath("//asset-clip")[0]
    marker = clip.find("marker")
    # 40/25s timeline marker; clip at25/25s uses source in50/25s.
    assert Fraction(marker.get("start").removesuffix("s")) == Fraction(65, 25) + 3600
    assert any(loss["action"] == "attach_markers" for loss in checks["accepted_losses"])


def test_color_resource_loss_is_not_duplicated_for_same_shared_format(tmp_path):
    document = fcpx(tmp_path, projects=3)
    document.tree.xpath("//format")[0].set("colorSpace", "1-1-1 (Rec. 709)")
    issues = analyze_cross(document, Target("xmeml", "5"))
    color = [i for i in issues if i.code == "COLOR_METADATA"]
    assert len(color) == 1
    assert color[0].project == "共享资源"


@pytest.mark.parametrize("kind", ["FCPXML", "XMEML"])
def test_unknown_audio_processing_requires_a_decision(tmp_path, kind):
    if kind == "FCPXML":
        document = fcpx(tmp_path)
        clip = document.tree.xpath("//asset-clip")[0]
        volume = etree.Element("adjust-volume", amount="-6dB")
        etree.SubElement(volume, "param", name="amount", key="volume")
        clip.insert(0, volume)
        target = Target("xmeml", "5")
        code = "AUDIO_VOLUME_UNSUPPORTED"
    else:
        document = xmeml(tmp_path)
        clip = document.tree.xpath("//media/audio/track/clipitem")[0]
        effect = etree.SubElement(etree.SubElement(clip, "filter"), "effect")
        etree.SubElement(effect, "effectid").text = "audiolevels"
        etree.SubElement(effect, "name").text = "Audio Levels"
        target = Target("fcpxml", "1.8")
        code = "AUDIO_VOLUME_UNSUPPORTED"
    issues = analyze_cross(document, target)
    processing = next(i for i in issues if i.code == code)
    assert processing.choices.keys() == {"drop_effect", "stop"}
    with pytest.raises(InterchangeError):
        convert_cross(document, target)


def test_more_than_stereo_is_blocked_without_silent_channel_loss(tmp_path):
    document = fcpx(tmp_path)
    document.tree.xpath("//asset")[0].set("audioChannels", "6")
    issues = analyze_cross(document, Target("xmeml", "5"))
    assert any(i.code == "AUDIO_CHANNEL_LAYOUT" and not i.choices for i in issues)


def test_custom_track_output_routing_is_blocked(tmp_path):
    document = xmeml(tmp_path)
    track = document.tree.xpath("//media/audio/track")[0]
    etree.SubElement(track, "outputchannelindex").text = "2"
    issues = analyze_cross(document, Target("fcpxml", "1.8"))
    assert any(i.code == "AUDIO_OUTPUT_ROUTING" and not i.choices for i in issues)


def test_reversed_stereo_source_channels_are_blocked(tmp_path):
    document = xmeml(tmp_path)
    media = document.tree.xpath("//file/media/audio")[0]
    left = etree.SubElement(media, "audiochannel")
    etree.SubElement(left, "channellabel").text = "right"
    issues = analyze_cross(document, Target("fcpxml", "1.8"))
    assert any(i.code == "AUDIO_CHANNEL_MAPPING" for i in issues)


def test_project_44k1_sample_rate_preserved_and_standard_outputs_written(tmp_path):
    document = fcpx(tmp_path, rate=Fraction(25))
    document.tree.xpath("//sequence")[0].set("audioRate", "44.1k")
    document.tree.xpath("//asset")[0].set("audioRate", "44100")
    tree, _, checks = convert_cross(document, Target("xmeml", "5"))
    assert tree.xpath("//sequence/media/audio/format/samplecharacteristics/samplerate/text()") == ["44100"]
    assert tree.xpath("//sequence/media/audio/outputs/group/channel/index/text()") == ["1", "2"]
    assert tree.xpath("//sequence/media/audio/track/outputchannelindex/text()") == ["1", "2"]
    assert tree.xpath("//file/media/audio/samplecharacteristics/samplerate/text()") == ["44100"]
    backdoc, target = Document(tmp_path / "source.xml", family="xmeml", tree=tree), Target("fcpxml", "1.8")
    back, _, _ = convert_cross(backdoc, target, decisions=marker_decisions(backdoc, target))
    assert back.xpath("//sequence/@audioRate") == ["44.1k"]
    assert back.xpath("//asset/@audioRate") == ["44100"]


def test_missing_xmeml_media_duration_requires_explicit_inference(tmp_path):
    document = xmeml(tmp_path)
    file = document.tree.xpath("//file[duration]")[0]
    file.remove(file.find("duration"))
    target = Target("fcpxml", "1.8")
    issues = analyze_cross(document, target)
    unknown = next(i for i in issues if i.code == "MEDIA_DURATION_UNKNOWN")
    with pytest.raises(InterchangeError):
        convert_cross(document, target, decisions=marker_decisions(document, target))
    decisions = marker_decisions(document, target)
    decisions[unknown.id] = "infer_duration"
    tree, _, checks = convert_cross(document, target, decisions=decisions)
    assert tree.xpath("//asset/@duration") == ["6s"]
    assert checks["timeline"] == "passed"


def test_unknown_fcpx_clip_attribute_is_not_discarded(tmp_path):
    document = fcpx(tmp_path)
    document.tree.xpath("//asset-clip")[0].set("futureRenderOption", "1")
    issues = analyze_cross(document, Target("xmeml", "5"))
    assert any(i.code == "CLIP_ATTRIBUTE" and not i.choices for i in issues)


def test_secondary_track_connected_offsets_use_primary_source_coordinates(tmp_path):
    document = fcpx(tmp_path)
    xml, _, _ = convert_cross(document, Target("xmeml", "5"))
    backdoc, target = Document(tmp_path / "source.xml", family="xmeml", tree=xml), Target("fcpxml", "1.8")
    back, _, checks = convert_cross(backdoc, target, decisions=marker_decisions(backdoc, target))
    overlay = back.xpath("//asset-clip[@name='上层']")[0]
    primary = overlay.getparent()
    assert primary.tag == "asset-clip"
    assert Fraction(overlay.get("offset").removesuffix("s")) == Fraction(primary.get("start").removesuffix("s")) + Fraction(30 * 1001, 30000)
    assert overlay.get("lane") == "1"
    assert checks["timeline"] == "passed"


def test_unsupported_xmeml_speed_filter_is_not_a_drop_effect_option(tmp_path):
    document = xmeml(tmp_path)
    clip = document.tree.xpath("//clipitem")[0]
    effect = etree.SubElement(etree.SubElement(clip, "filter"), "effect")
    etree.SubElement(effect, "effectid").text = "timeremap"
    issues = analyze_cross(document, Target("fcpxml", "1.8"))
    assert any(i.code == "RETIME_UNSUPPORTED" and not i.choices for i in issues)


def add_xeffect(clip, ident, values, *, start=None, end=None):
    effect_filter = etree.SubElement(clip, "filter")
    if start is not None:
        etree.SubElement(effect_filter, "start").text = str(start)
    if end is not None:
        etree.SubElement(effect_filter, "end").text = str(end)
    effect = etree.SubElement(effect_filter, "effect")
    etree.SubElement(effect, "effectid").text = ident
    for key, value in values.items():
        parameter = etree.SubElement(effect, "parameter")
        etree.SubElement(parameter, "parameterid").text = key
        child = etree.SubElement(parameter, "value")
        if isinstance(value, tuple):
            for tag, number in zip(("horiz", "vert"), value):
                etree.SubElement(child, tag).text = str(number)
        else:
            child.text = str(value)
    return effect_filter


def test_color_default_is_explicit_and_cannot_be_bypassed_with_other_decision(tmp_path):
    document, target = xmeml(tmp_path), Target("fcpxml", "1.8")
    issues = analyze_cross(document, target)
    color = next(i for i in issues if i.code == "COLOR_SPACE_DEFAULT")
    assert set(color.choices) == {"rec709", "stop"}
    for action in (None, "stop", "drop_metadata", "rec2020"):
        decisions = marker_decisions(document, target)
        decisions.pop(color.id)
        if action is not None:
            decisions[color.id] = action
        with pytest.raises(InterchangeError):
            convert_cross(document, target, decisions=decisions)
    tree, _, checks = convert_cross(document, target, decisions=marker_decisions(document, target))
    assert tree.xpath("//format/@colorSpace") == ["1-1-1 (Rec.709)"]
    assert checks["accepted_losses"][0]["action"] == "rec709"


@pytest.mark.parametrize("amount", ["-6", "-6dB", "3dB", "-120dB", "-infdB", "0dB"])
def test_static_audio_volume_survives_both_channels_and_roundtrip(tmp_path, amount):
    document = fcpx(tmp_path)
    clip = document.tree.xpath("//asset-clip")[0]
    clip.insert(0, etree.Element("adjust-volume", amount=amount))
    tree, _, checks = convert_cross(document, Target("xmeml", "5"))
    xvalid(tree)
    levels = tree.xpath("//clipitem/filter/effect[effectid='audiolevels']/parameter/value/text()")
    if amount == "0dB":
        assert levels == []
    else:
        assert len(levels) == 2
        if amount == "-infdB":
            assert levels == ["0", "0"]
        else:
            assert all(abs(20 * Decimal(n).log10() - Decimal(amount.removesuffix("dB"))) < Decimal("0.000000001") for n in levels)
    assert "static_audio_volume_db" in checks["verified_fields"]
    backdoc, target = Document(tmp_path / "legacy.xml", family="xmeml", tree=tree), Target("fcpxml", "1.8")
    back, _, _ = convert_cross(backdoc, target, decisions=marker_decisions(backdoc, target))
    assert_valid(back, "1.8")
    values = back.xpath("//asset-clip[@name='剪辑一']/adjust-volume/@amount")
    assert values == [] if amount == "0dB" else values == ["-infdB"] if amount == "-infdB" else values == [amount if amount.endswith("dB") else amount + "dB"]


def test_native_resolve_rotation_units_centeroffset_and_full_filter_span(tmp_path):
    document, target = xmeml(tmp_path), Target("fcpxml", "1.8")
    clip = document.tree.xpath("//video/track/clipitem")[0]
    add_xeffect(clip, "basic", {"rotation": "-30", "scale": "125", "centerOffset": (0, 0)}, start=0, end=1000)
    tree, _, _ = convert_cross(document, target, decisions=marker_decisions(document, target))
    assert_valid(tree, "1.8")
    assert tree.xpath("//adjust-transform/@rotation") == ["30"]
    assert tree.xpath("//adjust-transform/@scale") == ["1.25 1.25"]


def test_static_transform_filters_compose_instead_of_overwriting(tmp_path):
    document, target = xmeml(tmp_path), Target("fcpxml", "1.8")
    clip = document.tree.xpath("//video/track/clipitem")[0]
    add_xeffect(clip, "basic", {"rotation": "-30", "scale": "125"})
    add_xeffect(clip, "basic", {"rotation": "-10", "scale": "200"})
    add_xeffect(clip, "opacity", {"opacity": "50"})
    add_xeffect(clip, "opacity", {"opacity": "50"})
    tree, _, _ = convert_cross(document, target, decisions=marker_decisions(document, target))
    assert tree.xpath("//adjust-transform/@rotation") == ["40"]
    assert tree.xpath("//adjust-transform/@scale") == ["2.5 2.5"]
    assert tree.xpath("//adjust-blend/@amount") == ["0.25"]


@pytest.mark.parametrize("effectid,values,code", [("basic", {"rotation": "20"}, "TRANSFORM_UNSUPPORTED"), ("audiolevels", {"level": "0.5"}, "AUDIO_VOLUME_UNSUPPORTED")])
def test_partial_effect_span_requires_explicit_removal(tmp_path, effectid, values, code):
    document = xmeml(tmp_path)
    kind = "video" if effectid == "basic" else "audio"
    clip = document.tree.xpath(f"//{kind}/track/clipitem")[0]
    add_xeffect(clip, effectid, values, start=75, end=125)
    issues = analyze_cross(document, Target("fcpxml", "1.8"))
    issue = next(i for i in issues if i.code == code)
    assert issue.choices == {"drop_effect": "明确移除此处理；画面或声音可能改变", "stop": "停止转换"}


def test_channel_specific_volume_cannot_be_silently_merged(tmp_path):
    document = xmeml(tmp_path)
    add_xeffect(document.tree.xpath("//audio/track/clipitem")[0], "audiolevels", {"level": "0.5"})
    target = Target("fcpxml", "1.8")
    issues = analyze_cross(document, target)
    assert any(i.code == "LINK_GROUP_AUDIO_VOLUME" and not i.choices for i in issues)
    with pytest.raises(InterchangeError):
        convert_cross(document, target, decisions=marker_decisions(document, target))


@pytest.mark.parametrize("kind,start,points,source_range", [
    ("恒速", "3/2s", [("0s", "0s"), ("99/25s", "198/25s")], "3s..7s"),
    ("倒放", "3s", [("0s", "199/25s"), ("199/25s", "0s")], "124/25s..74/25s"),
    ("定格", "3600s", [("0s", "3s"), ("60000s", "3s"), ("1500001/25s", "1500001/25s")], "3s..3s"),
])
def test_native_time_maps_diagnose_used_interval_including_freeze_safety_tail(tmp_path, kind, start, points, source_range):
    document = fcpx(tmp_path, rate=Fraction(25))
    clip = document.tree.xpath("//asset-clip")[0]
    clip.set("start", start)
    clip.set("duration", "2s")
    mapping = etree.Element("timeMap", frameSampling="floor")
    clip.insert(0, mapping)
    for time, value in points:
        etree.SubElement(mapping, "timept", time=time, value=value, interp="linear")
    issues = analyze_cross(document, Target("xmeml", "5"))
    issue = next(i for i in issues if i.code == "RETIME_UNSUPPORTED")
    assert kind in issue.message
    assert issue.clip == "剪辑一" and issue.time == frame_time(108030, Fraction(25))
    assert not issue.choices
    assert "mapped_source_range=" + source_range in issue.details


def test_curve_retime_diagnostic_reports_segment_slopes(tmp_path):
    document = fcpx(tmp_path, rate=Fraction(25))
    clip = document.tree.xpath("//asset-clip")[0]
    clip.set("start", "0s")
    clip.set("duration", "4s")
    mapping = etree.Element("timeMap")
    clip.insert(0, mapping)
    for t, v in ((0, 0), (2, 4), (4, 5)):
        etree.SubElement(mapping, "timept", time=f"{t}s", value=f"{v}s", interp="linear")
    issue = next(i for i in analyze_cross(document, Target("xmeml", "5")) if i.code == "RETIME_UNSUPPORTED")
    assert "曲线或分段变速" in issue.message
    assert "segment_slopes=2,1/2" in issue.details
