# PyInstaller spec for FlowPilot (one-folder Windows build).
# Build with:  pyinstaller flowpilot.spec --noconfirm
# Output:      dist/FlowPilot/FlowPilot.exe
# Playwright's driver is bundled by the hook from pyinstaller-hooks-contrib; no browsers are
# bundled because FlowPilot attaches to the anti-detect browser over CDP.

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

datas = [
    ("flowpilot/resources", "flowpilot/resources"),
    ("flowpilot/core/recorder.js", "flowpilot/core"),
]
datas += copy_metadata("APScheduler")
datas += copy_metadata("playwright")

hiddenimports = (
    collect_submodules("apscheduler")
    + collect_submodules("flowpilot")
    + ["keyring.backends.Windows", "qasync", "PySide6.QtSvg", "anthropic"]
)

a = Analysis(
    ["run_flowpilot.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "numpy", "PIL", "lxml", "pandas", "matplotlib", "IPython", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FlowPilot",
    icon="flowpilot/resources/icons/flowpilot.ico",
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="FlowPilot")
