# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidget, QPushButton, QStyleOptionViewItem

from helpers import make_fcpxml, read_output, write_tree
from test_gui import wait_for
from xmlbridge.windows_gui import WindowsWindow


@pytest.fixture
def win():
    app = QApplication.instance() or QApplication([])
    widget = WindowsWindow()
    yield widget, app
    if widget._busy:
        widget.cancel()
        wait_for(app,lambda:not widget._busy)
    for dialog in list(widget._dialogs):
        dialog.close()
    widget.close()
    app.processEvents()


def test_windows_starts_on_welcome_with_risky_export_enabled(win):
    w,app = win
    assert w.pages.currentIndex()==0
    assert w.risky.isChecked()
    assert not w.export_button.isEnabled()
    assert not w._busy


def test_version_popup_uses_menu_typography_instead_of_large_selected_value(win,tmp_path):
    w,app = win
    source = write_tree(tmp_path/"source.fcpxml",make_fcpxml("1.9"))
    w.add_paths([source])
    wait_for(app,lambda:not w._busy)
    w.show()
    app.processEvents()
    w.version_combo.showPopup()
    app.processEvents()
    view = w.version_combo.view()
    option = QStyleOptionViewItem()
    view.itemDelegate().initStyleOption(option,view.model().index(0,0))
    assert option.font.pixelSize()==14
    assert w.version_combo.font().pixelSize()>option.font.pixelSize()
    assert 30<=view.sizeHintForRow(0)<=38
    assert view.window().height()<330
    assert view.window().width()<=w.version_combo.width()+24
    w.version_combo.hidePopup()


def test_package_picker_adds_bundle_and_scans_automatically(win,tmp_path,monkeypatch):
    w,app = win
    bundle = tmp_path/"中文 项目.fcpxmld"
    bundle.mkdir()
    write_tree(bundle/"Info.fcpxml",make_fcpxml("1.9"))
    monkeypatch.setattr("xmlbridge.windows_gui.QFileDialog.getExistingDirectory",lambda *args:str(bundle))
    w.choose_folder()
    wait_for(app,lambda:not w._busy)
    assert len(w.entries)==1 and w.pages.currentIndex()==1
    assert w.current().document.version=="1.9"
    assert w.export_button.isEnabled()


def test_windows_batch_partial_failure_and_checks(win,tmp_path):
    w,app = win
    valid = write_tree(tmp_path/"正常.fcpxml",make_fcpxml("1.8"))
    broken = tmp_path/"损坏.xml"
    broken.write_text("<fcpxml")
    source=valid.read_bytes()
    w.add_paths([valid,broken])
    wait_for(app,lambda:not w._busy)
    assert len(w.entries)==2 and len(w.included)==2
    w.output_folder=tmp_path/"交接输出"
    w.export()
    wait_for(app,lambda:not w._busy)
    good=w.entries[str(valid.resolve())].result
    bad=w.entries[str(broken.resolve())].result
    assert good.output_path and good.checks["structure"]=="passed"
    assert good.checks["host"]=="not_run"
    assert bad.status=="blocked" and not bad.output_path
    assert Path(bad.report_json).exists()
    assert valid.read_bytes()==source


def test_only_checked_file_exports(win,tmp_path):
    w,app=win
    a=write_tree(tmp_path/"a.fcpxml",make_fcpxml("1.8"))
    b=write_tree(tmp_path/"b.fcpxml",make_fcpxml("1.8"))
    w.add_paths([a,b])
    wait_for(app,lambda:not w._busy)
    w.file_list.item(1).setCheckState(Qt.CheckState.Unchecked)
    w.output_folder=tmp_path/"out"
    w.export()
    wait_for(app,lambda:not w._busy)
    assert w.entries[str(a.resolve())].result.output_path
    assert w.entries[str(b.resolve())].result is None


