"""Chinese desktop workbench for inspecting and converting editorial XML."""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .models import (
    FCP_VERSIONS,
    XMEML_VERSIONS,
    ConversionPlan,
    ConversionResult,
    Document,
    Target,
)


STYLE = """
QWidget { background: #171a1e; color: #e9edf2; font-size: 14px; }
QMainWindow { background: #171a1e; }
QLabel { background: transparent; }
QLabel#brand { font-size: 30px; font-weight: 650; letter-spacing: 1px; }
QLabel#subtitle { font-size: 16px; color: #bbc2cc; }
QLabel#eyebrow { color: #c69b5a; font-size: 12px; font-weight: 600; letter-spacing: 1px; }
QLabel#sectionTitle { font-size: 19px; font-weight: 600; }
QLabel#muted { color: #929ca9; font-size: 13px; }
QLabel#metric { font-size: 26px; font-weight: 600; }
QLabel#sourceFormat { font-size: 23px; font-weight: 600; }
QLabel#badge { color: #d6b47f; background: #302a22; border: 1px solid #5a4831;
 border-radius: 13px; padding: 6px 12px; font-size: 12px; }
QFrame#panel { background: #1e2228; border: 1px solid #30363e; border-radius: 12px; }
QFrame#metricCard { background: #252a31; border: 1px solid #30363e; border-radius: 9px; }
QFrame#checkCard { background: #22272e; border: 1px solid #343b44; border-radius: 8px; }
QPushButton { background: #2b313a; border: 1px solid #424b56; border-radius: 7px;
 padding: 9px 13px; min-height: 20px; font-weight: 500; }
QPushButton:hover { background: #363e49; border-color: #677383; }
QPushButton:pressed { background: #20252c; }
QPushButton:disabled { color: #6e7784; background: #242930; border-color: #303740; }
QPushButton#primary { background: #caa16b; border: 1px solid #d9b783; color: #1c1a16; font-weight: 650; }
QPushButton#primary:hover { background: #dfb985; }
QPushButton#primary:disabled { background: #554936; border-color: #62543e; color: #99876b; }
QPushButton#quiet { background: transparent; border-color: #3b434e; }
QLineEdit, QComboBox { background: #15191e; border: 1px solid #3b444f; border-radius: 6px;
 padding: 8px 10px; min-height: 20px; selection-background-color: #7c6544; }
QLineEdit:focus, QComboBox:focus { border-color: #caa16b; }
QLineEdit:disabled, QComboBox:disabled { color: #76818e; border-color: #303740; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #262c34; selection-background-color: #5a4934; padding: 4px; }
QListWidget { background: #1a1f25; border: 1px solid #343c46; border-radius: 7px; outline: none; }
QListWidget::item { padding: 11px 12px; border-bottom: 1px solid #2c333c; }
QListWidget::item:selected { background: #3a332a; color: #f1dfc5; }
QListWidget::item:hover { background: #2a3039; }
QListWidget::indicator { width: 17px; height: 17px; }
QTableWidget { background: #181d23; alternate-background-color: #1f252d;
 border: 1px solid #343d48; border-radius: 6px; gridline-color: #303944; outline: none; }
QTableWidget::item { padding: 9px; border: 0px; }
QTableWidget::item:selected { background: #3a332a; }
QHeaderView::section { background: #29313b; color: #bdc6d2; padding: 10px 8px;
 border: none; border-bottom: 1px solid #414c59; font-weight: 500; }
QTableCornerButton::section { background: #29313b; border: none; }
QTableWidget QComboBox { margin: 5px; font-size: 13px; padding: 5px; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #48515c; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal { background: transparent; height: 9px; }
QScrollBar::handle:horizontal { background: #48515c; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QSplitter::handle { background: transparent; width: 12px; }
"""


def _label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def _display_fps(value: str) -> str:
    try:
        return f"{float(Fraction(value)):.3f}".rstrip("0").rstrip(".") + " fps"
    except (ValueError, ZeroDivisionError):
        return value or "帧率未知"


def _display_duration(value: str) -> str:
    try:
        milliseconds = round(float(Fraction(value.removesuffix("s"))) * 1000)
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02}" + (f".{millis:03}" if millis else "")
    except (ValueError, ZeroDivisionError):
        return value


