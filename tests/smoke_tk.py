"""Manual smoke test for the Tk frontend (M0 + M1).

Not part of the pytest run: it needs a display, and CI (and the dev sandbox)
is headless. Run it directly when changing the UI layer.

    python tests/smoke_tk.py [--shot out.png]

On Linux with Xvfb:
    Xvfb :99 -screen 0 1100x820x24 & DISPLAY=:99 python tests/smoke_tk.py
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
    gif = make_gif(tmp / "smoke.gif", frames=8, size=(160, 80),
                   durations=[100, 100, 250, 100, 5, 500, 100, 100])

    root = tk.Tk()
    controller = AppController()
    window = MainWindow(root, controller)
    root.update()

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    # --- empty state ---------------------------------------------------
    check("empty: placeholder drawn", len(window.canvas.find_all()) == 1)
    check("empty: no photo retained", window.canvas._photo is None)
    check("empty: play button disabled", str(window.play_button["state"]) == "disabled")
    check("empty: title has no filename", root.title() == "GIF Editor Lite", root.title())

    # --- open ----------------------------------------------------------
    window.open_path(gif)
    root.update()

    check("open: 8 frames loaded", controller.frame_count == 8, str(controller.frame_count))
    canvas_images = [i for i in window.canvas.find_all()
                     if window.canvas.type(i) == "image"]
    check("open: canvas shows exactly one image", len(canvas_images) == 1,
          f"{len(canvas_images)} image items")
    check("open: canvas boundary drawn",
          any(window.canvas.type(i) == "rectangle" for i in window.canvas.find_all()))
    check("open: PhotoImage retained (blank-canvas bug)", window.canvas._photo is not None)
    check("open: image scaled up to fit",
          window.canvas._photo is not None and window.canvas._photo.width() > 160,
          f"{window.canvas._photo.width()}px from 160px source")
    check("open: title shows filename", root.title().startswith("smoke.gif"), root.title())
    check("open: play button enabled", str(window.play_button["state"]) == "normal")
    check("open: counter shows frame 1 of 8", "Frame 1 of 8" in window.counter.cget("text"),
          window.counter.cget("text"))

    # --- timeline ------------------------------------------------------
    tl_items = window.timeline.canvas.find_all()
    tl_images = [i for i in tl_items if window.timeline.canvas.type(i) == "image"]
    check("timeline: thumbnails drawn", len(tl_images) >= 8, f"{len(tl_images)} thumbs")
    check("timeline: a highlight border exists",
          any(window.timeline.canvas.type(i) == "rectangle" for i in tl_items))

    # --- playback (drive the clock directly for determinism) -----------
    controller.play()
    root.update()
    check("play: button now says Pause", window.play_button.cget("text") == "Pause",
          window.play_button.cget("text"))
    check("play: controller is playing", controller.playing)

    controller.tick(120)  # 120ms >= frame 0's 100ms -> frame 1
    root.update()
    check("play: playhead advanced", controller.index == 1, f"index={controller.index}")
    check("play: canvas followed the playhead", window.canvas._photo is not None)
    check("play: counter followed", "Frame 2 of 8" in window.counter.cget("text"),
          window.counter.cget("text"))

    controller.pause()
    root.update()
    check("pause: button says Play again", window.play_button.cget("text") == "Play")
    check("pause: controller stopped", not controller.playing)

    # --- scrub via timeline click --------------------------------------
    window._on_pick(5)
    root.update()
    check("click: playhead jumped to picked frame", controller.index == 5,
          f"index={controller.index}")
    check("click: frame selected", 5 in controller.selection.indices)

    # --- keyboard stepping ---------------------------------------------
    window.root.event_generate("<Left>")
    root.update()
    check("Left: stepped back one frame", controller.index == 4, f"index={controller.index}")

    # --- speed ----------------------------------------------------------
    window.speed.set("2x")
    window._on_speed(None)
    check("speed: 2x applied to controller", abs(controller.speed - 2.0) < 1e-6,
          str(controller.speed))

    # --- resize refits --------------------------------------------------
    before = window.canvas._photo.width()
    root.geometry("560x460")
    root.update()
    check("resize: image refit", window.canvas._photo.width() != before,
          f"{before}px -> {window.canvas._photo.width()}px")

    root.geometry("900x680")
    root.update()

    if args.shot:
        try:
            from PIL import ImageGrab

            controller.seek(5)  # a frame with the dot mid-strip, for a nicer shot
            root.update()
            ImageGrab.grab(xdisplay=os.environ.get("DISPLAY", ":99")).save(args.shot)
            print(f"screenshot -> {args.shot}")
        except Exception as exc:  # noqa: BLE001
            print(f"screenshot unavailable: {exc}")

    # --- close ----------------------------------------------------------
    controller.close()
    root.update()
    check("close: back to placeholder",
          window.canvas._photo is None and len(window.canvas.find_all()) == 1)
    check("close: timeline cleared", len(window.timeline.canvas.find_all()) == 0)
    check("close: play button disabled again", str(window.play_button["state"]) == "disabled")

    root.destroy()

    failed = sum(1 for _, ok, _ in checks if not ok)
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail else ""
        print(f"[{mark}] {name}{suffix}")
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
