"""Executable version of the modularity rule.

ARCHITECTURE.md 2.2: only `giflite/ui/tk/` may import a UI toolkit. A rule
that isn't checked decays quietly, so it lives in the test suite rather than
in prose alone.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "giflite"
ALLOWED_PREFIX = PACKAGE_ROOT / "ui" / "tk"

# ImageTk is in this pattern deliberately: it pulls in tkinter, so a
# PhotoImage cache under app/ would bind the app layer to the toolkit while a
# naive "import tkinter" search stayed green.
TOOLKIT_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(tkinter|PySide6|PyQt5|PyQt6|dearpygui|pygame)"
    r"|^\s*from\s+PIL\s+import\s+.*\bImageTk\b",
    re.MULTILINE,
)


def _offending_files() -> list[tuple[Path, str]]:
    offenders = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        if ALLOWED_PREFIX in path.parents:
            continue
        match = TOOLKIT_IMPORT.search(path.read_text(encoding="utf-8"))
        if match:
            offenders.append((path.relative_to(PACKAGE_ROOT.parent), match.group(0).strip()))
    return offenders


def test_only_the_tk_frontend_imports_a_toolkit():
    offenders = _offending_files()
    assert not offenders, "toolkit imports outside giflite/ui/tk: " + "; ".join(
        f"{path} ({line})" for path, line in offenders
    )


def test_importing_the_core_does_not_pull_in_tkinter():
    """The runtime half of the same rule.

    Static checks miss lazy imports, so this actually imports the library in a
    clean interpreter and asserts tkinter never reached sys.modules. It is
    also what keeps the core usable as a headless library.
    """
    code = (
        "import sys; "
        "import giflite.app.controller, giflite.core.io, giflite.core.model; "
        "assert 'tkinter' not in sys.modules, sorted(m for m in sys.modules if 'tk' in m.lower())"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PACKAGE_ROOT.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
