from __future__ import annotations

import json
import shutil
import threading
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from lxml import etree

from xmlbridge.core import convert, inspect, plan_conversion
from xmlbridge.models import ConversionPlan, FCP_VERSIONS, Target
from helpers import (
    assert_valid, hash_file, make_fcpxml, make_xmeml, media_uris, output_xml, read_output,
    resolve_uri, timing_snapshot, write_tree,
)


def plan_for(source: Path, version: str, project_ids=None):
    document = inspect(source)
    return plan_conversion(document, Target("fcpxml", version), project_ids=project_ids)


def assert_success(result, version: str):
    assert result.status == "success", (result.message, result.issues)
    assert result.output_path and Path(result.output_path).exists()
    tree = read_output(result.output_path)
    assert tree.getroot().get("version") == version
    assert_valid(tree, version)
    return tree


@pytest.mark.parametrize("version", FCP_VERSIONS)
def test_fixture_is_official_dtd_valid_and_inspectable(tmp_path, version):
    source = write_tree(tmp_path / f"source-{version}.fcpxml", make_fcpxml(version))
    document = inspect(source)
    assert (document.family, document.version) == ("fcpxml", version)
    assert [(p.id, p.name) for p in document.projects] == [("p0", "项目 1")]
    assert not [issue for issue in document.issues if issue.severity == "error"]


@pytest.mark.parametrize("source_version,target_version", [
    pair for left, right in zip(FCP_VERSIONS, FCP_VERSIONS[1:])
    for pair in ((left, right), (right, left))
])
def test_every_adjacent_version_migration_validates_and_preserves_timing(
    tmp_path, source_version, target_version,
):
    source_tree = make_fcpxml(source_version, bookmark="Ym9va21hcms=")
    source = write_tree(tmp_path / "source.fcpxml", source_tree)
    original_hash = hash_file(source)
    result = convert(plan_for(source, target_version), {}, tmp_path / "out")
    output = assert_success(result, target_version)
    assert timing_snapshot(output) == timing_snapshot(source_tree)
    assert hash_file(source) == original_hash
    assert output.xpath("string(//bookmark)") == "Ym9va21hcms="


def test_18_to_19_moves_media_and_bookmark_and_makes_caption_default_explicit(tmp_path):
    source_tree = make_fcpxml("1.8", bookmark="Ym9va21hcms=", caption=True)
    source = write_tree(tmp_path / "v18.fcpxml", source_tree)
    output = assert_success(convert(plan_for(source, "1.9"), {}, tmp_path / "out"), "1.9")
    assert not output.xpath("//asset/@src | //asset/bookmark")
    assert output.xpath("//asset/media-rep/@src") == media_uris(source_tree)
    assert output.xpath("//asset/media-rep/@kind") == ["original-media"]
    assert output.xpath("//asset/media-rep/bookmark/text()") == ["Ym9va21hcms="]
    assert output.xpath("//caption/text/@placement") == ["bottom"]


def test_19_to_18_restores_asset_src_and_bookmark(tmp_path):
    source_tree = make_fcpxml("1.9", bookmark="Ym9va21hcms=", caption=True)
    source_tree.xpath("//caption/text")[0].set("placement", "top")
    source = write_tree(tmp_path / "v19.fcpxml", source_tree)
    output = assert_success(convert(plan_for(source, "1.8"), {}, tmp_path / "out"), "1.8")
    assert not output.xpath("//media-rep")
    assert output.xpath("//asset/@src") == media_uris(source_tree)
    assert output.xpath("//asset/bookmark/text()") == ["Ym9va21hcms="]
    assert output.xpath("//caption/text/@placement") == ["top"]


