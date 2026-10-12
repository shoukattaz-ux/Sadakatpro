"""Run coroutines from Qt slots (qasync shares one event loop between Qt and asyncio)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from loguru import logger
from PySide6.QtWidgets import QMessageBox, QWidget

from flowpilot.adapters.base import AdapterError
from flowpilot.core.errors import FlowError, friendly_error

_tasks: set[asyncio.Task[Any]] = set()


def run_async(coro: Coroutine[Any, Any, Any], on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[BaseException], None] | None = None, parent: QWidget | None = None,
              error_title: str = "Something went wrong") -> asyncio.Task[Any]:
    """Schedule ``coro``; deliver the result to ``on_done``. Errors are shown in plain
    language (never a raw traceback) unless ``on_error`` handles them."""
    task = asyncio.ensure_future(coro)
    _tasks.add(task)

    def done(t: asyncio.Task[Any]) -> None:
        _tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            if on_error is not None:
                on_error(exc)
                return
            if not isinstance(exc, (FlowError, AdapterError, ValueError)):
                logger.opt(exception=exc).error("Background task failed")
            show_error(parent, error_title, exc)
            return
        if on_done is not None:
            try:
                on_done(t.result())
            except Exception as cb_exc:  # pragma: no cover
                logger.exception("Callback failed: {}", cb_exc)

    task.add_done_callback(done)
    return task


def show_error(parent: QWidget | None, title: str, exc: BaseException | str) -> None:
    message = friendly_error(exc) if isinstance(exc, BaseException) else exc
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    if isinstance(exc, BaseException):
        detail = getattr(exc, "detail", "") or f"{type(exc).__name__}: {exc}"
        box.setDetailedText(str(detail)[:4000])
    box.exec()


def confirm(parent: QWidget | None, title: str, text: str, ok_text: str = "Yes", danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is yes


def info(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)
