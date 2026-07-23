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
    window._pick(5)
    root.update()
    check("click: playhead jumped to picked frame", controller.index == 5,
          f"index={controller.index}")
    check("click: frame selected", 5 in controller.selection.indices)

    # --- shift / ctrl selection ----------------------------------------
    window._pick(2)
    window._extend(4)
    check("shift-extend: range selected", controller.selection.ordered == (2, 3, 4),
          str(controller.selection.ordered))
    window._toggle(6)
    check("ctrl-toggle: added a frame", controller.selection.ordered == (2, 3, 4, 6),
          str(controller.selection.ordered))
    window._select_all()
    check("select all: everything selected", len(controller.selection) == 8)

    # --- keyboard stepping ---------------------------------------------
    window._pick(5)
    window.root.event_generate("<Left>")
    root.update()
    check("Left: stepped back one frame", controller.index == 4, f"index={controller.index}")

    # --- editing: delete, then undo/redo -------------------------------
    window._pick(1)
    window._extend(2)  # select frames 2 and 3 (indices 1,2)
    controller.run_op("frames.delete")
    root.update()
    check("delete: two frames removed", controller.frame_count == 6,
          f"count={controller.frame_count}")
    check("delete: made the doc dirty", controller.dirty)
    check("delete: title shows unsaved marker", window.root.title().startswith("*"),
          window.root.title())
    tl_after_delete = len([i for i in window.timeline.canvas.find_all()
                           if window.timeline.canvas.type(i) == "image"])
    check("delete: timeline redrew fewer thumbs", tl_after_delete == 6,
          f"{tl_after_delete} thumbs")

    controller.undo()
    root.update()
    check("undo: frames restored", controller.frame_count == 8)
    check("undo: back to clean", not controller.dirty)
    check("undo: title marker cleared", not window.root.title().startswith("*"))

    controller.redo()
    root.update()
    check("redo: delete reapplied", controller.frame_count == 6)

    # --- editing: duplicate --------------------------------------------
    window._pick(0)
    controller.run_op("frames.duplicate")
    root.update()
    check("duplicate: one frame added", controller.frame_count == 7)
    check("duplicate: selection on the new copy", controller.selection.ordered == (1,))

    # --- editing: reorder via the move op (drag's commit) --------------
    window._pick(0)
    controller.run_op("frames.move", to=controller.frame_count)  # send to end
    root.update()
    check("move: selection followed to the end",
          controller.selection.ordered == (controller.frame_count - 1,),
          str(controller.selection.ordered))

    controller.undo()  # undo move
    controller.undo()  # undo duplicate
    controller.undo()  # undo delete
    root.update()
    check("undo x3: back to the original 8 frames", controller.frame_count == 8,
          f"count={controller.frame_count}")
    check("undo x3: clean again", not controller.dirty)

    # --- drag-to-reorder via the timeline's real mouse path ------------
    # Exercises _on_press/_on_motion/_on_release + _index_at/_gap_at, not just
    # the move op underneath. Fake events carry the widget-x the handlers read.
    class _Event:
        def __init__(self, x):
            self.x = x
            self.state = 0

    tl = window.timeline
    window._pick(0)
    root.update()
    press_x = 6 + tl._slot_w // 2          # centre of frame 0
    drop_x = 6 + 3 * tl._slot_w            # gap before original index 3
    check("gesture: press lands on frame 0", tl._index_at(press_x) == 0,
          f"index_at={tl._index_at(press_x)}")
    tl._on_press(_Event(press_x))
    tl._on_motion(_Event(drop_x))
    check("gesture: motion past threshold starts a drag", tl._dragging)
    check("gesture: drop gap computed", tl._drop_gap == 3, f"gap={tl._drop_gap}")
    tl._on_release(_Event(drop_x))
    root.update()
    check("gesture: frame 0 moved to index 2", controller.selection.ordered == (2,),
          str(controller.selection.ordered))
    check("gesture: recorded as an undoable edit", controller.can_undo)
    controller.undo()
    root.update()

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

            # Show a multi-frame selection so the shot demonstrates editing.
            window._pick(2)
            window._extend(4)
            controller.seek(3)
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