def test_multiple_media_representations_require_explicit_original_choice(tmp_path):
    tree = make_fcpxml("1.9")
    etree.SubElement(tree.xpath("//asset")[0], "media-rep", kind="proxy-media", src="file:///missing/proxy.mov")
    source = write_tree(tmp_path / "two-reps.fcpxml", tree)
    plan = plan_for(source, "1.8")
    choices = [issue for issue in plan.issues if "choose_original" in issue.choices]
    assert choices, plan.issues
    assert plan.blockers()
    blocked = convert(plan, {}, tmp_path / "blocked")
    assert blocked.status == "blocked" and not blocked.output_path
    decisions = {issue.id: "choose_original" for issue in choices}
    output = assert_success(convert(plan, decisions, tmp_path / "chosen"), "1.8")
    assert output.xpath("//asset/@src") == ["file:///missing/editorial-test.mov"]


def test_proxy_only_does_not_silently_become_original(tmp_path):
    tree = make_fcpxml("1.9")
    tree.xpath("//media-rep")[0].set("kind", "proxy-media")
    source = write_tree(tmp_path / "proxy-only.fcpxml", tree)
    plan = plan_for(source, "1.8")
    assert plan.blockers()
    result = convert(plan, {}, tmp_path / "out")
    assert result.status == "blocked" and not result.output_path


def test_ambiguous_original_media_cannot_be_chosen_implicitly(tmp_path):
    tree = make_fcpxml("1.9")
    etree.SubElement(tree.xpath("//asset")[0], "media-rep", kind="original-media", src="file:///missing/second-original.mov")
    source = write_tree(tmp_path / "two-originals.fcpxml", tree)
    plan = plan_for(source, "1.8")
    assert plan.blockers()
    result = convert(plan, {}, tmp_path / "out")
    assert result.status == "blocked" and not result.output_path


@pytest.mark.parametrize("attribute,value", [("sig", "fcp-signature"), ("suggestedFilename", "镜头.mov")])
def test_unrepresentable_media_metadata_requires_choice(tmp_path, attribute, value):
    tree = make_fcpxml("1.9")
    tree.xpath("//media-rep")[0].set(attribute, value)
    source = write_tree(tmp_path / "metadata.fcpxml", tree)
    plan = plan_for(source, "1.8")
    assert plan.blockers(), "1.8 cannot represent media-rep metadata; loss must be explicit"
    assert convert(plan, {}, tmp_path / "out").status == "blocked"


def test_new_element_is_blocked_on_downgrade(tmp_path):
    tree = make_fcpxml("1.10")
    etree.SubElement(tree.xpath("//resources")[0], "locator", id="r3", url="file:///missing/metadata")
    source = write_tree(tmp_path / "new-element.fcpxml", tree)
    plan = plan_for(source, "1.9")
    assert plan.blockers()
    assert convert(plan, {}, tmp_path / "out").status == "blocked"


def test_explicit_effect_removal_changes_only_the_approved_effect(tmp_path):
    tree = make_fcpxml("1.11")
    clip = tree.xpath("//asset-clip")[0]
    clip.insert(0, etree.Element("adjust-colorConform", enabled="1", conformType="conformHLGtoSDR",
                                 peakNitsOfPQSource="1000", peakNitsOfSDRToPQSource="100"))
    source = write_tree(tmp_path / "color-conform.fcpxml", tree)
    plan = plan_for(source, "1.10")
    drop_issues = [issue for issue in plan.issues if "drop" in issue.choices]
    assert len(drop_issues) == 1
    assert convert(plan, {drop_issues[0].id: "invalid-action"}, tmp_path / "bad").status == "blocked"
    output = assert_success(convert(plan, {drop_issues[0].id: "drop"}, tmp_path / "out"), "1.10")
    assert not output.xpath("//adjust-colorConform")
    assert timing_snapshot(output) == timing_snapshot(tree)
    assert output.xpath("//marker/@value") == tree.xpath("//marker/@value")


