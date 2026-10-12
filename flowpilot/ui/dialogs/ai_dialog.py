"""'Build steps with AI' dialog: describe the task, preview the drafted steps, add them."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from flowpilot.core.steps import StepBase
from flowpilot.services.ai_builder import AiBuilderError, Draft, draft_offline, draft_with_claude, page_outline
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import run_async
from flowpilot.ui.icons import has_icon, icon
from flowpilot.ui.theme.palette import pal
from flowpilot.ui.widgets.clay import ClayButton, ClayToggle, muted_label, title_label
from flowpilot.ui.widgets.step_list import category_color

EXAMPLES = ("Examples:\n• open instagram.com, log in, then like the first 3 posts\n"
            "• search “{{account.search_keyword}}” on duckduckgo.com and open the first result, scroll slowly\n"
            "• if a cookie banner shows, click Accept, otherwise continue")


class AiBuilderDialog(QDialog):
    def __init__(self, app: AppContext, existing: list[StepBase], account_vars: list[str],
                 has_selection: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.existing = existing
        self.account_vars = account_vars
        self.draft: Draft | None = None
        self.setWindowTitle("Build steps with AI")
        self.resize(720, 680)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        head = QHBoxLayout()
        badge = QLabel()
        badge.setPixmap(icon("sparkles", pal().primary, 28).pixmap(28, 28))
        head.addWidget(badge)
        head.addWidget(title_label("Build steps with AI", "subtitle"))
        head.addStretch(1)
        root.addLayout(head)
        root.addWidget(muted_label("Describe what the browser should do, in your own words. FlowPilot drafts the "
                                   "steps — you check them before they are added."))
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText(EXAMPLES)
        self.prompt.setMinimumHeight(90)
        self.prompt.setMaximumHeight(130)
        root.addWidget(self.prompt)

        opts = QHBoxLayout()
        self.use_page = ClayToggle(app.debug.is_open)
        self.use_page.setEnabled(app.debug.is_open)
        opts.addWidget(self.use_page)
        opts.addWidget(muted_label("Look at the page open in the test browser (better selectors)" if app.debug.is_open
                                   else "Open the test profile to let the AI see the page", wrap=False))
        opts.addStretch(1)
        root.addLayout(opts)
        place = QHBoxLayout()
        self.after = QRadioButton("Add after the selected step" if has_selection else "Add at the end")
        self.after.setChecked(True)
        self.replace = QRadioButton("Replace all steps")
        place.addWidget(self.after)
        place.addWidget(self.replace)
        place.addStretch(1)
        root.addLayout(place)

        gen_row = QHBoxLayout()
        self.generate_btn = ClayButton("Draft steps", "sparkles", "primary")
        self.generate_btn.clicked.connect(self.generate)
        self.status = muted_label("", wrap=True)
        gen_row.addWidget(self.generate_btn)
        gen_row.addWidget(self.status, 1)
        root.addLayout(gen_row)

        self.preview = QListWidget()
        root.addWidget(self.preview, 1)
        self.warnings = QLabel("")
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet(f"color: {pal().warning};")
        root.addWidget(self.warnings)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = ClayButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.add_btn = ClayButton("Add to flow", "check", "success")
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.add_btn)
        root.addLayout(buttons)

    @property
    def replace_all(self) -> bool:
        return self.replace.isChecked()

    def _api_key(self) -> str:
        from flowpilot.services.ai_builder import resolve_api_key

        stored = self.app.settings.ai_api_key_encrypted
        try:
            plain = self.app.services.secrets.decrypt(stored) if stored else ""
        except Exception:
            plain = ""
        return resolve_api_key(plain)

    def generate(self) -> None:
        request = self.prompt.toPlainText().strip()
        if not request:
            self.status.setText("Describe the task first.")
            return
        key = self._api_key()
        self.generate_btn.setEnabled(False)
        self.add_btn.setEnabled(False)
        self.status.setText("Thinking… (this can take up to a minute)" if key else "No API key: drafting offline…")

        async def go() -> Draft:
            if not key:
                return draft_offline(request)
            outline = ""
            if self.use_page.isChecked() and self.app.debug.is_open and self.app.debug.session is not None:
                try:
                    outline = await page_outline(self.app.debug.session.page)
                except Exception:
                    outline = ""
            existing = [f"{i + 1}. {s.summary()}" for i, s in enumerate(self.existing)]
            return await draft_with_claude(request, key, self.app.settings.ai_model or "claude-opus-5-5",
                                           outline, self.account_vars, existing)

        def done(draft: Draft) -> None:
            self.generate_btn.setEnabled(True)
            self.show_draft(draft)

        def failed(exc: BaseException) -> None:
            self.generate_btn.setEnabled(True)
            msg = str(exc) if isinstance(exc, AiBuilderError) else f"Something went wrong: {exc}"
            self.status.setText("✖ " + msg)

        run_async(go(), done, failed)

    def show_draft(self, draft: Draft) -> None:
        self.draft = draft
        self.preview.clear()
        p = pal()
        for i, step in enumerate(draft.steps, start=1):
            item = QListWidgetItem(f"{i}. {step.TITLE} — {step.summary()}")
            ic = step.ICON if has_icon(step.ICON) else "circle"
            item.setIcon(icon(ic, category_color(p, step.CATEGORY), 18))
            problems = step.problems()
            if problems or step.note:
                item.setToolTip("\n".join(problems + ([step.note] if step.note else [])))
            if problems:
                item.setForeground(p.c("danger"))
            self.preview.addItem(item)
        prefix = "Drafted offline (add an API key in Settings → AI for smarter drafts). " if draft.source == "offline" \
            else ""
        n_open = sum(1 for s in draft.steps if s.problems())
        self.status.setText(f"{prefix}{draft.summary} — {len(draft.steps)} step(s)"
                            + (f", {n_open} need an element (use Pick)." if n_open else "."))
        self.warnings.setText("\n".join(f"⚠ {w}" for w in draft.warnings))
        self.add_btn.setEnabled(bool(draft.steps))

    def steps(self) -> list[Any]:
        return list(self.draft.steps) if self.draft else []


