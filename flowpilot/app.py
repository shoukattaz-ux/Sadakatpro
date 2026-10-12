"""FlowPilot entry point: logging, Qt + asyncio (qasync) loop, services and the main window."""

from __future__ import annotations

import asyncio
import os
import sys

from loguru import logger


def setup_logging(log_dir) -> None:  # noqa: ANN001
    logger.remove()
    if sys.stderr is not None:  # windowed PyInstaller builds have no console
        logger.add(sys.stderr, level=os.environ.get("FLOWPILOT_LOG_LEVEL", "INFO"))
    logger.add(log_dir / "flowpilot_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days", level="DEBUG",
               encoding="utf-8", enqueue=False, backtrace=False, diagnose=False)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    import qasync

    from flowpilot import APP_NAME, __version__
    from flowpilot.services.container import Services
    from flowpilot.services.reporting import apply_retention

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    qt_app = QApplication.instance() or QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    loop = qasync.QEventLoop(qt_app)
    asyncio.set_event_loop(loop)

    services = Services.create()
    setup_logging(services.paths.logs)
    logger.info("FlowPilot {} starting, data in {}", __version__, services.paths.root)

    from flowpilot.ui.app_context import AppContext
    from flowpilot.ui.main_window import MainWindow
    from flowpilot.ui.theme.palette import ThemeManager

    s = services.settings.settings
    ThemeManager.instance().apply(s.theme, s.font_size)
    ctx = AppContext.create(services)
    window = MainWindow(ctx)
    try:
        apply_retention(services.repos, services.paths, s.log_retention_days)
    except Exception as exc:  # never block start-up on clean-up
        logger.warning("Retention clean-up failed: {}", exc)

    quit_event = asyncio.Event()
    qt_app.aboutToQuit.connect(quit_event.set)

    async def run() -> None:
        from PySide6.QtCore import QTimer

        services.scheduler.start()
        window.show()
        # dialogs open from a Qt slot, not from inside this coroutine
        QTimer.singleShot(300, window.first_launch)
        await quit_event.wait()
        logger.info("Shutting down…")
        await ctx.debug.close()
        await services.aclose()

    with loop:
        loop.run_until_complete(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