def test_risky_downgrade_is_not_blocked_and_strict_mode_is_honest(win,tmp_path):
    w,app=win
    from lxml import etree
    tree=make_fcpxml("1.9")
    etree.SubElement(tree.xpath("//asset-clip")[0],"third-party-opaque")
    source=tmp_path/"v19.fcpxml"
    tree.write(str(source),encoding="utf-8",xml_declaration=True)
    w.add_paths([source])
    wait_for(app,lambda:not w._busy)
    w.version_combo.setCurrentText("1.8")
    wait_for(app,lambda:not w._busy)
    w.output_folder=tmp_path/"risk"
    w.export()
    wait_for(app,lambda:not w._busy)
    assert w.current().result.output_path
    assert w.current().result.status=="exported_with_issues"
    assert w.current().result.checks["structure"]=="failed"
    assert read_output(w.current().result.output_path).getroot().get("version")=="1.8"
    for dialog in list(w._dialogs):dialog.close()
    app.processEvents()
    w.risky.setChecked(False)
    wait_for(app,lambda:not w._busy)
    assert not w.risky.isChecked()
    assert "严格" in w.risky_note.text()
    w.export()
    wait_for(app,lambda:not w._busy)
    assert w.current().result.status=="blocked" and not w.current().result.output_path


def test_source_change_after_scan_stops_export(win,tmp_path):
    w,app=win
    source=write_tree(tmp_path/"source.fcpxml",make_fcpxml("1.8"))
    w.add_paths([source])
    wait_for(app,lambda:not w._busy)
    source.write_bytes(source.read_bytes()+b"\n")
    w.output_folder=tmp_path/"out"
    w.export()
    wait_for(app,lambda:not w._busy)
    assert w.current().result.status=="blocked"
    assert any(i.code=="SOURCE_CHANGED"for i in w.current().result.issues)


def test_target_profile_switches_family_and_clear_returns_to_first_use(win,tmp_path):
    w,app=win
    source=write_tree(tmp_path/"source.fcpxml",make_fcpxml("1.8"))
    w.add_paths([source])
    wait_for(app,lambda:not w._busy)
    w.application_combo.setCurrentText("Adobe Premiere Pro")
    wait_for(app,lambda:not w._busy)
    assert w.target().family=="xmeml" and w.target().version=="5"
    w.clear()
    assert w.pages.currentIndex()==0 and not w.entries


def test_cancelled_replan_cannot_export_an_old_target(win,tmp_path,monkeypatch):
    import threading
    w,app=win
    a=write_tree(tmp_path/"a.fcpxml",make_fcpxml("1.9"))
    b=write_tree(tmp_path/"b.fcpxml",make_fcpxml("1.9"))
    w.add_paths([a,b])
    wait_for(app,lambda:not w._busy)
    started=threading.Event();release=threading.Event()
    original=w._plan
    def held(entry,target,risky):
        started.set();release.wait(5)
        return original(entry,target,risky)
    monkeypatch.setattr(w,"_plan",held)
    w.version_combo.setCurrentText("1.8")
    assert started.wait(5)
    w.cancel();release.set()
    wait_for(app,lambda:not w._busy)
    assert all(e.plan is None or e.plan.target.version=="1.8"for e in w.entries.values())


def test_unsupported_version_can_be_reported_without_breaking_the_window(win,tmp_path):
    w,app=win
    source=tmp_path/"unknown.fcpxml"
    source.write_text('<fcpxml version="unknown"><resources/></fcpxml>')
    w.add_paths([source])
    wait_for(app,lambda:not w._busy)
    assert w.current().document.version=="unknown"
    assert any(i.code=="UNSUPPORTED_VERSION"for i in w.current().plan.issues)
    assert w.pages.currentIndex()==1


def test_projects_dialog_controls_the_actual_export(win,tmp_path):
    w,app=win
    source=write_tree(tmp_path/"multi.fcpxml",make_fcpxml("1.8",projects=2))
    w.add_paths([source])
    wait_for(app,lambda:not w._busy)
    w.show_projects()
    dialog=w._dialogs[-1]
    dialog.findChild(QListWidget).item(0).setCheckState(Qt.CheckState.Unchecked)
    next(b for b in dialog.findChildren(QPushButton)if b.text()=="完成").click()
    wait_for(app,lambda:not w._busy)
    assert w.current().selected_ids==["p1"]
    w.output_folder=tmp_path/"out"
    w.export()
    wait_for(app,lambda:not w._busy)
    assert read_output(w.current().result.output_path).xpath("//project/@name")==["项目 2"]