def test_repeated_lossy_items_each_require_their_own_issue_and_approval(tmp_path):
    tree = make_fcpxml("1.14")
    collection = etree.SubElement(tree.xpath("//event")[0], "smart-collection", name="智能集合", match="all")
    for value in ("transcript", "visual", "any"):
        etree.SubElement(collection, "match-analysis-type", value=value)
    source = write_tree(tmp_path / "three-losses.fcpxml", tree)
    plan = plan_for(source, "1.13")
    losses = [issue for issue in plan.issues if "match-analysis-type" in issue.message and "drop" in issue.choices]
    assert len(losses) == 3, losses
    assert len({issue.id for issue in losses}) == 3
    partial = convert(plan, {losses[0].id: "drop"}, tmp_path / "partial")
    assert partial.status == "blocked" and not partial.output_path
    output = assert_success(convert(plan, {issue.id: "drop" for issue in losses}, tmp_path / "all"), "1.13")
    assert not output.xpath("//match-analysis-type")


def test_changed_enum_is_blocked_in_upgrade_direction(tmp_path):
    tree = make_fcpxml("1.9")
    clip = tree.xpath("//asset-clip")[0]
    clip.insert(0, etree.Element("adjust-stabilization", type="alignment-360"))
    source = write_tree(tmp_path / "enum.fcpxml", tree)
    plan = plan_for(source, "1.10")
    assert plan.blockers(), "A higher version can also remove an accepted enum"
    assert convert(plan, {}, tmp_path / "out").status == "blocked"


def test_distinct_unsupported_properties_have_distinct_diagnostics(tmp_path):
    tree = make_fcpxml("1.14")
    asset = tree.xpath("//asset")[0]
    asset.set("auxVideoFlags", "1")
    asset.set("heroEyeOverride", "left")
    asset.set("videoSources", "2")
    source = write_tree(tmp_path / "properties.fcpxml", tree)
    plan = plan_for(source, "1.8")
    issues = [issue for issue in plan.issues if issue.code == "TARGET_ATTRIBUTE"]
    for property_name in ("auxVideoFlags", "heroEyeOverride", "videoSources"):
        assert any(property_name in issue.message for issue in issues), (property_name, issues)
    assert len({issue.id for issue in issues}) == len(issues)


@pytest.mark.parametrize("payload", [
    b"<fcpxml version='1.9'><resources>",
    b"<not-editorial/>",
    b"<fcpxml version='1.7'><resources/></fcpxml>",
])
def test_malformed_unknown_and_unsupported_older_input_return_errors(tmp_path, payload):
    source = tmp_path / "input.xml"
    source.write_bytes(payload)
    document = inspect(source)
    assert any(issue.severity == "error" for issue in document.issues)
    plan = plan_conversion(document, Target("fcpxml", "1.9"))
    assert plan.blockers()
    result = convert(plan, {}, tmp_path / "out")
    assert result.status == "blocked" and not result.output_path


def test_nonexistent_source_is_reported_without_raising(tmp_path):
    document = inspect(tmp_path / "not-found.fcpxml")
    assert any(issue.severity == "error" for issue in document.issues)
    assert plan_conversion(document, Target("fcpxml", "1.9")).blockers()


def test_unsupported_destination_version_is_blocked(tmp_path):
    source = write_tree(tmp_path / "source.fcpxml", make_fcpxml("1.9"))
    plan = plan_for(source, "1.15")
    assert plan.blockers()
    assert convert(plan, {}, tmp_path / "out").status == "blocked"


def test_source_schema_failure_blocks_even_if_target_would_accept_it(tmp_path):
    tree = make_fcpxml("1.9")
    tree.xpath("//asset")[0].set("inventedField", "must-not-be-ignored")
    source = write_tree(tmp_path / "invalid.fcpxml", tree, validate=False)
    document = inspect(source)
    assert any(issue.severity == "error" for issue in document.issues)
    result = convert(plan_conversion(document, Target("fcpxml", "1.10")), {}, tmp_path / "out")
    assert result.status == "blocked" and not result.output_path


def test_public_convert_cannot_bypass_source_validation_with_a_manual_plan(tmp_path):
    tree = make_fcpxml("1.9")
    # auxVideoFlags is valid in 1.10 but not in the declared 1.9 source format.
    tree.xpath("//asset")[0].set("auxVideoFlags", "1")
    source = write_tree(tmp_path / "invalid-source.fcpxml", tree, validate=False)
    document = inspect(source)
    assert any(issue.severity == "error" for issue in document.issues)
    result = convert(ConversionPlan(document, Target("fcpxml", "1.10")), {}, tmp_path / "out")
    assert result.status == "blocked" and not result.output_path


