"""Print the CHANGELOG.md section of one version (used for the GitHub Release text)."""

import re
import sys
from pathlib import Path


def notes(version: str, changelog: str) -> str:
    pattern = re.compile(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", re.M | re.S)
    match = pattern.search(changelog)
    return match.group(1).strip() if match else f"FlowPilot {version}"


if __name__ == "__main__":
    version = sys.argv[1].lstrip("v")
    body = notes(version, Path("CHANGELOG.md").read_text("utf-8"))
    body += ("\n\n---\n**Install:** download `FlowPilot-Setup.exe` below and run it (no admin rights needed).\n"
             "Already installed? FlowPilot updates itself — or use *Settings → About → Check for updates*.\n")
    Path(sys.argv[2] if len(sys.argv) > 2 else "release-notes.md").write_text(body, "utf-8")
