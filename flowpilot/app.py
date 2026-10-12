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


def smoke_test() -> int:
    """Build the whole UI against a throw-away data folder and exit (checks a packaged build)."""
    import tempfile
    from pathlib import Path

    from PySide6.QtWidgets import QApplication

    from flowpilot import __version__
    from flowpilot.services.container import Services

    app = QApplication.instance() or QApplication(sys.argv)
    services = Services.create(Path(tempfile.mkdtemp(prefix="flowpilot-smoke-")))
    services.settings.settings.setup_completed = True
    from flowpilot.core.recorder import RECORDER_JS
    from flowpilot.ui.app_context import AppContext
    from flowpilot.ui.main_window import MainWindow
    from flowpilot.ui.theme.palette import ThemeManager

    ThemeManager.instance().apply("light")
    window = MainWindow(AppContext.create(services))
    window.show()
    for i in range(len(window.pages)):
        window.show_page(i)
        app.processEvents()
    import anthropic  # the AI step builder's SDK must be bundled

    message = (f"FlowPilot {__version__} smoke test OK ({len(window.pages)} pages, recorder script "
               f"{len(RECORDER_JS)} bytes, anthropic {anthropic.__version__})")
    if "--with-browser" in sys.argv:
        message += "; " + asyncio.run(_browser_check(services))
    services.db.dispose()
    print(message)
    if "--smoke-report" in sys.argv:  # the windowed exe has no console; CI reads this file
        idx = sys.argv.index("--smoke-report")
        if idx + 1 < len(sys.argv):
            Path(sys.argv[idx + 1]).write_text(message, "utf-8")
    return 0


async def _browser_check(services) -> str:  # noqa: ANN001
    """Run a two-step flow on a local Chrome/Edge profile through the real engine."""
    import json

    from flowpilot.core.engine import RunOptions, TargetSpec
    from flowpilot.core.steps import dump_steps, new_step
    from flowpilot.storage.models import Account, Connection

    repos = services.repos
    conn = repos.connections.save(Connection(type="local", name="Local", extra_json=json.dumps(
        {"headless": "1", "profiles_dir": str(services.paths.root / "smoke-profiles")})))
    repos.accounts.save(Account(name="smoke", connection_id=conn.id, remote_profile_id="smoke"))
    steps = [new_step("open_url", url="data:text/html,<h1>FlowPilot</h1>"),
             new_step("extract", selector="h1", variable="title"), new_step("close_browser")]
    flow = repos.flows.create("smoke", json.dumps(dump_steps(steps)))
    services.settings.settings.resource_guard = False
    run_id = await services.engine.start(flow, TargetSpec(), RunOptions(launch_delay_min=0, launch_delay_max=0))
    while run_id in services.engine.runs:
        await asyncio.sleep(0.2)
    item = repos.runs.items(run_id)[0]
    await services.engine.shutdown()
    await services.adapters.aclose()
    if item.status != "done":
        raise SystemExit(f"Browser check failed: {item.error}")
    return f"browser check OK ({json.loads(item.results_json)['vars']['title']})"


def main() -> int:
    if "--smoke-test" in sys.argv:
        return smoke_test()
    if "--version" in sys.argv:
        from flowpilot import __version__

        print(__version__)
        return 0

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