def test_public_convert_cannot_silently_export_empty_selection_with_a_manual_plan(tmp_path):
    source = write_tree(tmp_path / "projects.fcpxml", make_fcpxml("1.9", projects=2))
    document = inspect(source)
    result = convert(ConversionPlan(document, Target("fcpxml", "1.10"), project_ids=["missing"]), {}, tmp_path / "out")
    assert result.status == "blocked" and not result.output_path


def test_missing_idref_is_an_error(tmp_path):
    tree = make_fcpxml("1.9")
    tree.xpath("//asset-clip")[0].set("ref", "missingResource")
    source = write_tree(tmp_path / "missing-ref.fcpxml", tree, validate=False)
    document = inspect(source)
    assert any(issue.severity == "error" for issue in document.issues)
    assert plan_conversion(document, Target("fcpxml", "1.10")).blockers()


def test_offline_media_only_warns(tmp_path):
    source = write_tree(tmp_path / "offline.fcpxml", make_fcpxml("1.9"))
    document = inspect(source)
    assert any(issue.severity == "warning" for issue in document.issues)
    assert not [issue for issue in document.issues if issue.severity == "error"]
    assert_success(convert(plan_conversion(document, Target("fcpxml", "1.10")), {}, tmp_path / "out"), "1.10")


def test_project_selection_uses_inspection_ids_and_excludes_unselected_projects(tmp_path):
    source = write_tree(tmp_path / "projects.fcpxml", make_fcpxml("1.9", projects=2))
    document = inspect(source)
    assert [project.id for project in document.projects] == ["p0", "p1"]
    output = assert_success(convert(plan_conversion(document, Target("fcpxml", "1.10"), project_ids=["p1"]), {}, tmp_path / "out"), "1.10")
    assert output.xpath("//project/@name") == ["项目 2"]


def test_project_selection_keeps_transitive_compound_media_dependencies(tmp_path):
    tree = make_fcpxml("1.9", projects=2, frame_duration="1/25s", duration="10s", tc_format="NDF")
    media = etree.SubElement(tree.xpath("//resources")[0], "media", id="r3", name="复合来源")
    nested_sequence = etree.SubElement(media, "sequence", format="r1", duration="10s", tcStart="0s", tcFormat="NDF")
    nested_spine = etree.SubElement(nested_sequence, "spine")
    etree.SubElement(nested_spine, "asset-clip", ref="r2", offset="0s", start="3600s", duration="10s")
    spine = tree.xpath("//project")[1].find("sequence/spine")
    spine.remove(spine[0])
    etree.SubElement(spine, "ref-clip", ref="r3", offset="3600s", start="0s", duration="10s")
    source = write_tree(tmp_path / "compound-projects.fcpxml", tree)
    output = assert_success(convert(plan_for(source, "1.10", ["p1"]), {}, tmp_path / "out"), "1.10")
    assert output.xpath("//project/@name") == ["项目 2"]
    assert output.xpath("//project//ref-clip/@ref") == ["r3"]
    assert output.xpath("/fcpxml/resources/media[@id='r3']//asset-clip/@ref") == ["r2"]
    assert output.xpath("/fcpxml/resources/asset[@id='r2']")
    assert output.xpath("/fcpxml/resources/format[@id='r1']")


