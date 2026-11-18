# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
"""Windows workbench following XMLBridge's native macOS interaction layout."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QFont, QFontMetrics, QIcon, QPixmap, QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QPushButton, QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
    QStyledItemDelegate,
)

from . import core
from .gui import SourceEntry, Worker, collect_candidates, _display_duration, _display_fps
from .models import FCP_VERSIONS, XMEML_VERSIONS, Target


STYLE = """
QWidget { font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif; font-size: 14px; color: #24252b; }
QMainWindow, QDialog { background: #f8f9fb; }
QLabel { background: transparent; }
QLabel#muted { color: #687080; font-size: 13px; }
QLabel#title { font-size: 25px; font-weight: 600; }
QLabel#filename { font-size: 18px; font-weight: 600; }
QLabel#version { font-size: 38px; font-weight: 600; color: #5e62be; }
QLabel#arrow { font-size: 26px; color: #a7a9b2; }
QFrame#toolbar { background: #ffffff; border-bottom: 1px solid #e7e8ed; }
QFrame#sidebar { background: #f0f2f6; border-right: 1px solid #e2e5ec; }
QFrame#impact { background: #edf0f6; border-radius: 13px; }
QFrame#resultCard, QFrame#issueCard { background: #ffffff; border: 1px solid #e5e7ee; border-radius: 12px; }
QPushButton, QToolButton { background: #ffffff; border: 1px solid #e1e3eb; border-radius: 8px; padding: 9px 15px; }
QPushButton:hover, QToolButton:hover { background: #f0f1fa; border-color: #c8cbe4; }
QPushButton:disabled, QToolButton:disabled { color: #a5a8b2; background: #f0f1f5; border-color: #e6e7ee; }
QPushButton#primary { background: #666ad5; border-color: #666ad5; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #565ac4; }
QPushButton#primary:disabled { background: #c4c6e8; border-color: #c4c6e8; }
QPushButton#quiet { border: none; background: transparent; color: #686b83; }
QComboBox { border: 1px solid #e0e3ed; border-radius: 9px; background: #ffffff; padding: 9px 13px; }
QComboBox:disabled { color: #9c9fa9; }
QComboBox::drop-down { border: none; width: 25px; }
QComboBox#targetVersion { font-size: 34px; font-weight: 600; padding: 10px 18px; min-width: 130px; }
QComboBox QAbstractItemView { font-size: 14px; font-weight: 400; background: #ffffff; border: 1px solid #dfe3ed; selection-background-color: #e8e9fc; selection-color: #353967; color: #30313b; outline: none; padding: 4px; }
QComboBox QAbstractItemView::item { min-height: 32px; padding: 0 10px; }
QMenu { font-size: 14px; background: #ffffff; border: 1px solid #dfe3ed; padding: 5px; }
QMenu::item { padding: 8px 18px; border-radius: 5px; }
QMenu::item:selected { background: #e8e9fc; color: #353967; }
QMenu::separator { height: 1px; background: #e7e8ed; margin: 4px 8px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 12px 10px; border-radius: 8px; margin: 3px 6px; }
QListWidget::item:selected { background: #e0e3f2; color: #33354e; }
QListWidget::item:hover { background: #e9ebf4; }
QCheckBox { spacing: 8px; background: transparent; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 9px; }
QScrollBar::handle:vertical { background: #cbd0dc; border-radius: 4px; min-height: 26px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""


class ComboItemDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.font.setPixelSize(14)
        option.font.setWeight(QFont.Weight.Normal)
        option.fontMetrics = QFontMetrics(option.font)


def combo_box(name=""):
    combo = QComboBox(objectName=name)
    view = QListView(combo)
    view.setUniformItemSizes(True)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    combo.setView(view)
    combo.setItemDelegate(ComboItemDelegate(view))
    combo.setMaxVisibleItems(8)
    return combo


def label(text="", name="", wrap=False):
    w = QLabel(text)
    w.setWordWrap(wrap)
    if name:
        w.setObjectName(name)
    return w


def button(text, action, name=""):
    w = QPushButton(text)
    if name:
        w.setObjectName(name)
    w.clicked.connect(action)
    return w


def section(name="", margins=(20, 18, 20, 18)):
    frame = QFrame()
    if name:
        frame.setObjectName(name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(12)
    return frame, layout


class WindowsWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XMLBridge · 影视 XML 交接")
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "assets/XMLBridge.png")))
        self.resize(1040, 730)
        self.setMinimumSize(820, 640)
        self.setAcceptDrops(True)
        self.entries: dict[str, SourceEntry] = {}
        self.included: set[str] = set()
        self.output_folder: Path | None = None
        self._busy = False
        self._rendering = False
        self._close_after = False
        self._worker = None
        self._cancel = threading.Event()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._after_task = None
        self._dialogs = []
        self._build()
        self.setStyleSheet(STYLE)
        self._refresh()

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        toolbar = QFrame(objectName="toolbar")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(22, 12, 22, 12)
        logo = label()
        logo.setPixmap(QPixmap(str(Path(__file__).parent / "assets/XMLBridge.png")).scaled(27, 27, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        bar.addWidget(logo)
        bar.addWidget(label("XMLBridge"))
        bar.addStretch()
        self.add_button = QToolButton()
        self.add_button.setText("＋  添加文件")
        menu = QMenu(self)
        menu.addAction("添加 XML 文件…", self.choose_files)
        menu.addAction("添加目录包或文件夹…", self.choose_folder)
        menu.addSeparator()
        menu.addAction("重新分析当前设置", self._target_changed)
        self.add_button.setMenu(menu)
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.add_button.clicked.connect(self.choose_files)
        bar.addWidget(self.add_button)
        self.clear_button = button("清空", self.clear, "quiet")
        bar.addWidget(self.clear_button)
        outer.addWidget(toolbar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar, side = section("sidebar", (10, 22, 10, 12))
        self.sidebar.setFixedWidth(225)
        side.addWidget(label("交接文件"))
        self.file_list = QListWidget()
        self.file_list.setObjectName("fileQueue")
        self.file_list.currentRowChanged.connect(lambda _: self._refresh())
        self.file_list.itemChanged.connect(self._include_changed)
        side.addWidget(self.file_list, 1)
        self.queue_note = label("", "muted")
        side.addWidget(self.queue_note)
        body.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)
        self._build_welcome()
        self._build_conversion()

        footer = QHBoxLayout()
        footer.setContentsMargins(25, 10, 25, 15)
        self.activity = label("本机处理，原文件保留", "muted")
        self.activity.setWordWrap(True)
        footer.addWidget(self.activity, 1)
        self.cancel_button = button("取消", self.cancel, "quiet")
        footer.addWidget(self.cancel_button)
        self.version_label = label("0.2.0 · Windows 测试版 2", "muted")
        footer.addWidget(self.version_label)
        outer.addLayout(footer)

    def _build_welcome(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.setSpacing(18)
        logo = label()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(QPixmap(str(Path(__file__).parent / "assets/XMLBridge.png")).scaled(94, 94, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        box.addWidget(logo)
        text = label("从一个 XML 开始", "title")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(text)
        text = label("添加剪辑文件，选择需要交接的版本。", "muted")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(text)
        self.welcome_add = button("＋  添加文件", self.choose_files, "primary")
        box.addWidget(self.welcome_add, 0, Qt.AlignmentFlag.AlignCenter)
        self.welcome_folder = button("添加目录包或文件夹…", self.choose_folder, "quiet")
        box.addWidget(self.welcome_folder, 0, Qt.AlignmentFlag.AlignCenter)
        text = label("或把 XML、FCPXML、.fcpxmld 拖到这个窗口", "muted")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(text)
        self.pages.addWidget(page)

    def _build_conversion(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(40, 28, 40, 24)
        box.setSpacing(22)
        head = QHBoxLayout()
        head.addWidget(label("当前交接", "muted"))
        head.addStretch()
        self.projects_button = button("选择项目", self.show_projects, "quiet")
        head.addWidget(self.projects_button)
        box.addLayout(head)

        hero = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(10)
        badge = QFrame()
        badge.setFixedSize(110,125)
        badge.setStyleSheet("QFrame { background:#ffffff; border:1px solid #e0e3ef; border-radius:16px; }")
        badge_box = QVBoxLayout(badge)
        code = label("〈 / 〉")
        code.setStyleSheet("border:none; color:#7679cf; font-size:24px;")
        code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge_box.addWidget(code)
        self.source_badge = label("1.9")
        self.source_badge.setStyleSheet("border:none; font-size:22px; font-weight:600; color:#666ac0;")
        self.source_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge_box.addWidget(self.source_badge)
        left.addWidget(badge,0,Qt.AlignmentFlag.AlignCenter)
        self.source_name = label("", "filename", True)
        self.source_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.source_name)
        self.source_type = label("", "muted")
        self.source_type.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.source_type)
        hero.addLayout(left, 1)
        arrow = label("→", "arrow")
        arrow.setFixedWidth(55)
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero.addWidget(arrow)
        right = QVBoxLayout()
        right.setSpacing(12)
        right.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.family_combo = combo_box()
        self.family_combo.addItem("目标 FCPXML", "fcpxml")
        self.family_combo.addItem("目标 Final Cut 7 XML", "xmeml")
        self.family_combo.currentIndexChanged.connect(self._family_changed)
        right.addWidget(self.family_combo)
        self.version_combo = combo_box("targetVersion")
        self.version_combo.addItems(FCP_VERSIONS)
        self.version_combo.setCurrentText("1.9")
        self.version_combo.currentIndexChanged.connect(self._target_changed)
        right.addWidget(self.version_combo)
        self.application_combo = combo_box()
        self.application_combo.addItems(["通用", "DaVinci Resolve", "Final Cut Pro", "Adobe Premiere Pro"])
        self.application_combo.currentIndexChanged.connect(self._application_changed)
        right.addWidget(self.application_combo)
        hero.addLayout(right, 1)
        box.addLayout(hero)
        self.metrics = label("", "muted", True)
        self.metrics.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.metrics)

        impact, layout = section("impact", (18, 15, 18, 15))
        row = QHBoxLayout()
        col = QVBoxLayout()
        self.impact_title = label()
        self.impact_detail = label("", "muted", True)
        col.addWidget(self.impact_title)
        col.addWidget(self.impact_detail)
        row.addLayout(col, 1)
        self.impact_button = button("查看影响  ›", self.show_impacts, "quiet")
        row.addWidget(self.impact_button)
        layout.addLayout(row)
        box.addWidget(impact)
        self.risky = QCheckBox("允许有损转换")
        self.risky.setChecked(True)
        self.risky.toggled.connect(self._target_changed)
        box.addWidget(self.risky)
        self.risky_note = label("同格式可带问题导出，检查结果如实记录。", "muted", True)
        box.addWidget(self.risky_note)
        box.addStretch(1)
        bottom = QHBoxLayout()
        self.output_button = button("与源文件放在一起  ▾", self.choose_output, "quiet")
        bottom.addWidget(self.output_button)
        bottom.addStretch()
        self.results_button = button("查看结果", self.show_results, "quiet")
        bottom.addWidget(self.results_button)
        self.export_button = button("导出", self.export, "primary")
        bottom.addWidget(self.export_button)
        box.addLayout(bottom)
        self.pages.addWidget(page)

    def current(self):
        item = self.file_list.currentItem()
        return self.entries.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def target(self):
        return Target(self.family_combo.currentData(), self.version_combo.currentText(), self.application_combo.currentText())

    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "添加剪辑 XML", "", "剪辑 XML (*.xml *.fcpxml);;所有文件 (*)")
        self.add_paths(files)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择 .fcpxmld 目录包或文件夹")
        if folder:
            self.add_paths([folder])

    def choose_output(self):
        menu = QMenu(self)
        choose = menu.addAction("选择文件夹…")
        source = menu.addAction("与源文件放在一起")
        picked = menu.exec(self.output_button.mapToGlobal(self.output_button.rect().bottomLeft()))
        if picked == source:
            self.output_folder = None
        elif picked == choose:
            folder = QFileDialog.getExistingDirectory(self, "选择导出位置", str(self.output_folder or Path.home()))
            if folder:
                self.output_folder = Path(folder)
        self.output_button.setText((self.output_folder.name if self.output_folder else "与源文件放在一起") + "  ▾")
        self.output_button.setToolTip(str(self.output_folder or ""))

    def _plan(self, entry, target, risky):
        return core.plan_conversion(entry.document, target, entry.selected_ids, allow_risky=risky)

    def add_paths(self, paths):
        if self._busy or not paths:
            return
        target, risky = self.target(), self.risky.isChecked()
        existing = set(self.entries)
        def task(cancel, emit):
            paths_found = collect_candidates(paths, cancel)
            count = 0
            for path in paths_found:
                if cancel.is_set():
                    break
                key = str(path.resolve())
                if key in existing:
                    continue
                doc = core.inspect(path)
                entry = SourceEntry(path, document=doc, selected_ids=[p.id for p in doc.projects] if doc.projects else None)
                entry.plan = self._plan(entry, target, risky)
                entry.status = "已分析" if doc.tree is not None else "无法读取"
                emit(("entry", key, entry))
                count += 1
            return f"已添加并分析 {count} 个文件。" if paths_found else "没有找到 XML 文件或 .fcpxmld 目录包。"
        self._launch("正在读取剪辑文件…", task)

    def _launch(self, message, task, after=None):
        if self._busy:
            return
        self._busy = True
        self._cancel = threading.Event()
        self._after_task = after
        self.activity.setText(message)
        self._worker = Worker(task, self._cancel)
        self._worker.signals.item.connect(self._receive)
        self._worker.signals.finished.connect(self._finished)
        self._refresh()
        self._pool.start(self._worker)

    def _receive(self, event):
        kind, key, value = event
        if kind == "entry":
            if key not in self.entries:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                self.file_list.blockSignals(True)
                self.file_list.addItem(item)
                self.file_list.blockSignals(False)
                self.included.add(key)
            self.entries[key] = value
        elif kind == "result":
            self.entries[key].result = value
            self.entries[key].status = {"success":"已生成", "exported_with_issues":"已生成 · 有问题", "cancelled":"已取消", "blocked":"未生成 · 查看报告"}.get(value.status, "导出失败")
        elif kind == "status":
            self.entries[key].status = value
            self.activity.setText(value + "：" + self.entries[key].path.name)
        if self.file_list.currentRow() < 0:
            self.file_list.setCurrentRow(0)
        self._refresh()

    def _finished(self, cancelled, message):
        self._busy = False
        self._worker = None
        self.activity.setText(("已取消。" if cancelled else "") + message)
        callback, self._after_task = self._after_task, None
        self._refresh()
        if self._close_after:
            self.close()
        elif callback:
            callback()

    def cancel(self):
        self._cancel.set()
        self.activity.setText("正在取消，已生成的文件会保留…")
        self.cancel_button.setEnabled(False)

    def clear(self):
        if self._busy:
            return
        self.entries.clear()
        self.included.clear()
        self.file_list.clear()
        self.activity.setText("本机处理，原文件保留")
        self._refresh()

    def _include_changed(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self.included.add(key)
        else:
            self.included.discard(key)
        self._refresh()

    def _family_changed(self):
        if self._rendering:
            return
        self.version_combo.blockSignals(True)
        family = self.family_combo.currentData()
        self.version_combo.clear()
        self.version_combo.addItems(FCP_VERSIONS if family == "fcpxml" else XMEML_VERSIONS)
        self.version_combo.setCurrentText("1.9" if family == "fcpxml" else "5")
        self.version_combo.blockSignals(False)
        self._target_changed()

    def _application_changed(self):
        application = self.application_combo.currentText()
        family = "xmeml" if "Premiere" in application else "fcpxml" if application == "Final Cut Pro" else None
        if family and self.family_combo.currentData() != family:
            self.family_combo.setCurrentIndex(self.family_combo.findData(family))
        else:
            self._target_changed()

    def _target_changed(self):
        if self._rendering or self._busy:
            return
        target, risky = self.target(), self.risky.isChecked()
        inputs = [(k, SourceEntry(e.path, document=e.document, selected_ids=e.selected_ids, status="已分析")) for k,e in self.entries.items()]
        if not inputs:
            self._refresh()
            return
        for entry in self.entries.values():
            entry.plan = None
            entry.result = None
            entry.decisions.clear()
            entry.status = "等待更新设置"
        def task(cancel, emit):
            for key, entry in inputs:
                if cancel.is_set():
                    break
                entry.plan = self._plan(entry, target, risky)
                emit(("entry", key, entry))
            return "交接设置已更新。"
        self._launch("正在检查交接影响…", task)

    def _refresh(self):
        if self._rendering:
            return
        self._rendering = True
        try:
            self.pages.setCurrentIndex(1 if self.entries else 0)
            self.sidebar.setVisible(len(self.entries) > 1)
            self.file_list.blockSignals(True)
            for i in range(self.file_list.count()):
                item = self.file_list.item(i)
                entry = self.entries.get(item.data(Qt.ItemDataRole.UserRole))
                if entry:
                    item.setText(entry.path.name + "\n" + entry.status)
                    item.setToolTip(str(entry.path))
            self.file_list.blockSignals(False)
            self.queue_note.setText(f"{len(self.included)} 个文件参与导出")
            for control in [self.add_button, self.clear_button, self.welcome_add, self.welcome_folder,
                            self.family_combo, self.version_combo, self.application_combo, self.risky,
                            self.projects_button, self.impact_button, self.output_button, self.file_list]:
                control.setEnabled(not self._busy)
            self.cancel_button.setVisible(self._busy)
            self.cancel_button.setEnabled(not self._cancel.is_set())
            self.clear_button.setVisible(bool(self.entries))
            self.results_button.setVisible(any(e.result for e in self.entries.values()))
            self.results_button.setEnabled(not self._busy)
            self.export_button.setText(f"导出 {len(self.included)} 个文件" if len(self.entries)>1 else "转换并导出")
            self.export_button.setEnabled(not self._busy and any(k in self.included and e.plan and (not e.document.projects or e.selected_ids) for k,e in self.entries.items()))
            self.risky_note.setText("同格式可带问题导出，检查结果如实记录。" if self.risky.isChecked() else "严格检查，未解决的问题将只生成诊断报告。")
            entry = self.current()
            if not entry:
                return
            doc = entry.document
            if len(self.entries)==1 and doc.family==self.target().family and doc.version in FCP_VERSIONS+XMEML_VERSIONS:
                source_version=tuple(int(p)for p in doc.version.split("."))
                target_version=tuple(int(p)for p in self.target().version.split("."))
                action="降级" if target_version<source_version else "升级" if target_version>source_version else "转换"
                self.export_button.setText(action+"并导出")
            self.source_badge.setText(doc.version or "?")
            self.source_name.setText(entry.path.stem)
            self.source_name.setToolTip(str(entry.path))
            self.source_type.setText(("FCPXML" if doc.family=="fcpxml" else "Final Cut 7 XML" if doc.family=="xmeml" else "无法识别") + " · " + doc.version)
            selected = [p for p in doc.projects if p.id in (entry.selected_ids or [])]
            self.projects_button.setText(f"{len(selected)} / {len(doc.projects)} 个项目  ▾" if doc.projects else "素材与事件")
            self.projects_button.setEnabled(not self._busy and bool(doc.projects))
            fps = " / ".join(dict.fromkeys(_display_fps(p.fps)for p in selected)) or "帧率未知"
            duration = " / ".join(_display_duration(p.duration)for p in selected) or "时长未知"
            self.metrics.setText(f"{fps}  ·  {duration}  ·  {sum(p.clip_count for p in selected)} 个片段  ·  {len(doc.media)} 个素材")
            issues = entry.plan.issues if entry.plan else doc.issues
            blockers = core.export_blockers(issues, entry.decisions, allow_risky=self.risky.isChecked(), same_family=doc.family==self.target().family)
            self.impact_title.setText(f"{len(issues)} 项内容需要留意" if issues else "未发现需要处理的兼容问题")
            self.impact_detail.setText(f"{len(blockers)} 项仍需处理；可查看具体原因或生成诊断报告。" if blockers else "问题已记录，当前设置仍可生成文件。" if issues else "仍需在目标软件核对实际导入结果。")
        finally:
            self._rendering = False

    def _show_dialog(self, dialog):
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._dialogs.append(dialog)
        dialog.destroyed.connect(lambda: self._dialogs.remove(dialog) if dialog in self._dialogs else None)
        dialog.open()

    def show_projects(self):
        entry = self.current()
        if not entry or self._busy:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("选择交接项目")
        dialog.resize(540, 430)
        box = QVBoxLayout(dialog)
        box.setContentsMargins(26, 24, 26, 24)
        box.addWidget(label("选择交接项目", "filename"))
        box.addWidget(label("全部项目均已扫描；至少选择一个项目。", "muted"))
        listing = QListWidget()
        for project in entry.document.projects:
            item = QListWidgetItem(project.name + "\n" + _display_fps(project.fps) + " · " + _display_duration(project.duration))
            item.setData(Qt.ItemDataRole.UserRole, project.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if project.id in (entry.selected_ids or []) else Qt.CheckState.Unchecked)
            listing.addItem(item)
        box.addWidget(listing, 1)
        done = button("完成", lambda: None, "primary")
        def apply_selection():
            entry.selected_ids = [listing.item(i).data(Qt.ItemDataRole.UserRole)for i in range(listing.count())if listing.item(i).checkState()==Qt.CheckState.Checked]
            dialog.accept()
            self._target_changed()
        done.clicked.connect(apply_selection)
        box.addWidget(done, 0, Qt.AlignmentFlag.AlignRight)
        self._show_dialog(dialog)

    def show_impacts(self):
        entry = self.current()
        if not entry or not entry.plan or self._busy:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("交接影响")
        dialog.resize(690, 560)
        box = QVBoxLayout(dialog)
        box.setContentsMargins(25, 24, 25, 20)
        box.addWidget(label("交接影响", "filename"))
        box.addWidget(label("查看具体变化；你的选择会记录到交接报告。", "muted"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        cards = QVBoxLayout(content)
        for issue in entry.plan.issues:
            card, col = section("issueCard", (16, 14, 16, 14))
            title = label(issue.message, wrap=True)
            title.setStyleSheet("font-weight:600;")
            col.addWidget(title)
            where = " · ".join(p for p in [issue.project, issue.clip, issue.time, issue.code]if p)
            col.addWidget(label(where, "muted", True))
            if issue.details:
                col.addWidget(label(issue.details, "muted", True))
            if issue.choices:
                choice = combo_box()
                choice.addItem("按当前模式自动处理并记录" if self.risky.isChecked() else "请选择处理方式", "")
                for code, text in issue.choices.items():
                    choice.addItem(text, code)
                choice.setCurrentIndex(max(0, choice.findData(entry.decisions.get(issue.id, ""))))
                def selected(_, widget=choice, ident=issue.id):
                    if widget.currentData():
                        entry.decisions[ident] = widget.currentData()
                    else:
                        entry.decisions.pop(ident, None)
                    entry.result = None
                    self._refresh()
                choice.currentIndexChanged.connect(selected)
                col.addWidget(choice)
            else:
                col.addWidget(label("保留并记录；是否可导出以当前转换规则为准。", "muted", True))
            cards.addWidget(card)
        if not entry.plan.issues:
            cards.addWidget(label("未发现需要处理的兼容问题。"))
        cards.addStretch()
        scroll.setWidget(content)
        box.addWidget(scroll, 1)
        box.addWidget(button("完成", dialog.accept, "primary"), 0, Qt.AlignmentFlag.AlignRight)
        self._show_dialog(dialog)

    def export(self):
        if self._busy:
            return
        jobs = [(k,e,e.plan,dict(e.decisions)) for k,e in self.entries.items() if k in self.included and e.plan and (e.selected_ids or not e.document.projects)]
        if not jobs:
            return
        risky, output = self.risky.isChecked(), self.output_folder
        def task(cancel, emit):
            count = 0
            for index,(key,entry,plan,decisions) in enumerate(jobs):
                if cancel.is_set():
                    emit(("status", key, "未处理 · 已取消"))
                    continue
                emit(("status", key, f"正在导出 {index+1} / {len(jobs)}"))
                result = core.convert(plan, decisions, output or entry.path.parent, cancel, allow_risky=risky)
                emit(("result", key, result))
                count += bool(result.output_path)
            return f"处理完成，已生成 {count} / {len(jobs)} 个文件。"
        self._launch("正在生成交接文件…", task, self.show_results)

    def show_results(self):
        results = [e for e in self.entries.values()if e.result]
        if not results:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("交接结果")
        dialog.resize(680, 570)
        box = QVBoxLayout(dialog)
        box.setContentsMargins(26, 24, 26, 20)
        box.addWidget(label("交接结果", "filename"))
        count = sum(bool(e.result.output_path)for e in results)
        box.addWidget(label(f"已生成 {count} / {len(results)} 个文件；检查结果分别列出。", "muted"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        cards = QVBoxLayout(content)
        state_names = {"passed":"通过", "failed":"未通过", "not_run":"尚未验证"}
        for entry in results:
            result = entry.result
            card, col = section("resultCard")
            col.addWidget(label(entry.path.stem))
            col.addWidget(label(entry.status + " · " + result.message, "muted", True))
            for key,name in [("structure","结构检查"),("timeline","时间线核对"),("host","目标软件导入")]:
                row = QHBoxLayout()
                row.addWidget(label(name))
                row.addStretch()
                state = result.checks.get(key,"not_run")
                value = label(state_names.get(state,state))
                value.setStyleSheet("color:" + ("#23946b" if state=="passed" else "#c27b28" if state=="failed" else "#888c98") + ";")
                row.addWidget(value)
                col.addLayout(row)
            row = QHBoxLayout()
            if result.output_path:
                row.addWidget(button("打开结果文件夹", lambda _, p=result.output_path: self._open(Path(p).parent)))
            if result.report_html:
                row.addWidget(button("打开交接报告", lambda _, p=result.report_html: self._open(Path(p))))
            col.addLayout(row)
            cards.addWidget(card)
        cards.addStretch()
        scroll.setWidget(content)
        box.addWidget(scroll,1)
        box.addWidget(button("完成",dialog.accept,"primary"),0,Qt.AlignmentFlag.AlignRight)
        self._show_dialog(dialog)

    def _open(self,path):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.absolute()))):
            self.activity.setText("系统未能打开：" + str(path))

    def dragEnterEvent(self,event):
        if not self._busy and event.mimeData().hasUrls() and any(u.isLocalFile()for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self,event):
        if not self._busy:
            self.add_paths([u.toLocalFile()for u in event.mimeData().urls()if u.isLocalFile()])
            event.acceptProposedAction()

    def closeEvent(self,event):
        if self._busy:
            self._close_after = True
            self.cancel()
            event.ignore()
        else:
            event.accept()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication(["XMLBridge"])
    app.setStyle("Fusion")
    app.setApplicationName("XMLBridge")
    app.setOrganizationName("XMLBridge")
    app.setFont(QFont("Segoe UI" if sys.platform=="win32" else "Helvetica Neue",10))
    window = WindowsWindow()
    window.show()
    if args:
        QTimer.singleShot(0,lambda:window.add_paths(args))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
