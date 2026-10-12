"""Side panel that edits the selected step. The form is generated from the step's pydantic
fields (title = label, description = help text), so new step types need no UI code."""

from __future__ import annotations

import types
import typing
from collections.abc import Callable
from typing import Any, Literal, get_args, get_origin

from pydantic import ValidationError
from pydantic.fields import FieldInfo
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from flowpilot.core.steps import COMMON_FIELDS, HIDDEN_FIELDS, Delay, StepBase
from flowpilot.ui.widgets.clay import ClayButton, ClaySpinBox, ClayToggle, HelpIcon, IconButton, muted_label, title_label

MULTILINE = {"code", "values", "items", "items_list", "fallbacks", "files", "note"}
SELECTOR_FIELDS = {"selector", "target_selector"}
FILE_FIELDS = {"file"}
VARIABLE_FIELDS = {"text", "url", "value", "message", "values", "items_list", "contains", "key", "code", "name",
                   "variable"}
OPTION_LABELS = {"on_error": {"stop": "Stop (account failed)", "skip": "Skip this step", "goto": "Go to a label",
                              "fail_close": "Mark failed + close browser"},
                 "humanize": {"default": "Use Settings", "on": "On", "off": "Off"}}

BUILTIN_VARS = ["now", "today", "timestamp", "random_int", "uuid", "loop.item", "loop.index", "row.column",
                "error.message", "account.name", "account.profile_id", "account.group", "account.position"]


def _unwrap_optional(ann: Any) -> tuple[Any, bool]:
    origin = get_origin(ann)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(ann) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return ann, False