@pytest.mark.parametrize("selected", [None, ["p1"]])
def test_cross_format_multiple_projects_keep_individual_timing_and_markers(tmp_path, selected):
    tree = make_fcpxml("1.9", projects=2, frame_duration="1/25s", duration="10s", tc_format="NDF")
    projects = tree.xpath("//project")
    for index, project in enumerate(projects):
        sequence = project.find("sequence")
        clip = sequence.find("spine/asset-clip")
        marker = clip.find("marker")
        sequence.set("tcStart", f"{3600 + index * 3600}s")
        sequence.set("duration", f"{10 + index * 2}s")
        clip.set("offset", sequence.get("tcStart"))
        clip.set("start", f"{3600 + index * 5}s")
        clip.set("duration", sequence.get("duration"))
        marker.set("start", f"{3602 + index * 7}s")
        marker.set("duration", "1/25s")
        marker.set("value", f"独立核对 {index + 1}")
    source = write_tree(tmp_path / "cross-projects.fcpxml", tree)
    plan = plan_conversion(inspect(source), Target("xmeml", "5"), project_ids=selected)
    assert not plan.blockers(), plan.issues
    converted = convert(plan, {}, tmp_path / "out")
    output = assert_success(converted, "5")
    sequences = output.xpath("//sequence[not(ancestor::sequence)]")
    expected_indices = [0, 1] if selected is None else [1]
    assert len(sequences) == len(expected_indices)
    for index, sequence in zip(expected_indices, sequences):
        assert sequence.findtext("name") == f"项目 {index + 1}"
        assert int(sequence.findtext("duration")) == (10 + index * 2) * 25
        assert int(sequence.findtext("timecode/frame")) == (3600 + index * 3600) * 25
        clip = sequence.find("media/video/track/clipitem")
        assert int(clip.findtext("start")) == 0
        assert int(clip.findtext("end")) == (10 + index * 2) * 25
        assert int(clip.findtext("in")) == index * 5 * 25
        assert int(clip.findtext("out")) == (10 + index * 7) * 25
        assert clip.findtext("marker/name") == f"独立核对 {index + 1}"
        assert int(clip.findtext("marker/in")) == (2 + index * 2) * 25
    reverse_plan = plan_conversion(inspect(output_xml(converted.output_path)), Target("fcpxml", "1.9"))
    reverse_decisions = {issue.id: "rec709" for issue in reverse_plan.issues
                         if issue.code == "COLOR_SPACE_DEFAULT" and "rec709" in issue.choices}
    assert not reverse_plan.blockers(reverse_decisions), reverse_plan.issues
    returned = assert_success(convert(reverse_plan, reverse_decisions, tmp_path / "roundtrip"), "1.9")
    returned_projects = returned.xpath("//project")
    assert len(returned_projects) == len(expected_indices)
    exact = lambda value: Fraction(value.removesuffix("s"))
    for index, project in zip(expected_indices, returned_projects):
        sequence = project.find("sequence")
        clip = sequence.find("spine/asset-clip")
        assert project.get("name") == f"项目 {index + 1}"
        assert exact(sequence.get("duration")) == 10 + index * 2
        assert exact(sequence.get("tcStart")) == 3600 + index * 3600
        assert exact(clip.get("start")) == 3600 + index * 5
        assert exact(clip.find("marker").get("start")) == 3602 + index * 7


def test_unknown_project_selection_is_blocked(tmp_path):
    source = write_tree(tmp_path / "projects.fcpxml", make_fcpxml("1.9"))
    plan = plan_for(source, "1.10", ["not-a-project"])
    assert plan.blockers()
    assert convert(plan, {}, tmp_path / "out").status == "blocked"


@pytest.mark.parametrize("frame_duration,duration,tc_format", [
    ("1/24s", "86400s", "NDF"),
    ("1001/24000s", "1001000/24s", "NDF"),
    ("1001/30000s", "1001000/30s", "DF"),
    ("1001/60000s", "1001000/60s", "DF"),
])
def test_fractional_and_long_timeline_values_do_not_change(tmp_path, frame_duration, duration, tc_format):
    tree = make_fcpxml("1.8", frame_duration=frame_duration, duration=duration, tc_format=tc_format)
    source = write_tree(tmp_path / "timing.fcpxml", tree)
    output = assert_success(convert(plan_for(source, "1.14"), {}, tmp_path / "out"), "1.14")
    assert timing_snapshot(output) == timing_snapshot(tree)


