import json
from pathlib import Path

from lxml import etree

from helpers import make_fcpxml, read_output, write_tree
from xmlbridge.core import convert, inspect, plan_conversion
from xmlbridge.models import Target
from xmlbridge.native_bridge import handle


def test_risky_downgrade_preserves_unknown_content_and_reports_failed_structure(tmp_path):
    tree = make_fcpxml("1.9")
    etree.SubElement(tree.find("resources"), "studio-extension", setting="keep-me")
    source = tmp_path / "中文源.fcpxml"
    tree.write(str(source), encoding="utf-8", xml_declaration=True)
    original = source.read_bytes()
    plan = plan_conversion(inspect(source), Target("fcpxml", "1.8"), allow_risky=True)
    assert convert(plan, {}, tmp_path / "strict").status == "blocked"
    result = convert(plan, {}, tmp_path / "risky", allow_risky=True)
    assert result.status == "exported_with_issues", result.message
    output = read_output(result.output_path)
    assert output.getroot().get("version") == "1.8"
    assert output.find("resources/asset").get("src")
    assert output.find("resources/studio-extension").get("setting") == "keep-me"
    report = json.loads(Path(result.report_json).read_text())
    assert report["checks"]["structure"] == "failed"
    assert report["checks"]["host"] == "not_run"
    assert "SOURCE_SCHEMA" in {i["code"] for i in report["issues"]}
    assert source.read_bytes() == original


def test_missing_reference_can_export_but_timeline_cannot_pass(tmp_path):
    tree = make_fcpxml("1.9")
    tree.find(".//asset-clip").set("ref", "missing")
    source = tmp_path / "missing.fcpxml"
    tree.write(str(source), encoding="utf-8", xml_declaration=True)
    result = convert(plan_conversion(inspect(source), Target("fcpxml", "1.8")), {},
                     tmp_path / "out", allow_risky=True)
    assert result.status == "exported_with_issues"
    assert result.checks["timeline"] == "failed"
    assert result.checks["structure"] == "failed"


def test_explicit_stop_and_unreadable_input_still_stop(tmp_path):
    tree = make_fcpxml("1.9")
    asset = tree.find("resources/asset")
    etree.SubElement(asset, "media-rep", kind="proxy-media", src="file:///proxy.mov")
    source = write_tree(tmp_path / "proxy.fcpxml", tree)
    plan = plan_conversion(inspect(source), Target("fcpxml", "1.8"), allow_risky=True)
    issue = next(i for i in plan.issues if i.code == "MEDIA_REP_LOSS")
    stopped = convert(plan, {issue.id: "stop"}, tmp_path / "stop", allow_risky=True)
    assert stopped.status == "blocked" and not stopped.output_path
    result = convert(plan, {}, tmp_path / "chosen", allow_risky=True)
    assert result.status == "success"
    assert not read_output(result.output_path).xpath("//media-rep")
    bad = tmp_path / "bad.xml"
    bad.write_text("<fcpxml")
    result = convert(plan_conversion(inspect(bad), Target("fcpxml", "1.8")), {},
                     tmp_path / "broken", allow_risky=True)
    assert result.status == "blocked" and not result.output_path


def test_native_package_picker_input_projects_and_cancel(tmp_path):
    package = tmp_path / "含中文.fcpxmld"
    package.mkdir()
    write_tree(package / "Info.fcpxml", make_fcpxml("1.10", projects=2))
    request = {"action": "scan", "paths": [str(package), str(package / "Info.fcpxml")],
               "target": {"family": "fcpxml", "version": "1.8", "application": "通用"}, "allow_risky": True}
    response = handle(request)
    assert len(response["entries"]) == 1
    entry = response["entries"][0]
    assert len(entry["projects"]) == 2 and entry["readable"]
    converted = handle({**request, "action": "convert", "path": entry["path"],
                        "input_hash": entry["input_hash"], "project_ids": ["p1"],
                        "output_dir": str(tmp_path / "outputs")})["result"]
    assert converted["status"] == "success", converted
    assert read_output(converted["output_path"]).xpath("//project/@name") == ["项目 2"]
    cancel = tmp_path / "cancel"
    cancel.touch()
    assert handle({**request, "cancel_path": str(cancel)})["cancelled"]
    stale = handle({**request, "action": "convert", "path": entry["path"],
                    "input_hash": "outdated", "output_dir": str(tmp_path / "stale")})["result"]
    assert stale["status"] == "blocked" and not (tmp_path / "stale").exists()


def test_preserve_decision_keeps_known_unsupported_feature_without_claiming_removal(tmp_path):
    tree = make_fcpxml("1.11")
    etree.SubElement(tree.find(".//asset-clip"), "adjust-colorConform", enabled="1")
    source = write_tree(tmp_path / "color.fcpxml", tree, validate=False)
    plan = plan_conversion(inspect(source), Target("fcpxml", "1.8"), allow_risky=True)
    issue = next(i for i in plan.issues if i.code == "TARGET_ELEMENT")
    result = convert(plan, {issue.id: "preserve"}, tmp_path / "out", allow_risky=True)
    assert result.status == "exported_with_issues", result.message
    assert read_output(result.output_path).find(".//adjust-colorConform") is not None
    assert "保留并记录" in Path(result.report_html).read_text()


def test_risky_flag_does_not_enable_unknown_cross_format_mapping(tmp_path):
    tree = make_fcpxml("1.9")
    etree.SubElement(tree.find(".//asset-clip"), "timeMap")
    source = write_tree(tmp_path / "speed.fcpxml", tree, validate=False)
    result = convert(plan_conversion(inspect(source), Target("xmeml", "5"), allow_risky=True), {},
                     tmp_path / "out", allow_risky=True)
    assert result.status == "blocked" and not result.output_path


def test_source_change_and_cancel_remain_effective_in_risky_mode(tmp_path):
    import threading
    source = write_tree(tmp_path / "change.fcpxml", make_fcpxml("1.9"))
    plan = plan_conversion(inspect(source), Target("fcpxml", "1.8"), allow_risky=True)
    source.write_text(source.read_text() + "\n")
    result = convert(plan, {}, tmp_path / "out", allow_risky=True)
    assert result.status == "blocked" and not result.output_path
    assert any(i.code == "SOURCE_CHANGED" for i in result.issues)
    event = threading.Event(); event.set()
    result = convert(plan, {}, tmp_path / "cancelled", event, allow_risky=True)
    assert result.status == "cancelled" and not (tmp_path / "cancelled").exists()
