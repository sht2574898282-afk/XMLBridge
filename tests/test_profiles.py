# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
from lxml import etree

from xmlbridge.core import convert, inspect, plan_conversion
from xmlbridge.models import Target
from helpers import make_fcpxml, read_output, write_tree


def test_premiere_does_not_accept_fcpxml_target(tmp_path):
    source = write_tree(tmp_path / "source.fcpxml", make_fcpxml("1.9"))
    plan = plan_conversion(inspect(source), Target("fcpxml", "1.9", "Adobe Premiere Pro"))
    assert any(i.code == "APPLICATION_FORMAT" for i in plan.blockers())
    assert convert(plan, {}, tmp_path / "out").status == "blocked"


def test_modern_final_cut_requires_fcpxml(tmp_path):
    source = write_tree(tmp_path / "source.fcpxml", make_fcpxml("1.9"))
    plan = plan_conversion(inspect(source), Target("xmeml", "5", "Final Cut Pro"))
    assert any(i.code == "APPLICATION_FORMAT" for i in plan.blockers())


def test_resolve_profile_wraps_event_without_changing_project(tmp_path):
    tree = make_fcpxml("1.9")
    root = tree.getroot()
    library = root.find("library")
    events = list(library.findall("event"))
    for event in events:
        root.append(event)
    root.remove(library)
    project = etree.tostring(root.find("event/project"), method="c14n")
    source = write_tree(tmp_path / "event.fcpxml", tree)
    plan = plan_conversion(inspect(source), Target("fcpxml", "1.9", "DaVinci Resolve"))
    assert not plan.blockers(), plan.issues
    result = convert(plan, {}, tmp_path / "out")
    assert result.status == "success", result.message
    output = read_output(result.output_path)
    normalized = etree.fromstring(
        etree.tostring(output.find("library/event/project")),
        etree.XMLParser(remove_blank_text=True),
    )
    assert etree.tostring(normalized, method="c14n") == project
