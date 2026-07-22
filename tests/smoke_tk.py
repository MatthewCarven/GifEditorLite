"""Manual smoke test for the Tk frontend.

Not part of the pytest run: it needs a display, and CI (and my sandbox) is
headless by default. Run it directly when changing the UI layer.

    python tests/smoke_tk.py [--shot out.png]

On Linux with Xvfb:
    Xvfb :99 -screen 0 1100x760x24 & DISPLAY=:99 python tests/smoke_tk.py
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from giflite.app.controller import AppController  # noqa: E402
from giflite.ui.tk.app import MainWindow  # noqa: E402
from tests.conftest import make_gif  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shot", type=Path, help="save a screenshot here")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp())
    gif = make_gif(tmp / "smoke.gif", frames=6, size=(160, 80),
                   durations=[100, 100, 250, 100, 5, 500])

    root = tk.Tk()
    controller = AppController()
    window = MainWindow(root, controller)
    root.update()

    checks: list[tuple[str, bool, str]] = []

    # --- empty state ---------------------------------------------------
    items = window.canvas.find_all()
    checks.append(("empty state draws a placeholder", len(items) == 1, f"{len(items)} items"))
    checks.append((
        "empty state has no photo retained",
        window.canvas._photo is None,
        "",
    ))
    checks.append((
        "empty state title has no filename",
        root.title() == "GIF Editor Lite",
        root.title(),
    ))

    # --- open ----------------------------------------------------------
    window.open_path(gif)
    root.update()

    checks.append(("document loaded", controller.frame_count == 6, str(controller.frame_count)))
    checks.append((
        "canvas draws exactly one image",
        len(window.canvas.find_all()) == 1
        and window.canvas.type(window.canvas.find_all()[0]) == "image",
        str([window.canvas.type(i) for i in window.canvas.find_all()]),
    ))
    checks.append((
        "PhotoImage reference retained (blank-canvas bug)",
        window.canvas._photo is not None,
        "",
    ))
    checks.append((
        "image was scaled up to fit the window",
        window.canvas._photo is not None and window.canvas._photo.width() > 160,
        f"{window.canvas._photo.width()}px wide from a 160px source",
    ))
    checks.append((
        "title shows the filename",
        root.title().startswith("smoke.gif"),
        root.title(),
    ))
    checks.append((
        "status bar summarises the document",
        "Frame 1 of 6" in window.status.cget("text"),
        window.status.cget("text"),
    ))

    # --- resize redraws -------------------------------------------------
    before = window.canvas._photo.width()
    root.geometry("500x400")
    root.update()
    checks.append((
        "resize refits the image",
        window.canvas._photo.width() != before,
        f"{before}px -> {window.canvas._photo.width()}px",
    ))

    # --- seek ------------------------------------------------------------
    controller.seek(3)
    root.update()
    checks.append((
        "seek updates the status bar",
        "Frame 4 of 6" in window.status.cget("text"),
        window.status.cget("text"),
    ))

    # --- failure path ----------------------------------------------------
    root.geometry("900x620")
    root.update()

    if args.shot:
        try:
            from PIL import ImageGrab

            grab = ImageGrab.grab(xdisplay=os.environ.get("DISPLAY", ":99"))
            grab.save(args.shot)
            print(f"screenshot -> {args.shot}")
        except Exception as exc:  # noqa: BLE001
            print(f"screenshot unavailable: {exc}")

    # --- close ------------------------------------------------------------
    controller.close()
    root.update()
    checks.append((
        "close returns to the placeholder",
        window.canvas._photo is None and len(window.canvas.find_all()) == 1,
        "",
    ))

    root.destroy()

    failed = 0
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        suffix = f"  ({detail})" if detail else ""
        print(f"[{mark}] {name}{suffix}")
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