class StepForm(QWidget):
    """Edits one step; emits :attr:`changed` with an updated copy."""

    changed = Signal(object)  # StepBase
    pick_requested = Signal(str)  # field name

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Transparent")
        self.step: StepBase | None = None
        self.labels: list[str] = []
        self.variables: list[str] = []
        self._setters: dict[str, Callable[[Any], None]] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.header = title_label("No step selected", "subtitle")
        self.header.setWordWrap(True)
        self.desc = muted_label("Select a step on the left to change its settings.")
        self.problems = QLabel("")
        self.problems.setProperty("role", "error")
        self.problems.setWordWrap(True)
        outer.addWidget(self.header)
        outer.addWidget(self.desc)
        outer.addWidget(self.problems)
        area = QScrollArea()
        area.setWidgetResizable(True)
        self.host = QWidget()
        self.host.setObjectName("Transparent")
        self.body = QVBoxLayout(self.host)
        self.body.setContentsMargins(0, 0, 6, 0)
        area.setWidget(self.host)
        outer.addWidget(area, 1)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._flush)
        self._pending: dict[str, Any] = {}

    # ------------------------------------------------------------------ building
    def set_context(self, labels: list[str], variables: list[str]) -> None:
        self.labels = labels
        self.variables = variables

    def set_step(self, step: StepBase | None, problems: list[str] | None = None) -> None:
        self._flush()
        self.step = step
        _clear_layout(self.body)
        self._setters.clear()
        if step is None:
            self.header.setText("No step selected")
            self.desc.setText("Select a step on the left to change its settings.")
            self.problems.setText("")
            return
        self.header.setText(step.TITLE + ("  (Advanced)" if step.ADVANCED else ""))
        self.desc.setText(step.DESCRIPTION or step.summary())
        self.show_problems(problems or [])
        own = [n for n in type(step).model_fields if n not in COMMON_FIELDS and n not in HIDDEN_FIELDS]
        main = _form_layout()
        if step.USES_SELECTOR:
            self._add_field(main, "selector")
            self._add_field(main, "fallbacks")
        for name in own:
            self._add_field(main, name)
        if main.rowCount() == 0:
            main.addRow(muted_label("This step has no settings."))
        self.body.addLayout(main)
        if step.BLOCK in ("else", "end_if", "end_loop", "end_try", "catch"):
            self.body.addStretch(1)
            return
        toggle = ClayButton("More options", "sliders", "ghost", compact=True)
        toggle.setCheckable(True)
        self.body.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        extra = QWidget()
        extra.setObjectName("Transparent")
        opts = _form_layout()
        extra_lay = QVBoxLayout(extra)
        extra_lay.setContentsMargins(0, 0, 0, 0)
        extra_lay.addLayout(opts)
        for name in ("timeout", "retries", "retry_delay", "on_error", "on_error_label", "delay_before",
                     "delay_after", "humanize", "label", "note"):
            self._add_field(opts, name)
        extra.setVisible(False)
        toggle.toggled.connect(extra.setVisible)
        self.body.addWidget(extra)
        self.body.addStretch(1)

    def show_problems(self, problems: list[str]) -> None:
        self.problems.setText("\n".join(f"⚠ {p}" for p in problems))
        self.problems.setVisible(bool(problems))

    def _label(self, info: FieldInfo, name: str) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lab = QLabel(info.title or name.replace("_", " ").capitalize())
        lab.setWordWrap(True)
        lay.addWidget(lab)
        if info.description:
            lay.addWidget(HelpIcon(info.description))
        lay.addStretch(1)
        return w

    def _add_field(self, form: QFormLayout, name: str) -> None:
        assert self.step is not None
        fields = type(self.step).model_fields
        if name not in fields:
            return
        info = fields[name]
        value = getattr(self.step, name)
        ann, optional = _unwrap_optional(info.annotation)
        editor = self._editor(name, ann, optional, value, info)
        form.addRow(self._label(info, name), editor)

    def _editor(self, name: str, ann: Any, optional: bool, value: Any, info: FieldInfo) -> QWidget:
        origin = get_origin(ann)
        if ann is bool:
            t = ClayToggle(bool(value))
            t.toggled.connect(lambda v: self._set(name, v, now=True))
            return t
        if origin is Literal:
            box = QComboBox()
            labels = OPTION_LABELS.get(name, {})
            for opt in get_args(ann):
                box.addItem(labels.get(opt, str(opt).replace("_", " ").capitalize()), opt)
            box.setCurrentIndex(max(0, box.findData(value)))
            box.currentIndexChanged.connect(lambda _: self._set(name, box.currentData(), now=True))
            return box
        if ann is Delay:
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lo = ClaySpinBox(value.min, 0, 3600, 0.5, 1, " s")
            hi = ClaySpinBox(value.max, 0, 3600, 0.5, 1, " s")
            upd = lambda *_: self._set(name, Delay(min=lo.value(), max=hi.value()))  # noqa: E731
            lo.valueChanged.connect(upd)
            hi.valueChanged.connect(upd)
            lay.addWidget(lo)
            lay.addWidget(QLabel("to"))
            lay.addWidget(hi)
            lay.addStretch(1)
            return row
        if ann in (int, float) and not optional:
            lo, hi = -1e9, 1e9
            for m in info.metadata:
                lo = getattr(m, "ge", lo) if getattr(m, "ge", None) is not None else lo
                hi = getattr(m, "le", hi) if getattr(m, "le", None) is not None else hi
            lo = max(lo, -100000)
            hi = min(hi, 1000000)
            spin = ClaySpinBox(float(value), lo, hi, 1 if ann is int else 0.5, 0 if ann is int else 2)
            spin.valueChanged.connect(lambda v: self._set(name, int(v) if ann is int else v))
            return spin
        if ann in (int, float) and optional:
            edit = QLineEdit("" if value is None else f"{value:g}")
            edit.setPlaceholderText("Default")
            edit.textChanged.connect(lambda t: self._set(name, _num(t, ann)))
            return edit
        if origin is list:
            text = QPlainTextEdit("\n".join(str(v) for v in (value or [])))
            text.setMaximumHeight(110)
            text.setPlaceholderText("One per line")
            text.textChanged.connect(lambda: self._set(name, [ln.strip() for ln in text.toPlainText().splitlines()
                                                              if ln.strip()]))
            if name == "files":
                return self._with_buttons(text, name, browse_many=True)
            return self._with_buttons(text, name) if name in VARIABLE_FIELDS else text
        # strings
        if name in MULTILINE or name == "code":
            text = QPlainTextEdit(str(value or ""))
            text.setMaximumHeight(160 if name == "code" else 80)
            text.textChanged.connect(lambda: self._set(name, text.toPlainText()))
            return self._with_buttons(text, name) if name in VARIABLE_FIELDS else text
        if name in ("on_error_label", "goto_label"):
            box = QComboBox()
            box.setEditable(True)
            box.addItems([""] + self.labels)
            box.setCurrentText(str(value or ""))
            box.currentTextChanged.connect(lambda t: self._set(name, t))
            return box
        edit = QLineEdit(str(value or ""))
        if name in SELECTOR_FIELDS:
            edit.setPlaceholderText("Click 'Pick' and choose the element in the browser")
        edit.textChanged.connect(lambda t: self._set(name, t))
        return self._with_buttons(edit, name)

    def _with_buttons(self, editor: QWidget, name: str, browse_many: bool = False) -> QWidget:
        buttons: list[QWidget] = []
        if name in SELECTOR_FIELDS:
            pick = ClayButton("Pick", "target", "secondary", compact=True, tooltip="Choose the element in the browser")
            pick.clicked.connect(lambda: self.pick_requested.emit(name))
            buttons.append(pick)
        if name in VARIABLE_FIELDS:
            var = IconButton("variable", "Insert a variable", size=28)
            var.clicked.connect(lambda: self._variable_menu(editor, var))
            buttons.append(var)
        if name in FILE_FIELDS or browse_many:
            browse = IconButton("folder", "Choose file", size=28)
            browse.clicked.connect(lambda: self._browse(editor, browse_many))
            buttons.append(browse)
        if not buttons:
            return editor
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(editor, 1)
        for b in buttons:
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        return w

    def _variable_menu(self, editor: QWidget, anchor: QWidget) -> None:
        menu = QMenu(self)
        acc = menu.addMenu("Account variables")
        for v in self.variables:
            if v.startswith("account."):
                acc.addAction("{{" + v + "}}").triggered.connect(lambda _=False, t=v: _insert(editor, "{{" + t + "}}"))
        flow = menu.addMenu("Flow variables")
        for v in self.variables:
            if not v.startswith("account."):
                flow.addAction("{{" + v + "}}").triggered.connect(lambda _=False, t=v: _insert(editor, "{{" + t + "}}"))
        built = menu.addMenu("Built-in")
        for v in BUILTIN_VARS:
            built.addAction("{{" + v + "}}").triggered.connect(lambda _=False, t=v: _insert(editor, "{{" + t + "}}"))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _browse(self, editor: QWidget, many: bool) -> None:
        if many:
            files, _ = QFileDialog.getOpenFileNames(self, "Choose files")
            if files and isinstance(editor, QPlainTextEdit):
                existing = editor.toPlainText().strip()
                editor.setPlainText("\n".join(([existing] if existing else []) + files))
        else:
            file, _ = QFileDialog.getOpenFileName(self, "Choose file", "", "CSV (*.csv *.txt);;All files (*)")
            if file and isinstance(editor, QLineEdit):
                editor.setText(file)

    # ------------------------------------------------------------------ updates
    def _set(self, name: str, value: Any, now: bool = False) -> None:
        self._pending[name] = value
        if now:
            self._flush()
        else:
            self._debounce.start()

    def _flush(self) -> None:
        self._debounce.stop()
        if not self._pending or self.step is None:
            self._pending.clear()
            return
        data = self.step.model_dump()
        if "selector" in self._pending and self._pending["selector"] != self.step.selector:
            data["element_desc"] = ""  # typed by hand: the recorded element name no longer applies
        data.update({k: (v.model_dump() if isinstance(v, Delay) else v) for k, v in self._pending.items()})
        self._pending.clear()
        try:
            new = type(self.step).model_validate(data)
        except ValidationError:
            return
        if new != self.step:
            self.step = new
            self.changed.emit(new)


def _form_layout() -> QFormLayout:
    """Labels above fields: the side panel is narrow."""
    form = QFormLayout()
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
    form.setVerticalSpacing(4)
    form.setContentsMargins(0, 0, 0, 6)
    return form


def _num(text: str, ann: Any) -> Any:
    text = text.strip().replace(",", ".")
    if not text:
        return None
    try:
        return int(float(text)) if ann is int else float(text)
    except ValueError:
        return None


def _insert(editor: QWidget, text: str) -> None:
    target = editor
    if not isinstance(target, (QLineEdit, QPlainTextEdit)):
        found = editor.findChild(QLineEdit) or editor.findChild(QPlainTextEdit)
        if found is None:
            return
        target = found
    if isinstance(target, QLineEdit):
        target.insert(text)
    else:
        target.insertPlainText(text)
    target.setFocus()


def _clear_layout(layout: Any) -> None:
    """Remove everything from a layout now (deleteLater alone leaves widgets visible until
    the event loop runs, which shows stale fields for a moment)."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())
            item.layout().deleteLater()
