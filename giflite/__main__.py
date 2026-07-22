"""Entry point: python -m giflite [file]

No --ui switch. There is one frontend; a switch would be ceremony around a
choice that doesn't exist yet (ARCHITECTURE.md 9).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from giflite import __version__
from giflite.app.controller import AppController


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="giflite", description="A small, modular GIF editor."
    )
    parser.add_argument("file", nargs="?", type=Path, help="animation to open")
    parser.add_argument("--version", action="version", version=f"giflite {__version__}")
    args = parser.parse_args(argv)

    controller = AppController()

    # Imported here rather than at module scope so that a headless import of
    # giflite never pulls in tkinter, and so a missing Tk gives a clear
    # message instead of a traceback at startup.
    try:
        from giflite.ui.tk.app import TkFrontend
    except ImportError as exc:
        print(
            f"Could not start the Tk frontend: {exc}\n"
            "On Linux this usually means the python3-tk package is missing.",
            file=sys.stderr,
        )
        return 1

    TkFrontend().run(controller, initial_path=args.file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