def test_mixed_source_and_sequence_frame_rates_keep_their_own_timebases(tmp_path):
    tree = make_fcpxml("1.9", frame_duration="1/25s", duration="1001s", tc_format="NDF")
    resources = tree.xpath("//resources")[0]
    resources.insert(1, etree.Element("format", id="r3", frameDuration="1001/30000s", width="1920", height="1080"))
    tree.xpath("//asset")[0].set("format", "r3")
    source = write_tree(tmp_path / "mixed-fps.fcpxml", tree)
    output = assert_success(convert(plan_for(source, "1.14"), {}, tmp_path / "out"), "1.14")
    assert timing_snapshot(output) == timing_snapshot(tree)
    assert output.xpath("//asset/@format") == ["r3"]


@pytest.mark.parametrize("xpath,attribute,value", [
    ("//asset-clip", "duration", "not-a-time"),
    ("//asset-clip", "duration", "-1s"),
    ("//format", "frameDuration", "0s"),
])
def test_structurally_valid_but_invalid_timing_is_an_error(tmp_path, xpath, attribute, value):
    tree = make_fcpxml("1.9")
    tree.xpath(xpath)[0].set(attribute, value)
    source = write_tree(tmp_path / "invalid-time.fcpxml", tree)
    document = inspect(source)
    assert any(issue.severity == "error" for issue in document.issues)
    assert convert(plan_conversion(document, Target("fcpxml", "1.10")), {}, tmp_path / "out").status == "blocked"


def test_declared_source_range_cannot_exceed_asset_duration(tmp_path):
    tree = make_fcpxml("1.9", duration="10s", frame_duration="1/25s", tc_format="NDF")
    tree.xpath("//asset")[0].set("duration", "5s")
    source = write_tree(tmp_path / "range.fcpxml", tree)
    document = inspect(source)
    assert any(issue.severity == "error" for issue in document.issues), document.issues
    result = convert(plan_conversion(document, Target("fcpxml", "1.10")), {}, tmp_path / "out")
    assert result.status == "blocked" and not result.output_path


def test_relative_media_path_preserves_the_same_real_target(tmp_path):
    media = tmp_path / "输入 目录" / "素材 & 一号.mov"
    media.parent.mkdir()
    media.write_bytes(b"fixture-media-path-only")
    source = write_tree(media.parent / "source.fcpxml", make_fcpxml("1.9", src="%E7%B4%A0%E6%9D%90%20%26%20%E4%B8%80%E5%8F%B7.mov"))
    output_path = tmp_path / "different" / "deep" / "out"
    result = convert(plan_for(source, "1.10"), {}, output_path)
    output = assert_success(result, "1.10")
    assert resolve_uri(media_uris(output)[0], output_xml(result.output_path)) == media.resolve()


def test_fcpxmld_keeps_required_relative_dependency(tmp_path):
    bundle = tmp_path / "交接.fcpxmld"
    dependency = bundle / "Media" / "素材.mov"
    dependency.parent.mkdir(parents=True)
    dependency.write_bytes(b"bundle-dependency")
    sidecar = bundle / "Resources" / "需要的模板.moti"
    sidecar.parent.mkdir()
    sidecar.write_bytes(b"motion-sidecar-fixture")
    source_xml = write_tree(bundle / "Info.fcpxml", make_fcpxml("1.9", src="Media/%E7%B4%A0%E6%9D%90.mov"))
    source_hash = hash_file(source_xml)
    result = convert(plan_for(bundle, "1.10"), {}, tmp_path / "out")
    output = assert_success(result, "1.10")
    resolved = resolve_uri(media_uris(output)[0], output_xml(result.output_path))
    assert resolved.exists() and resolved.read_bytes() == b"bundle-dependency"
    output_bundle = Path(result.output_path)
    assert output_bundle.is_dir() and output_bundle.suffix == ".fcpxmld"
    assert (output_bundle / "Resources" / sidecar.name).read_bytes() == sidecar.read_bytes()
    assert hash_file(source_xml) == source_hash


