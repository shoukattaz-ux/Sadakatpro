"""Launcher used by PyInstaller (and handy for development: ``python run_flowpilot.py``)."""

import sys

from flowpilot.app import main

if __name__ == "__main__":
    sys.exit(main())