def collect_candidates(paths: list[str | Path], cancel: threading.Event | None = None) -> list[Path]:
    """Expand folders without double-counting XML files inside FCPXML bundles."""
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path):
        path = path.expanduser().absolute()
        for parent in path.parents:
            if parent.suffix.lower() == ".fcpxmld":
                path = parent
                break
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            found.append(path)

    for raw in paths:
        if cancel and cancel.is_set():
            break
        path = Path(raw).expanduser().absolute()
        if path.suffix.lower() == ".fcpxmld":
            add(path)
        elif path.is_dir():
            for root, dirs, files in os.walk(path, followlinks=False):
                if cancel and cancel.is_set():
                    break
                dirs.sort(key=str.casefold)
                bundles = [name for name in dirs if Path(name).suffix.lower() == ".fcpxmld"]
                for name in bundles:
                    add(Path(root) / name)
                dirs[:] = [name for name in dirs if name not in bundles and not name.startswith(".")]
                for name in sorted(files, key=str.casefold):
                    if Path(name).suffix.lower() in {".xml", ".fcpxml"}:
                        add(Path(root) / name)
        elif path.suffix.lower() in {".xml", ".fcpxml"}:
            add(path)
    return found


@dataclass
class SourceEntry:
    path: Path
    document: Document | None = None
    plan: ConversionPlan | None = None
    selected_ids: list[str] | None = None
    decisions: dict[str, str] = field(default_factory=dict)
    result: ConversionResult | None = None
    status: str = "待分析"
    error: str = ""


class WorkerSignals(QObject):
    item = Signal(object)
    finished = Signal(bool, str)


