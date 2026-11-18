"""Independent, official-DTD-valid regression coverage for complex editorial XML.

These tests prove native XML preservation and conservative interchange decisions.
They do not substitute for a real host's visual or audible acceptance.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest
from lxml import etree

from helpers import assert_valid, hash_file, make_fcpxml, read_output, write_tree
from xmlbridge.core import convert, inspect, plan_conversion
from xmlbridge.models import FCP_VERSIONS, Target


FEATURES = ("nested", "multicam", "speed2", "reverse", "freeze", "title", "transition", "adjustments")


def native_fixture(feature: str, version: str = "1.9", *, projects: int = 1):
    tree = make_fcpxml(version, projects=projects, frame_duration="1/25s", duration="10s", tc_format="NDF")
    for marker in tree.xpath("//marker"):
        marker.set("duration", "1/25s")
    resources = tree.find("resources")
    project = tree.xpath("//project")[0]
    project.set("name", "复杂工程")
    sequence = project.find("sequence")
    spine = sequence.find("spine")
    clip = spine[0]
    clip.set("name", "测试片段")
    for marker in list(clip.findall("marker")):
        clip.remove(marker)

    if feature == "nested":
        inner = etree.SubElement(resources, "media", id="r10", name="内部复合")
        inner_sequence = etree.SubElement(inner, "sequence", format="r1", duration="10s", tcStart="0s", tcFormat="NDF")
        inner_spine = etree.SubElement(inner_sequence, "spine")
        etree.SubElement(inner_spine, "asset-clip", ref="r2", name="内部素材", offset="0s", start="3600s", duration="10s")
        outer = etree.SubElement(resources, "media", id="r11", name="外层复合")
        outer_sequence = etree.SubElement(outer, "sequence", format="r1", duration="10s", tcStart="0s", tcFormat="NDF")
        outer_spine = etree.SubElement(outer_sequence, "spine")
        etree.SubElement(outer_spine, "ref-clip", ref="r10", name="中间引用", offset="0s", start="0s", duration="10s")
        spine.remove(clip)
        etree.SubElement(spine, "ref-clip", ref="r11", name="测试片段", offset="3600s", start="0s", duration="10s")
    elif feature == "multicam":
        media = etree.SubElement(resources, "media", id="r20", name="多机位源")
        multicam = etree.SubElement(media, "multicam", format="r1", duration="10s", tcStart="0s", tcFormat="NDF")
        for ident, name in (("A", "主机"), ("B", "侧机")):
            angle = etree.SubElement(multicam, "mc-angle", angleID=ident, name=name)
            etree.SubElement(angle, "asset-clip", ref="r2", name=f"机位 {ident}", offset="0s", start="3600s", duration="10s")
        spine.remove(clip)
        mc = etree.SubElement(spine, "mc-clip", ref="r20", name="测试片段", offset="3600s", start="0s", duration="10s")
        etree.SubElement(mc, "mc-source", angleID="A", srcEnable="video")
        etree.SubElement(mc, "mc-source", angleID="B", srcEnable="audio")
    elif feature in ("speed2", "reverse", "freeze"):
        clip.set("start", "0s")
        mapping = etree.SubElement(clip, "timeMap", frameSampling="nearest-neighbor", preservesPitch="1")
        values = {"speed2": ("3600s", "3620s"), "reverse": ("3620s", "3600s"), "freeze": ("3605s", "3605s")}[feature]
        etree.SubElement(mapping, "timept", time="0s", value=values[0], interp="linear")
        etree.SubElement(mapping, "timept", time="10s", value=values[1], interp="linear")
    elif feature == "title":
        etree.SubElement(resources, "effect", id="r30", name="Basic Title", uid=".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti")
        title = etree.SubElement(clip, "title", ref="r30", name="测试片段标题", lane="1", offset="3601s", start="0s", duration="2s")
        text = etree.SubElement(title, "text", placement="bottom")
        etree.SubElement(text, "text-style", ref="ts1").text = "中文标题 & A/B"
        style = etree.SubElement(title, "text-style-def", id="ts1")
        etree.SubElement(style, "text-style", font="Helvetica", fontSize="72", fontColor="1 1 1 1", alignment="center")
    elif feature == "transition":
        etree.SubElement(resources, "effect", id="r31", name="Cross Dissolve", uid=".../Transitions.localized/Dissolves.localized/Cross Dissolve.localized/Cross Dissolve.motr")
        sequence.set("duration", "9s")
        clip.set("duration", "5s")
        transition = etree.SubElement(spine, "transition", name="测试转场", offset="3604s", duration="1s")
        filt = etree.SubElement(transition, "filter-video", ref="r31", name="Cross Dissolve")
        etree.SubElement(filt, "param", name="Amount", key="1", value="1")
        etree.SubElement(spine, "asset-clip", ref="r2", name="第二片段", offset="3604s", start="3610s", duration="5s")
    elif feature == "adjustments":
        etree.SubElement(clip, "adjust-transform", position="0 0", scale="1.25 1.25", rotation="-37.5", anchor="0 0")
        etree.SubElement(clip, "adjust-blend", mode="normal", amount="0.75")
        etree.SubElement(clip, "adjust-volume", amount="-6dB")
    else:
        raise ValueError(feature)
    assert_valid(tree, version)
    return tree


def element_snapshot(node):
    """All explicit parameters and child order, independent of pretty printing."""
    return (node.tag, tuple(sorted(node.attrib.items())), (node.text or "").strip(), tuple(element_snapshot(child) for child in node))


def editorial_snapshot(tree):
    return tuple(element_snapshot(node) for node in tree.xpath("/fcpxml/library | /fcpxml/resources/media | /fcpxml/resources/effect"))


NATIVE_ROUTES = sorted(set(
    [(version, version) for version in FCP_VERSIONS]
    + [(source, target) for source, target in zip(FCP_VERSIONS, FCP_VERSIONS[1:])]
    + [(target, source) for source, target in zip(FCP_VERSIONS, FCP_VERSIONS[1:])]
    + [(edge, version) for edge in ("1.8", "1.14") for version in FCP_VERSIONS]
))


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("source_version,target_version", NATIVE_ROUTES)
def test_native_complex_features_survive_same_version_adjacent_and_full_span(
    tmp_path, feature, source_version, target_version,
):
    source_tree = native_fixture(feature, source_version)
    source = write_tree(tmp_path / "source.fcpxml", source_tree)
    original_hash = hash_file(source)
    document = inspect(source)
    assert not [issue for issue in document.issues if issue.severity == "error"]
    plan = plan_conversion(document, Target("fcpxml", target_version))
    assert not plan.blockers(), plan.issues
    result = convert(plan, {}, tmp_path / "out")
    assert result.status == "success", (result.message, result.issues)
    output = read_output(result.output_path)
    assert_valid(output, target_version)
    assert output.getroot().get("version") == target_version
    assert editorial_snapshot(output) == editorial_snapshot(source_tree)
    assert result.checks["timeline"] == "passed"
    assert result.checks["structure"] == "passed"
    assert result.checks["host"] == "not_run"
    assert hash_file(source) == original_hash


@pytest.mark.parametrize("feature,code,clip_name", [
    ("nested", "UNSUPPORTED_REF_CLIP", "测试片段"),
    ("multicam", "UNSUPPORTED_MC_CLIP", "测试片段"),
    ("speed2", "RETIME_UNSUPPORTED", "测试片段"),
    ("reverse", "RETIME_UNSUPPORTED", "测试片段"),
    ("freeze", "RETIME_UNSUPPORTED", "测试片段"),
    ("title", "UNSUPPORTED_TITLE", "测试片段标题"),
    ("transition", "UNSUPPORTED_TRANSITION", "测试转场"),
])
def test_unmapped_complex_features_block_with_specific_project_clip_and_time(tmp_path, feature, code, clip_name):
    source = write_tree(tmp_path / "complex.fcpxml", native_fixture(feature))
    plan = plan_conversion(inspect(source), Target("xmeml", "5"))
    issue = next(issue for issue in plan.issues if issue.code == code)
    assert issue.project == "复杂工程"
    assert issue.clip == clip_name
    expected_time = {"title": "3601s", "transition": "3604s"}.get(feature, "3600s")
    assert expected_time in issue.time
    assert issue.location.startswith("/fcpxml/")
    assert issue.severity == "error" and not issue.choices
    result = convert(plan, {}, tmp_path / "out")
    assert result.status == "blocked" and not result.output_path


@pytest.mark.parametrize("feature", ("nested", "multicam"))
def test_project_filter_preserves_transitive_resource_dependencies(tmp_path, feature):
    source_tree = native_fixture(feature, projects=2)
    source = write_tree(tmp_path / "projects.fcpxml", source_tree)
    document = inspect(source)
    assert [project.name for project in document.projects] == ["复杂工程", "项目 2"]
    plan = plan_conversion(document, Target("fcpxml", "1.8"), project_ids=["p0"])
    result = convert(plan, {}, tmp_path / "out")
    assert result.status == "success", result.issues
    output = read_output(result.output_path)
    assert_valid(output, "1.8")
    assert output.xpath("//project/@name") == ["复杂工程"]
    source_resources = source_tree.xpath("/fcpxml/resources/media")
    assert [element_snapshot(node) for node in output.xpath("/fcpxml/resources/media")] == [element_snapshot(node) for node in source_resources]
    ids = set(output.xpath("//*[@id]/@id"))
    assert set(output.xpath("//@ref | //@format")) <= ids
    assert output.xpath("//resources/asset[@id='r2']")
    if feature == "nested":
        assert output.xpath("//project/sequence/spine/ref-clip/@ref") == ["r11"]
        assert output.xpath("//media[@id='r11']/sequence/spine/ref-clip/@ref") == ["r10"]
        assert output.xpath("//media[@id='r10']/sequence/spine/asset-clip/@ref") == ["r2"]
    else:
        assert output.xpath("//mc-source/@angleID") == ["A", "B"]
        assert output.xpath("//media[@id='r20']/multicam/mc-angle/@angleID") == ["A", "B"]


@pytest.mark.parametrize("feature", ("nested", "multicam", "speed2", "title", "transition"))
def test_cross_selection_of_other_project_excludes_unsupported_timeline(tmp_path, feature):
    source = write_tree(tmp_path / "selection.fcpxml", native_fixture(feature, projects=2))
    target = Target("xmeml", "5")
    plan = plan_conversion(inspect(source), target, project_ids=["p1"])
    assert not plan.blockers(), plan.issues
    result = convert(plan, {}, tmp_path / "out")
    assert result.status == "success", result.issues
    output = read_output(result.output_path)
    assert_valid(output, "5")
    assert output.xpath("//sequence/name/text()") == ["项目 2"]


@pytest.mark.parametrize("rotation", ["-37.5", "90", "360", "0"])
@pytest.mark.parametrize("volume", ["-6dB", "3dB", "-120dB", "-infdB", "0dB"])
def test_cross_static_rotation_and_volume_use_legacy_units_and_round_trip(tmp_path, rotation, volume):
    tree = native_fixture("adjustments")
    tree.xpath("//adjust-transform")[0].set("rotation", rotation)
    tree.xpath("//adjust-volume")[0].set("amount", volume)
    source = write_tree(tmp_path / "adjustments.fcpxml", tree)
    document = inspect(source)
    plan = plan_conversion(document, Target("xmeml", "5"))
    assert not plan.blockers(), plan.issues
    result = convert(plan, {}, tmp_path / "legacy")
    assert result.status == "success", result.issues
    legacy = read_output(result.output_path)
    assert_valid(legacy, "5")
    assert legacy.xpath("//video/track/clipitem/filter/effect/parameter[parameterid='scale']/value/text()") == ["125"]
    assert legacy.xpath("//video/track/clipitem/filter/effect/parameter[parameterid='opacity']/value/text()") == ["75"]
    rotations = legacy.xpath("//video/track/clipitem/filter/effect/parameter[parameterid='rotation']/value/text()")
    if Fraction(rotation):
        # The two dialects have opposite rotation signs (verified against the
        # same Resolve +30-degree clip's native FCPXML and XMEML exports).
        assert len(rotations) == 1 and Fraction(rotations[0]) == -Fraction(rotation)
    else:
        assert all(Fraction(value) == 0 for value in rotations)
    gains = legacy.xpath("//audio/track/clipitem/filter/effect[effectid='audiolevels']/parameter[parameterid='level']/value/text()")
    if volume != "0dB":
        assert len(gains) == 2, "Both linked source channels must receive the volume"
        if volume == "-infdB":
            assert all(Decimal(value) == 0 for value in gains)
        else:
            with localcontext() as context:
                context.prec = 40
                measured = [20 * Decimal(value).log10() for value in gains]
            assert all(abs(value - Decimal(volume.removesuffix("dB"))) < Decimal("0.000000001") for value in measured)
    back_plan = plan_conversion(inspect(result.output_path), Target("fcpxml", "1.9"))
    decisions = {issue.id: "rec709" for issue in back_plan.issues if issue.code == "COLOR_SPACE_DEFAULT"}
    assert not back_plan.blockers(decisions), back_plan.issues
    back_result = convert(back_plan, decisions, tmp_path / "back")
    assert back_result.status == "success", back_result.issues
    back = read_output(back_result.output_path)
    assert_valid(back, "1.9")
    assert back.xpath("//asset-clip/adjust-transform/@scale") == ["1.25 1.25"]
    assert Fraction(back.xpath("string(//asset-clip/adjust-transform/@rotation)") or "0") == Fraction(rotation)
    assert back.xpath("//asset-clip/adjust-blend/@amount") == ["0.75"]
    amounts = back.xpath("//asset-clip/adjust-volume/@amount")
    if volume == "-infdB":
        assert amounts == ["-infdB"]
    elif volume != "0dB":
        assert len(amounts) == 1
        assert abs(Decimal(amounts[0].removesuffix("dB")) - Decimal(volume.removesuffix("dB"))) <= Decimal("0.000000001")
    assert back.xpath("//project/sequence/@duration") == ["10s"]
    clip = back.xpath("//project/sequence/spine/asset-clip")[0]
    assert Fraction(clip.get("start").removesuffix("s")) == 3600
    assert Fraction(clip.get("duration").removesuffix("s")) == 10

