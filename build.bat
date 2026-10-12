@echo off
REM Builds dist\FlowPilot\FlowPilot.exe (needs Python 3.11+ installed and on PATH).
setlocal
if not exist .venv (
    python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
pip install -r requirements.txt pyinstaller || goto :error
pyinstaller flowpilot.spec --noconfirm || goto :error
dist\FlowPilot\FlowPilot.exe --smoke-test --with-browser
echo.
echo Done: dist\FlowPilot\FlowPilot.exe
goto :eof
:error
echo Build failed.
exit /b 1