def test_fcpxmld_output_remains_usable_after_the_package_is_moved(tmp_path):
    bundle = tmp_path / "input.fcpxmld"
    dependency = bundle / "Media" / "素材.mov"
    dependency.parent.mkdir(parents=True)
    dependency.write_bytes(b"portable-bundle-media")
    write_tree(bundle / "Info.fcpxml", make_fcpxml("1.9", src="Media/%E7%B4%A0%E6%9D%90.mov"))
    result = convert(plan_for(bundle, "1.10"), {}, tmp_path / "original-output")
    assert_success(result, "1.10")
    relocated = tmp_path / "relocated" / "moved.fcpxmld"
    relocated.parent.mkdir()
    shutil.move(result.output_path, relocated)
    shutil.rmtree(bundle)
    shutil.rmtree(tmp_path / "original-output")
    output = read_output(relocated)
    resolved = resolve_uri(media_uris(output)[0], output_xml(relocated))
    assert resolved.is_relative_to(relocated)
    assert resolved.read_bytes() == b"portable-bundle-media"


def test_precancelled_conversion_does_not_write_success_artifacts(tmp_path):
    source = write_tree(tmp_path / "source.fcpxml", make_fcpxml("1.9"))
    event = threading.Event()
    event.set()
    output_dir = tmp_path / "out"
    result = convert(plan_for(source, "1.10"), {}, output_dir, cancel_event=event)
    assert result.status == "cancelled" and not result.output_path
    assert not list(output_dir.rglob("*.fcpxml"))


def test_source_is_never_overwritten_when_output_directory_is_source_directory(tmp_path):
    source = write_tree(tmp_path / "source.fcpxml", make_fcpxml("1.9"))
    original_hash = hash_file(source)
    result = convert(plan_for(source, "1.10"), {}, tmp_path)
    assert_success(result, "1.10")
    assert output_xml(result.output_path).resolve() != source.resolve()
    assert hash_file(source) == original_hash


def test_source_changed_after_inspection_requires_a_new_plan(tmp_path):
    source = write_tree(tmp_path / "source.fcpxml", make_fcpxml("1.9"))
    plan = plan_for(source, "1.10")
    changed_tree = make_fcpxml("1.9", duration="10s")
    write_tree(source, changed_tree)
    changed_hash = hash_file(source)
    result = convert(plan, {}, tmp_path / "out")
    assert result.status == "blocked" and not result.output_path
    assert hash_file(source) == changed_hash


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_external_entity_never_reads_local_secret(tmp_path, encoding):
    secret = tmp_path / "secret.txt"
    secret.write_text("XXE_SENTINEL_MUST_NOT_APPEAR")
    source = tmp_path / "entity.fcpxml"
    payload = etree.tostring(make_fcpxml("1.9", caption=True), encoding="unicode").replace("测试字幕", "&external;")
    source.write_bytes(f'<!DOCTYPE fcpxml [<!ENTITY external SYSTEM "{secret.as_uri()}">]>{payload}'.encode(encoding))
    document = inspect(source)
    assert any(issue.severity == "error" for issue in document.issues)
    if document.tree is not None:
        assert "XXE_SENTINEL_MUST_NOT_APPEAR" not in etree.tostring(document.tree).decode()
    plan = plan_conversion(document, Target("fcpxml", "1.10"))
    result = convert(plan, {}, tmp_path / "out")
    if result.status == "success":
        assert b"XXE_SENTINEL_MUST_NOT_APPEAR" not in output_xml(result.output_path).read_bytes()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_external_dtd_does_not_trigger_network_request(tmp_path, encoding):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<!ELEMENT fcpxml ANY>")

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = tmp_path / "external-dtd.fcpxml"
        payload = etree.tostring(make_fcpxml("1.9"), encoding="unicode")
        source.write_bytes(
            (f'<!DOCTYPE fcpxml SYSTEM "http://127.0.0.1:{server.server_port}/evil.dtd">' + payload).encode(encoding)
        )
        document = inspect(source)
        plan_conversion(document, Target("fcpxml", "1.10"))
        assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_conversion_reports_do_not_claim_host_validation(tmp_path):
    source = write_tree(tmp_path / "source.fcpxml", make_fcpxml("1.9"))
    result = convert(plan_for(source, "1.10"), {}, tmp_path / "out")
    assert_success(result, "1.10")
    assert result.report_html and Path(result.report_html).exists()
    assert result.report_json and Path(result.report_json).exists()
    report = json.loads(Path(result.report_json).read_text())
    assert isinstance(report, dict)
    assert not result.checks.get("host_validated", False)
    from lxml import html as html_parser
    page = html_parser.fromstring(Path(result.report_html).read_text())
    assert page.xpath('//meta[@name="color-scheme"]/@content')==["light"]
    assert "尚未验证" in page.xpath('string(//div[contains(@class,"pending")])')
    assert "未对本次输出执行剪辑软件导入" in page.text_content()
    assert page.xpath('//dl/dd')[0].text==str(source.resolve())