class Worker(QRunnable):
    def __init__(self, task: Callable, cancel: threading.Event):
        super().__init__()
        self.task = task
        self.cancel = cancel
        self.signals = WorkerSignals()

    def run(self):
        try:
            summary = self.task(self.cancel, self.signals.item.emit) or ""
            self.signals.finished.emit(self.cancel.is_set(), str(summary))
        except Exception as exc:
            self.signals.finished.emit(self.cancel.is_set(), f"任务未完成：{exc}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XMLBridge · 影视 XML 交接")
        self.resize(1540, 970)
        self.setMinimumSize(1240, 800)
        self.setAcceptDrops(True)
        self.entries: dict[str, SourceEntry] = {}
        self._items: dict[str, QListWidgetItem] = {}
        self._busy = False
        self._operation = ""
        self._cancel = threading.Event()
        self._worker: Worker | None = None
        self._showing = False
        self._close_when_done = False
        self._replan_key: str | None = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._replan_timer = QTimer(self)
        self._replan_timer.setSingleShot(True)
        self._replan_timer.setInterval(180)
        self._replan_timer.timeout.connect(self._replan_current)
        self._build_ui()
        self.setStyleSheet(STYLE)
        self._show_entry()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(18)

        header = QHBoxLayout()
        title = QVBoxLayout()
        title.setSpacing(4)
        title.addWidget(_label("XMLBridge", "brand"))
        title.addWidget(_label("影视 XML 交接", "subtitle"))
        header.addLayout(title)
        header.addStretch()
        header.addWidget(_label("本机处理  ·  原件保留", "badge"))
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(12)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_queue())
        splitter.addWidget(self._build_source())
        splitter.addWidget(self._build_conversion())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([250, 345, 845])
        layout.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.activity = _label("拖入 XML、FCPXML 或 .fcpxmld，也可以添加文件夹。", "muted", True)
        footer.addWidget(self.activity, 1)
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setObjectName("quiet")
        self.cancel_button.clicked.connect(self._request_cancel)
        self.cancel_button.setEnabled(False)
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)

    @staticmethod
    def _panel(title: str, eyebrow: str):
        panel = QFrame()
        panel.setObjectName("panel")
        box = QVBoxLayout(panel)
        box.setContentsMargins(17, 19, 17, 17)
        box.setSpacing(13)
        box.addWidget(_label(eyebrow, "eyebrow"))
        box.addWidget(_label(title, "sectionTitle"))
        return panel, box

    def _build_queue(self):
        panel, box = self._panel("文件队列", "01  /  INPUT")
        panel.setMinimumWidth(210)
        self.queue_count = _label("0 个文件", "muted")
        box.addWidget(self.queue_count)
        self.file_list = QListWidget()
        self.file_list.setObjectName("fileQueue")
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.currentItemChanged.connect(lambda *_: self._show_entry())
        box.addWidget(self.file_list, 1)
        self.add_files_button = QPushButton("＋ 添加文件")
        self.add_files_button.clicked.connect(self._choose_files)
        box.addWidget(self.add_files_button)
        self.add_folder_button = QPushButton("添加文件夹 / XML 包")
        self.add_folder_button.setObjectName("quiet")
        self.add_folder_button.clicked.connect(self._choose_folder)
        box.addWidget(self.add_folder_button)
        self.remove_button = QPushButton("移除选中")
        self.remove_button.setObjectName("quiet")
        self.remove_button.clicked.connect(self._remove_selected)
        box.addWidget(self.remove_button)
        box.addWidget(_label("文件夹会扫描 XML 候选；\n目录包仅作为一个文件加入。", "muted", True))
        return panel

    def _build_source(self):
        panel, box = self._panel("源文件分析", "02  /  SOURCE")
        panel.setMinimumWidth(285)
        self.source_format = _label("等待源文件", "sourceFormat", True)
        box.addWidget(self.source_format)
        self.source_path = _label("选择左侧文件查看分析。", "muted", True)
        self.source_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.source_path)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.metrics: dict[str, QLabel] = {}
        for key, caption in [("projects", "项目"), ("clips", "片段"), ("media", "媒体")]:
            card = QFrame()
            card.setObjectName("metricCard")
            contents = QVBoxLayout(card)
            contents.setContentsMargins(12, 11, 12, 11)
            value = _label("—", "metric")
            self.metrics[key] = value
            contents.addWidget(value)
            contents.addWidget(_label(caption, "muted"))
            metrics.addWidget(card)
        box.addLayout(metrics)
        self.source_media_note = _label("", "muted", True)
        box.addWidget(self.source_media_note)

        projects_title = QHBoxLayout()
        projects_title.addWidget(_label("选择交接项目"))
        projects_title.addStretch()
        self.select_all_button = QPushButton("全选")
        self.select_all_button.setObjectName("quiet")
        self.select_all_button.setMaximumWidth(65)
        self.select_all_button.clicked.connect(lambda: self._set_project_checks(True))
        projects_title.addWidget(self.select_all_button)
        self.select_none_button = QPushButton("清空")
        self.select_none_button.setObjectName("quiet")
        self.select_none_button.setMaximumWidth(65)
        self.select_none_button.clicked.connect(lambda: self._set_project_checks(False))
        projects_title.addWidget(self.select_none_button)
        box.addLayout(projects_title)
        self.project_list = QListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.setMinimumHeight(200)
        self.project_list.itemChanged.connect(self._projects_changed)
        box.addWidget(self.project_list, 1)
        self.project_note = _label("分析后列出项目、帧率和时长。", "muted", True)
        box.addWidget(self.project_note)
        self.analyze_button = QPushButton("分析队列")
        self.analyze_button.clicked.connect(self.analyze_queue)
        box.addWidget(self.analyze_button)
        return panel

    def _build_conversion(self):
        panel, box = self._panel("交接目标与影响", "03  /  HANDOFF")
        panel.setMinimumWidth(590)
        targets = QHBoxLayout()
        targets.setSpacing(12)
        self.application_combo = QComboBox()
        self.application_combo.setObjectName("targetApplication")
        self.application_combo.addItems(["通用交接", "Final Cut Pro", "DaVinci Resolve", "Adobe Premiere Pro"])
        self.family_combo = QComboBox()
        self.family_combo.setObjectName("targetFamily")
        self.family_combo.addItem("FCPXML", "fcpxml")
        self.family_combo.addItem("Final Cut 7 XML", "xmeml")
        self.version_combo = QComboBox()
        self.version_combo.setObjectName("targetVersion")
        self.version_combo.addItems(FCP_VERSIONS)
        self.version_combo.setCurrentText("1.9")
        for caption, widget, stretch in [
            ("目标软件", self.application_combo, 2),
            ("XML 类型", self.family_combo, 2),
            ("目标版本", self.version_combo, 1),
        ]:
            column = QVBoxLayout()
            column.setSpacing(6)
            column.addWidget(_label(caption, "muted"))
            column.addWidget(widget)
            targets.addLayout(column, stretch)
        box.addLayout(targets)
        self.application_combo.currentIndexChanged.connect(self._application_changed)
        self.family_combo.currentIndexChanged.connect(self._family_changed)
        self.version_combo.currentIndexChanged.connect(self._target_changed)
        box.addWidget(_label("按目标软件选择 XML 类型；具体软件版本兼容性仍需实际导入验证。", "muted", True))

        impact_heading = QHBoxLayout()
        impact_heading.addWidget(_label("转换影响"))
        impact_heading.addStretch()
        self.impact_count = _label("尚未分析", "muted")
        impact_heading.addWidget(self.impact_count)
        box.addLayout(impact_heading)
        self.impact_table = QTableWidget(0, 4)
        self.impact_table.setObjectName("impactTable")
        self.impact_table.setHorizontalHeaderLabels(["等级", "项目 / 片段 / 时间", "原因", "处理方式"])
        self.impact_table.verticalHeader().hide()
        self.impact_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.impact_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.impact_table.setAlternatingRowColors(True)
        self.impact_table.setWordWrap(True)
        header = self.impact_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.impact_table.setColumnWidth(0, 62)
        self.impact_table.setColumnWidth(1, 150)
        self.impact_table.setColumnWidth(3, 178)
        box.addWidget(self.impact_table, 1)
        self.plan_note = _label("先分析源文件，再查看可执行的转换与需要处理的项目。", "muted", True)
        box.addWidget(self.plan_note)

        output = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setObjectName("outputDirectory")
        self.output_edit.setPlaceholderText("选择导出目录")
        self.output_edit.textChanged.connect(self._update_controls)
        self.output_browse = QPushButton("选择目录")
        self.output_browse.setObjectName("quiet")
        self.output_browse.clicked.connect(self._choose_output)
        output.addWidget(self.output_edit, 1)
        output.addWidget(self.output_browse)
        box.addWidget(_label("导出目录", "muted"))
        box.addLayout(output)
        self.export_button = QPushButton("导出队列")
        self.export_button.setObjectName("primary")
        self.export_button.setToolTip("逐文件处理：可转换的文件导出 XML，存在阻断的文件仅生成诊断报告。")
        self.export_button.clicked.connect(self.export_queue)
        box.addWidget(self.export_button)

        checks_row = QHBoxLayout()
        checks_row.setSpacing(9)
        self.check_labels: dict[str, QLabel] = {}
        for key, caption in [("structure", "01  结构验证"), ("timeline", "02  时间线验证"), ("host", "03  软件导入")]:
            card = QFrame()
            card.setObjectName("checkCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(11, 10, 11, 10)
            card_layout.setSpacing(5)
            card_layout.addWidget(_label(caption, "muted"))
            state = _label("未验证" if key == "host" else "尚未运行")
            card_layout.addWidget(state)
            self.check_labels[key] = state
            checks_row.addWidget(card)
        box.addLayout(checks_row)
        self.result_note = _label("验证状态来自实际结果；软件导入验证独立记录。", "muted", True)
        self.result_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.result_note)
        result_actions = QHBoxLayout()
        self.report_button = QPushButton("打开交接报告")
        self.report_button.setObjectName("quiet")
        self.report_button.clicked.connect(self._open_report)
        self.output_button = QPushButton("在 Finder 中打开输出")
        self.output_button.setObjectName("quiet")
        self.output_button.clicked.connect(self._open_output)
        result_actions.addWidget(self.report_button)
        result_actions.addWidget(self.output_button)
        box.addLayout(result_actions)
        return panel

    def _current_entry(self) -> SourceEntry | None:
        item = self.file_list.currentItem()
        return self.entries.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _target(self) -> Target:
        return Target(
            family=self.family_combo.currentData(),
            version=self.version_combo.currentText(),
            application=self.application_combo.currentText(),
        )

    def _choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "添加影视 XML", "", "影视 XML (*.xml *.fcpxml);;所有文件 (*)")
        if files:
            self.add_paths(files)

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择文件夹或 .fcpxmld 目录包")
        if path:
            self.add_paths([path])

    def _choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择导出目录", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def add_paths(self, paths: list[str | Path]):
        if self._busy or not paths:
            return
        raw_paths = list(paths)

        def task(cancel, emit):
            candidates = collect_candidates(raw_paths, cancel)
            emit({"kind": "candidates", "paths": candidates})
            return f"找到 {len(candidates)} 个 XML 候选。请选择目标后分析队列。"

        self._launch("扫描文件", task)

    def _add_candidates(self, paths: list[Path]):
        for path in paths:
            key = str(path.resolve())
            if key in self.entries:
                continue
            self.entries[key] = SourceEntry(path=path)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(190, 76))
            item.setToolTip(str(path))
            self.file_list.addItem(item)
            self._items[key] = item
            self._refresh_queue_item(key)
        self.queue_count.setText(f"{len(self.entries)} 个文件")
        if self.file_list.currentItem() is None and self.file_list.count():
            self.file_list.setCurrentRow(0)

    def _remove_selected(self):
        if self._busy:
            return
        for item in self.file_list.selectedItems():
            key = item.data(Qt.ItemDataRole.UserRole)
            self.entries.pop(key, None)
            self._items.pop(key, None)
            self.file_list.takeItem(self.file_list.row(item))
        self.queue_count.setText(f"{len(self.entries)} 个文件")
        self._show_entry()

    def _refresh_queue_item(self, key: str):
        entry = self.entries[key]
        item = self._items[key]
        family = ""
        if entry.document:
            family = f"{entry.document.family.upper()} {entry.document.version}  ·  "
        item.setText(f"{entry.path.name}\n{family}{entry.status}")
        item.setToolTip(str(entry.path) + ("\n" + entry.error if entry.error else ""))
        item.setForeground(QColor("#e7b282" if entry.error else "#e9edf2"))

    def _show_entry(self):
        self._showing = True
        entry = self._current_entry()
        self.project_list.clear()
        self.impact_table.setRowCount(0)
        for value in self.metrics.values():
            value.setText("—")
        self.source_media_note.setText("")
        self.plan_note.setText("先分析源文件，再查看可执行的转换与需要处理的项目。")
        self.impact_count.setText("尚未分析")
        if not entry:
            self.source_format.setText("等待源文件")
            self.source_path.setText("选择左侧文件查看分析。")
            self.project_note.setText("分析后列出项目、帧率和时长。")
        else:
            self.source_path.setText(entry.path.name)
            self.source_path.setToolTip(str(entry.path))
            doc = entry.document
            if doc:
                self.source_format.setText(f"{doc.family.upper()} {doc.version}")
                selected = set(entry.selected_ids or [])
                for project in doc.projects:
                    item = QListWidgetItem(
                        f"{project.name}\n{_display_fps(project.fps)}  ·  {_display_duration(project.duration)}  ·  {project.clip_count} 片段"
                    )
                    item.setData(Qt.ItemDataRole.UserRole, project.id)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if project.id in selected else Qt.CheckState.Unchecked)
                    item.setToolTip(f"项目 ID：{project.id}\n媒体引用：{project.media_count}\n精确帧率：{project.fps}\n精确时长：{project.duration}")
                    self.project_list.addItem(item)
                self.metrics["projects"].setText(str(len(doc.projects)))
                self.metrics["clips"].setText(str(sum(p.clip_count for p in doc.projects)))
                self.metrics["media"].setText(str(len(doc.media)))
                missing = sum(media.exists is False for media in doc.media)
                unknown = sum(media.exists is None for media in doc.media)
                self.source_media_note.setText(
                    f"媒体引用：{missing} 个离线，{unknown} 个未检查。" if doc.media else "源文件未列出媒体引用。"
                )
                self.project_note.setText(
                    f"已选择 {len(selected)} / {len(doc.projects)} 个项目。仅导出勾选项目。"
                    if doc.projects else "没有项目时间线，按文件级分析与诊断处理。"
                )
            else:
                self.source_format.setText("待分析" if not entry.error else "分析未完成")
                self.project_note.setText(entry.error or "点击“分析队列”读取源文件。")
            if entry.plan:
                self._populate_issues(entry)
            elif entry.error:
                self.plan_note.setText(entry.error)
        self._show_checks(entry.result if entry else None)
        self._showing = False
        self._update_controls()

    def _populate_issues(self, entry: SourceEntry):
        plan = entry.plan
        if not plan:
            return
        self.impact_table.setRowCount(len(plan.issues))
        names = {"error": "阻断", "warning": "注意", "info": "信息"}
        colors = {"error": "#e79a8d", "warning": "#d7b27b", "info": "#a7b8c9"}
        for row, issue in enumerate(plan.issues):
            level = QTableWidgetItem(names.get(issue.severity, issue.severity))
            level.setForeground(QColor(colors.get(issue.severity, "#a7b8c9")))
            location = "\n".join(part for part in [issue.project, issue.clip, issue.time] if part) or "文件级"
            where = QTableWidgetItem(location)
            where.setToolTip("\n".join(part for part in [location, issue.location] if part))
            reason = QTableWidgetItem(issue.message)
            reason.setToolTip("\n".join(part for part in [issue.code, issue.details, issue.location] if part))
            self.impact_table.setItem(row, 0, level)
            self.impact_table.setItem(row, 1, where)
            self.impact_table.setItem(row, 2, reason)
            if issue.choices:
                choice = QComboBox()
                choice.addItem("请选择处理方式", "")
                for code, title in issue.choices.items():
                    choice.addItem(title, code)
                current = choice.findData(entry.decisions.get(issue.id, ""))
                choice.setCurrentIndex(max(current, 0))
                choice.setToolTip(issue.details or issue.message)
                choice.currentIndexChanged.connect(
                    lambda index, widget=choice, key=str(entry.path.resolve()), issue_id=issue.id:
                    self._decision_changed(key, issue_id, widget.currentData())
                )
                self.impact_table.setCellWidget(row, 3, choice)
            else:
                self.impact_table.setItem(row, 3, QTableWidgetItem("需先解决源文件" if issue.severity == "error" else "保留并记录"))
            self.impact_table.setRowHeight(row, 82)
        self._refresh_plan_summary(entry)

    def _refresh_plan_summary(self, entry: SourceEntry):
        if not entry.plan:
            return
        blockers = entry.plan.blockers(entry.decisions)
        self.impact_count.setText(f"{len(entry.plan.issues)} 项影响  ·  {len(blockers)} 项待处理")
        if blockers:
            self.plan_note.setText(f"还有 {len(blockers)} 项阻断。未解决时仅生成诊断报告；带选项的项目可逐项选择处理方式。")
        elif entry.document and entry.document.projects and not entry.selected_ids:
            self.plan_note.setText("至少勾选一个项目后才能导出。")
        elif entry.plan.steps:
            self.plan_note.setText("转换步骤：" + " · ".join(entry.plan.steps))
        else:
            self.plan_note.setText("当前分析没有阻断项。导出时执行结构与时间线验证。")

    def _decision_changed(self, key: str, issue_id: str, action: str):
        entry = self.entries.get(key)
        if not entry or self._busy:
            return
        if action:
            entry.decisions[issue_id] = action
        else:
            entry.decisions.pop(issue_id, None)
        entry.result = None
        entry.status = "可导出" if self._is_ready(entry) else "待处理"
        self._refresh_queue_item(key)
        self._refresh_plan_summary(entry)
        self._show_checks(None)
        self._update_controls()

    def _set_project_checks(self, checked: bool):
        if self._busy:
            return
        self._showing = True
        for index in range(self.project_list.count()):
            self.project_list.item(index).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._showing = False
        self._projects_changed()

    def _projects_changed(self, *_):
        if self._showing or self._busy:
            return
        entry = self._current_entry()
        if not entry or not entry.document:
            return
        entry.selected_ids = [
            self.project_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.project_list.count())
            if self.project_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        entry.plan = None
        entry.decisions.clear()
        entry.result = None
        entry.status = "待更新分析" if entry.selected_ids else "未选择项目"
        self._refresh_queue_item(str(entry.path.resolve()))
        self.impact_table.setRowCount(0)
        self.impact_count.setText("正在更新" if entry.selected_ids else "未选择项目")
        self.project_note.setText(f"已选择 {len(entry.selected_ids)} / {len(entry.document.projects)} 个项目。仅导出勾选项目。")
        self.plan_note.setText("正在重新计算所选项目的影响。" if entry.selected_ids else "至少勾选一个项目后才能导出。")
        self._show_checks(None)
        self._update_controls()
        if entry.selected_ids:
            self._replan_key = str(entry.path.resolve())
            self._replan_timer.start()
        else:
            self._replan_timer.stop()

    def _application_changed(self, *_):
        application = self.application_combo.currentText()
        family = "xmeml" if "Premiere" in application else "fcpxml" if application == "Final Cut Pro" else None
        if family and family != self.family_combo.currentData():
            self.family_combo.setCurrentIndex(self.family_combo.findData(family))
        else:
            self._target_changed()

    def _family_changed(self, *_):
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        family = self.family_combo.currentData()
        self.version_combo.addItems(FCP_VERSIONS if family == "fcpxml" else XMEML_VERSIONS)
        self.version_combo.setCurrentText("1.9" if family == "fcpxml" else "5")
        self.version_combo.blockSignals(False)
        self._target_changed()

    def _target_changed(self, *_):
        if self._busy:
            return
        self._replan_timer.stop()
        for key, entry in self.entries.items():
            entry.plan = None
            entry.decisions.clear()
            entry.result = None
            if entry.document:
                entry.status = "待重新分析"
            self._refresh_queue_item(key)
        self._show_entry()
        if self.entries:
            self.activity.setText("交接目标已更新；请重新分析队列。")

    def analyze_queue(self):
        if self._busy or not self.entries:
            return
        self._replan_timer.stop()
        target = self._target()
        inputs = [(key, entry.path, None if entry.selected_ids is None else list(entry.selected_ids)) for key, entry in self.entries.items()]
        for key, _, _ in inputs:
            entry = self.entries[key]
            entry.plan = None
            entry.result = None
            entry.decisions.clear()
            entry.error = ""
            entry.status = "等待分析"
            self._refresh_queue_item(key)

        def task(cancel, emit):
            from . import core
            completed = 0
            failures = 0
            for key, path, previous_selection in inputs:
                if cancel.is_set():
                    break
                emit({"kind": "status", "key": key, "status": "正在分析"})
                try:
                    doc = core.inspect(path)
                    ids = [p.id for p in doc.projects]
                    selected = ids if previous_selection is None else [pid for pid in previous_selection if pid in ids]
                    if cancel.is_set():
                        break
                    selection = selected if doc.projects else None
                    plan = core.plan_conversion(doc, target, project_ids=selection)
                    emit({"kind": "analyzed", "key": key, "document": doc, "plan": plan, "selected_ids": selection})
                    completed += 1
                except Exception as exc:
                    failures += 1
                    emit({"kind": "error", "key": key, "message": str(exc)})
            return f"分析完成 {completed} / {len(inputs)} 个文件" + (f"；{failures} 个失败。" if failures else "。")

        self._launch("分析队列", task)

    def _replan_current(self):
        entry = self.entries.get(self._replan_key) if self._replan_key else self._current_entry()
        if self._busy or not entry or not entry.document or not entry.selected_ids:
            return
        key = str(entry.path.resolve())
        document = entry.document
        selected = list(entry.selected_ids)
        target = self._target()

        def task(cancel, emit):
            from . import core
            try:
                plan = core.plan_conversion(document, target, project_ids=selected)
                if not cancel.is_set():
                    emit({"kind": "analyzed", "key": key, "document": document, "plan": plan, "selected_ids": selected})
                return "所选项目的分析已更新。"
            except Exception as exc:
                emit({"kind": "error", "key": key, "message": str(exc)})
                return "所选项目的分析未完成。"

        self._launch("更新项目分析", task)

    @staticmethod
    def _is_ready(entry: SourceEntry):
        return bool(MainWindow._has_plan(entry) and not entry.plan.blockers(entry.decisions))

    @staticmethod
    def _has_plan(entry: SourceEntry):
        return bool(entry.document and entry.plan and (entry.selected_ids or not entry.document.projects))

    def export_queue(self):
        if self._busy or not any(self._has_plan(entry) for entry in self.entries.values()):
            return
        output_text = self.output_edit.text().strip()
        if not output_text:
            self.activity.setText("请先选择导出目录。")
            return
        output_dir = Path(output_text).expanduser().absolute()
        if output_dir.exists() and not output_dir.is_dir():
            self.activity.setText("导出位置是文件，请选择文件夹。")
            return
        inputs = [(key, entry.plan, dict(entry.decisions)) for key, entry in self.entries.items() if self._has_plan(entry)]
        skipped = len(self.entries) - len(inputs)
        for key, _, _ in inputs:
            self.entries[key].result = None
            self.entries[key].status = "等待导出"
            self.entries[key].error = ""
            self._refresh_queue_item(key)
        self._show_checks(None)

        def task(cancel, emit):
            from . import core
            success = 0
            unsuccessful = 0
            for key, plan, decisions in inputs:
                if cancel.is_set():
                    break
                emit({"kind": "status", "key": key, "status": "正在转换与验证"})
                try:
                    result = core.convert(plan, decisions, output_dir, cancel_event=cancel)
                except Exception as exc:
                    result = ConversionResult(status="failed", message=str(exc))
                emit({"kind": "result", "key": key, "result": result})
                if self._result_verified(result):
                    success += 1
                else:
                    unsuccessful += 1
            summary = f"导出完成 {success} / {len(inputs)} 个文件" + (f"；{unsuccessful} 个未成功，请查看对应报告。" if unsuccessful else "。目标软件导入仍以独立验证结果为准。")
            if skipped:
                summary += f" {skipped} 个未分析或未选择项目的文件未处理。"
            return summary

        self._launch("导出与验证", task)

    @staticmethod
    def _result_verified(result: ConversionResult):
        return (
            result.status == "success"
            and bool(result.output_path)
            and result.checks.get("structure") == "passed"
            and result.checks.get("timeline") == "passed"
        )

    def _launch(self, operation: str, task: Callable):
        if self._busy:
            return
        self._busy = True
        self._operation = operation
        self._cancel = threading.Event()
        self.activity.setText(f"{operation}…")
        self.cancel_button.setText("取消任务")
        self._worker = Worker(task, self._cancel)
        self._worker.signals.item.connect(self._on_worker_item)
        self._worker.signals.finished.connect(self._on_worker_finished)
        self._update_controls()
        self._pool.start(self._worker)

    def _on_worker_item(self, event: dict):
        kind = event.get("kind")
        if kind == "candidates":
            self._add_candidates(event["paths"])
            return
        key = event.get("key")
        entry = self.entries.get(key)
        if not entry:
            return
        if kind == "status":
            entry.status = event["status"]
            self.activity.setText(f"{entry.path.name}  ·  {entry.status}…")
        elif kind == "error":
            entry.error = event["message"]
            entry.status = "分析失败"
            entry.plan = None
            entry.result = None
        elif kind == "analyzed":
            entry.document = event["document"]
            entry.plan = event["plan"]
            entry.selected_ids = event["selected_ids"]
            entry.error = ""
            entry.status = "可导出" if self._is_ready(entry) else "待处理" if entry.selected_ids else "未选择项目"
        elif kind == "result":
            entry.result = event["result"]
            names = {"failed": "导出失败", "blocked": "已阻断", "cancelled": "已取消"}
            entry.status = "验证通过 · 待软件导入" if self._result_verified(entry.result) else names.get(entry.result.status, "结果待核查")
            if self._result_verified(entry.result) and entry.result.checks.get("host") == "passed":
                entry.status = "三道验证通过"
            entry.error = "" if self._result_verified(entry.result) else entry.result.message or "未通过导出验证。"
        self._refresh_queue_item(key)
        if self._current_entry() is entry and kind != "status":
            self._show_entry()

    def _on_worker_finished(self, cancelled: bool, message: str):
        self._busy = False
        self._worker = None
        if cancelled:
            self.activity.setText("任务已取消；已经完成的输出和报告可以保留查看。")
        else:
            self.activity.setText(message or "任务已结束。")
        for key, entry in self.entries.items():
            if entry.status in {"等待分析", "正在分析", "等待导出", "正在转换与验证"}:
                entry.status = "未处理 · 已取消" if cancelled else "未完成"
                self._refresh_queue_item(key)
        self.cancel_button.setText("取消任务")
        self._show_entry()
        if self._close_when_done:
            self.close()

    def _request_cancel(self):
        if self._busy:
            self._cancel.set()
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("正在取消…")
            self.activity.setText("正在安全取消，等待当前步骤结束…")

    def _show_checks(self, result: ConversionResult | None):
        checks = result.checks if result else {}
        states = {
            "passed": ("通过", "#9ebea7"),
            "failed": ("未通过", "#e79a8d"),
            "blocked": ("已阻断", "#e3b17c"),
            "not_run": ("未验证", "#929ca9"),
            "skipped": ("未验证", "#929ca9"),
        }
        for key, label in self.check_labels.items():
            default = "未验证" if key == "host" else "尚未运行"
            text, color = states.get(str(checks.get(key, "")), (default, "#929ca9"))
            label.setText(text)
            label.setStyleSheet(f"color: {color}; font-weight: 600;")
        if result:
            if result.status == "success" and not self._result_verified(result):
                self.result_note.setText("结果待核查：输出或必要验证未完成，尚未标记为成功。" + (f" {result.message}" if result.message else ""))
            else:
                self.result_note.setText(result.message or ("输出已生成。请在目标软件导入并核对。" if self._result_verified(result) else "转换未成功，查看报告了解原因。"))
        else:
            self.result_note.setText("验证状态来自实际结果；软件导入验证独立记录。")

    def _update_controls(self, *_):
        idle = not self._busy
        for widget in [
            self.add_files_button, self.add_folder_button, self.application_combo,
            self.family_combo, self.version_combo, self.output_edit, self.output_browse,
            self.select_all_button, self.select_none_button, self.project_list,
        ]:
            widget.setEnabled(idle)
        self.remove_button.setEnabled(idle and bool(self.entries))
        self.analyze_button.setEnabled(idle and bool(self.entries))
        planned = [entry for entry in self.entries.values() if self._has_plan(entry)]
        self.export_button.setEnabled(idle and bool(planned) and bool(self.output_edit.text().strip()))
        blocked = sum(not self._is_ready(entry) for entry in planned)
        self.export_button.setText(
            f"导出与诊断 · {len(planned)} 个文件（{blocked} 个仅报告）" if blocked
            else f"导出队列 · {len(planned)} 个文件" if planned else "导出队列"
        )
        self.cancel_button.setEnabled(self._busy and not self._cancel.is_set())
        for row in range(self.impact_table.rowCount()):
            combo = self.impact_table.cellWidget(row, 3)
            if combo:
                combo.setEnabled(idle)
        entry = self._current_entry()
        result = entry.result if entry else None
        self.report_button.setEnabled(idle and bool(result and (result.report_html or result.report_json)))
        self.output_button.setEnabled(idle and bool(result and result.output_path and Path(result.output_path).exists()))

    def _open_report(self):
        entry = self._current_entry()
        if entry and entry.result:
            path = entry.result.report_html or entry.result.report_json
            if path:
                self._open_path(Path(path))

    def _open_output(self):
        entry = self._current_entry()
        if entry and entry.result and entry.result.output_path:
            self._open_path(Path(entry.result.output_path).parent)

    def _open_path(self, path: Path):
        if not path.exists():
            self.activity.setText(f"文件不存在：{path}")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.absolute()))):
            self.activity.setText(f"系统未能打开：{path}")

    def dragEnterEvent(self, event):
        if not self._busy and event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if self._busy:
            event.ignore()
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        self.add_paths(paths)
        event.acceptProposedAction()

    def closeEvent(self, event):
        if self._busy:
            self._close_when_done = True
            self._request_cancel()
            self.activity.setText("正在取消当前任务，结束后关闭窗口。")
            event.ignore()
        else:
            self._replan_timer.stop()
            event.accept()


def main(argv: list[str] | None = None):
    args = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(args)
    app.setApplicationName("XMLBridge")
    app.setApplicationDisplayName("XMLBridge · 影视 XML 交接")
    app.setOrganizationName("XMLBridge")
    font = QFont("PingFang SC", 12)
    app.setFont(font)
    window = MainWindow()
    window.show()
    if len(args) > 1:
        QTimer.singleShot(0, lambda: window.add_paths(args[1:]))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
