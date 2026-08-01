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


def test_no_frozen_dataclass_carries_an_unhashable_default():
    """`pyproject` says >=3.10, and 3.11 changed what a legal default is.

    Until 3.11, dataclasses rejected a mutable default by asking "is it a list,
    dict or set". 3.11 replaced that with "is its type unhashable", which is a
    better question and a wider net: `MappingProxyType({})` is *immutable* and
    perfectly safe to share, and it is caught anyway. `Document.meta` used one,
    so the class body raised ValueError at import and the entire package failed
    to import on 3.11, 3.12 and 3.13 -- not a subtle degradation, a hard stop
    for anyone whose `python` was newer than the box it was written on.

    A test that just imports giflite would catch it on a new interpreter and be
    silent on an old one, which is backwards: the developer on 3.10 is the one
    who needs telling. So the rule is restated here, where it fails everywhere.
    """
    import dataclasses
    import importlib
    import pkgutil

    import giflite

    offenders = []
    for info in pkgutil.walk_packages(giflite.__path__, "giflite."):
        # ui.tk is the one place allowed to import a toolkit, and importing it
        # here would need a display. The rule still holds there; nothing in it
        # is a dataclass, and the check above already fences that package off.
        if info.name.startswith("giflite.ui.tk"):
            continue
        module = importlib.import_module(info.name)
        for name, obj in vars(module).items():
            if not dataclasses.is_dataclass(obj) or not isinstance(obj, type):
                continue
            # Report each class once, where it is defined -- `Document` is
            # imported into a dozen modules and would otherwise fill the
            # failure message with the same line a dozen times.
            if obj.__module__ != info.name:
                continue
            for field in dataclasses.fields(obj):
                default = field.default
                if default is dataclasses.MISSING:
                    continue
                if type(default).__hash__ is None:
                    offenders.append(
                        f"{info.name}.{name}.{field.name} = {type(default).__name__}")
    assert not offenders, (
        "unhashable dataclass defaults (illegal on Python 3.11+; use "
        "field(default_factory=...)): " + "; ".join(offenders))