@pytest.mark.parametrize("source_version,target_version", [("4", "5"), ("5", "4")])
def test_legacy_version_migration_preserves_frame_counts_and_df_timecode(tmp_path, source_version, target_version):
    tree = make_xmeml(source_version)
    source = write_tree(tmp_path / "legacy.xml", tree)
    original_hash = hash_file(source)
    plan = plan_conversion(inspect(source), Target("xmeml", target_version))
    result = convert(plan, {}, tmp_path / "out")
    output = assert_success(result, target_version)
    timing_tags = {"duration", "start", "end", "in", "out", "timebase", "ntsc", "string", "frame", "displayformat"}
    snapshot = lambda t: [(node.tag, node.text) for node in t.iter() if node.tag in timing_tags]
    assert snapshot(output) == snapshot(tree)
    assert hash_file(source) == original_hash


@pytest.mark.parametrize("tag,parent_path", [
    ("subclipmasterid", "//clipitem"),
    ("parameterspecifier", "//parameter"),
    ("anchoroffset", "//keyframe"),
])
def test_legacy_v5_additions_block_v4_downgrade(tmp_path, tag, parent_path):
    tree = make_xmeml("5")
    if tag != "subclipmasterid":
        clip = tree.xpath("//clipitem")[0]
        effect = etree.SubElement(etree.SubElement(clip, "filter"), "effect")
        parameter = etree.SubElement(effect, "parameter")
        if tag == "anchoroffset":
            etree.SubElement(parameter, "keyframe")
    etree.SubElement(tree.xpath(parent_path)[0], tag).text = "legacy-extra"
    source = write_tree(tmp_path / "legacy-extra.xml", tree)
    plan = plan_conversion(inspect(source), Target("xmeml", "4"))
    assert plan.blockers()
    result = convert(plan, {}, tmp_path / "out")
    assert result.status == "blocked" and not result.output_path


def test_legacy_missing_file_reference_is_an_error(tmp_path):
    tree = make_xmeml("5")
    file = tree.xpath("//file")[0]
    for child in list(file):
        file.remove(child)
    source = write_tree(tmp_path / "legacy-missing-file.xml", tree)
    document = inspect(source)
    assert any(issue.severity == "error" for issue in document.issues)
    assert plan_conversion(document, Target("xmeml", "4")).blockers()


@pytest.mark.parametrize("tag,value", [("end", "not-frames"), ("out", "-1")])
def test_legacy_invalid_frame_range_is_an_error(tmp_path, tag, value):
    tree = make_xmeml("5")
    tree.xpath(f"//clipitem/{tag}")[0].text = value
    source = write_tree(tmp_path / "legacy-invalid-range.xml", tree)
    document = inspect(source)
    assert any(issue.severity == "error" for issue in document.issues), document.issues
    result = convert(plan_conversion(document, Target("xmeml", "4")), {}, tmp_path / "out")
    assert result.status == "blocked" and not result.output_path
