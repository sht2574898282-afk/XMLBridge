# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from xmlbridge import core
from xmlbridge.gui import MainWindow, collect_candidates
from xmlbridge.models import ConversionResult
from helpers import make_fcpxml, read_output, write_tree


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


def wait_for(app, condition, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("GUI worker did not reach the expected state")


@pytest.fixture
def window(qt_app):
    widget = MainWindow()
    yield widget
    if widget._busy:
        widget._request_cancel()
        wait_for(qt_app, lambda: not widget._busy)
    widget.close()
    qt_app.processEvents()


def test_candidate_scan_deduplicates_bundle_and_ignores_non_xml(tmp_path):
    bundle = tmp_path / "exchange.fcpxmld"
    bundle.mkdir()
    info = bundle / "Info.fcpxml"
    info.write_text("<fcpxml/>")
    (bundle / "other.xml").write_text("<extra/>")
    loose = tmp_path / "timeline.xml"
    loose.write_text("<xmeml/>")
    (tmp_path / "movie.mov").write_bytes(b"media")
    candidates = collect_candidates([tmp_path, bundle, info])
    assert set(candidates) == {bundle, loose}
    assert len(candidates) == 2


def test_batch_reports_bad_file_and_exports_only_checked_project(window, qt_app, tmp_path, monkeypatch):
    valid = write_tree(tmp_path / "valid.fcpxml", make_fcpxml("1.8", projects=2))
    bad = tmp_path / "unrelated.xml"
    bad.write_text("<unrelated/>")
    main_thread = threading.get_ident()
    calls = []
    for name in ("inspect", "plan_conversion", "convert"):
        original = getattr(core, name)

        def observed(*args, _original=original, _name=name, **kwargs):
            calls.append((_name, threading.get_ident()))
            return _original(*args, **kwargs)

        monkeypatch.setattr(core, name, observed)

    window.add_paths([valid, bad])
    wait_for(qt_app, lambda: not window._busy)
    window.analyze_queue()
    assert window._busy and not window.analyze_button.isEnabled()
    wait_for(qt_app, lambda: not window._busy)
    assert len(window.entries) == 2
    window.file_list.setCurrentRow(0)
    window.project_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    entry = window.entries[str(valid.resolve())]
    wait_for(qt_app, lambda: entry.plan is not None and not window._busy)
    assert entry.selected_ids == ["p1"]
    window.output_edit.setText(str(tmp_path / "outputs"))
    assert window.export_button.isEnabled()
    assert "仅报告" in window.export_button.text()
    window.export_queue()
    wait_for(qt_app, lambda: not window._busy)
    result = entry.result
    assert result.status == "success"
    assert read_output(result.output_path).xpath("//project/@name") == ["项目 2"]
    diagnostic = window.entries[str(bad.resolve())].result
    assert diagnostic.status == "blocked"
    assert not diagnostic.output_path
    assert Path(diagnostic.report_html).exists()
    assert window.check_labels["host"].text() == "未验证"
    assert window.report_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert window.analyze_button.isEnabled()
    assert {name for name, _ in calls} == {"inspect", "plan_conversion", "convert"}
    assert all(thread != main_thread for _, thread in calls)


def test_empty_project_selection_cannot_export(window, qt_app, tmp_path):
    source = write_tree(tmp_path / "valid.fcpxml", make_fcpxml("1.8", projects=2))
    window.add_paths([source])
    wait_for(qt_app, lambda: not window._busy)
    window.analyze_queue()
    wait_for(qt_app, lambda: not window._busy)
    window.output_edit.setText(str(tmp_path / "outputs"))
    assert window.export_button.isEnabled()
    window._set_project_checks(False)
    assert window._current_entry().selected_ids == []
    assert not window.export_button.isEnabled()
    window.export_queue()
    assert not window._busy
    assert not (tmp_path / "outputs").exists()


def test_cancel_conversion_marks_remainder_unprocessed(window, qt_app, tmp_path, monkeypatch):
    paths = [write_tree(tmp_path / f"source-{i}.fcpxml", make_fcpxml("1.8")) for i in range(2)]
    window.add_paths(paths)
    wait_for(qt_app, lambda: not window._busy)
    window.analyze_queue()
    wait_for(qt_app, lambda: not window._busy)
    entered = threading.Event()

    def slow_conversion(plan, decisions, output_dir, cancel_event=None):
        entered.set()
        assert cancel_event.wait(5), "test cancellation did not arrive"
        return ConversionResult("cancelled", message="测试取消", checks={"structure": "not_run", "timeline": "not_run", "host": "not_run"})

    monkeypatch.setattr(core, "convert", slow_conversion)
    window.output_edit.setText(str(tmp_path / "outputs"))
    window.export_queue()
    wait_for(qt_app, entered.is_set)
    window._request_cancel()
    wait_for(qt_app, lambda: not window._busy)
    entries = list(window.entries.values())
    assert entries[0].result.status == "cancelled"
    assert entries[1].result is None
    assert entries[1].status == "未处理 · 已取消"
    assert all(label.text() != "通过" for label in window.check_labels.values())
    assert window.analyze_button.isEnabled()
    assert not window.cancel_button.isEnabled()
