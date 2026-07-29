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
from giflite.core.model import Region, Selection  # noqa: E402
from giflite.ui.tk import canvas as canvas_module  # noqa: E402
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

    # --- M4: param dialog drives an op ---------------------------------
    from giflite.ui.tk.dialogs import ParamDialog  # noqa: E402
    from giflite.core.ops import get_op, op_defaults  # noqa: E402
    resize = get_op("canvas.resize")
    dlg = ParamDialog(root, "Resize", resize.params,
                      op_defaults(resize, controller.doc, controller.selection))
    root.update()
    check("dialog: seeded with current width", dlg._getters["width"]() == str(controller.doc.size[0]),
          f'{dlg._getters["width"]()} vs {controller.doc.size[0]}')
    # drive it: turn off keep-aspect, set a new width, OK
    for name, getter in dlg._getters.items():
        pass  # (getters are read-only; set via the underlying vars below)
    dlg.destroy()
    # apply resize straight through the controller (the dialog's coerced output)
    before_size = controller.doc.size
    controller.run_op("canvas.resize", width=before_size[0] * 2, height=1, keep_aspect=True)
    root.update()
    check("resize op: canvas widened, aspect kept",
          controller.doc.size[0] == before_size[0] * 2, str(controller.doc.size))
    check("resize op: preview refit to new canvas", window.canvas._photo is not None)

    # --- M4: a no-param op via the menu invoke path --------------------
    # (An op WITH params, like flip, would open a modal dialog here and block a
    # scripted run -- so exercise a genuinely param-free op: reverse.)
    n_before = controller.frame_count
    window._invoke_op("frames.reverse")
    root.update()
    check("invoke_op: param-free op ran without a dialog",
          controller.frame_count == n_before and controller.can_undo)

    # --- M4: ping-pong toggle ------------------------------------------
    window.pingpong_var.set(True)
    window._on_pingpong()
    check("pingpong: controller reflects the toggle", controller.pingpong)
    window.pingpong_var.set(False)
    window._on_pingpong()
    check("pingpong: toggles back off", not controller.pingpong)

    # Fake mouse events carrying widget x/y, shared by every gesture check below.
    class _XY:
        def __init__(self, x, y):
            self.x = x
            self.y = y
            self.state = 0

    # --- the mapping is anchored to where Tk actually drew the image -----
    # This checks _image_geom against ground truth (the image item's bbox) and
    # feeds _display_to_image real *widget* coordinates. The gesture checks below
    # can't do that job: they derive their click points from _image_to_display, so
    # a wrong origin stays self-consistent and passes. That hole hid a live bug --
    # a scrolled canvas offset every stroke up and to the left.
    root.update()
    img_item = [i for i in window.canvas.find_all()
                if window.canvas.type(i) == "image"][0]
    bbox = window.canvas.bbox(img_item)
    geom = window.canvas._image_geom
    check("mapping: _image_geom matches the drawn image's bbox",
          bbox[:2] == geom[:2], f"bbox {bbox} vs geom {geom}")

    # Widget points computed by hand -- no help from _image_to_display, which is
    # the point: the two functions agreeing with each other proves nothing.
    left, top, fw, fh = geom
    src_w, src_h = controller.doc.size
    sx, sy = fw / src_w, fh / src_h
    hand_x = left + round((src_w // 2 + 0.5) * sx)   # the pixel's visible middle
    hand_y = top + round((src_h // 2 + 0.5) * sy)
    check("mapping: a hand-computed widget point maps to the centre pixel",
          window.canvas._display_to_image(hand_x, hand_y) == (src_w // 2, src_h // 2),
          str(window.canvas._display_to_image(hand_x, hand_y)))

    # The regression Matthew hit: clicking the visible centre of a pixel used to
    # paint its right-hand neighbour, because the mapping rounded instead of
    # flooring. Invisible at 1:1 zoom, a whole pixel off at 30x.
    wrong_centres = [(px, py, window.canvas._display_to_image(
                          left + (px + 0.5) * sx, top + (py + 0.5) * sy))
                     for px in range(0, src_w, 7) for py in range(0, src_h, 5)
                     if window.canvas._display_to_image(
                          left + (px + 0.5) * sx, top + (py + 0.5) * sy) != (px, py)]
    check("mapping: every pixel centre maps to its own pixel",
          not wrong_centres, str(wrong_centres[:3]))

    # ...and every point *within* a pixel, not just its centre.
    strays = [(px, frac) for px in range(0, src_w, 7) for frac in (0.02, 0.5, 0.98)
              if window.canvas._display_to_image(
                     left + (px + frac) * sx, top + 2.5 * sy)[0] != px]
    check("mapping: any point inside a pixel maps to that pixel",
          not strays, str(strays[:3]))

    # Crop must NOT share that rule: its coordinates are edges between pixels, so
    # it snaps to the nearest boundary and spans 0..src inclusive.
    check("mapping: crop still snaps to pixel boundaries",
          window.canvas._display_to_image(left, top, snap="edge") == (0, 0)
          and window.canvas._display_to_image(left + fw, top + fh, snap="edge")
              == (src_w, src_h),
          str(window.canvas._display_to_image(left + fw, top + fh, snap="edge")))
    check("mapping: crop tool asks for edge coordinates, brushes for pixels",
          window._tools["crop"].coords == "edge"
          and window._tools["pencil"].coords == "pixel")

    # The preview overlay must sit on the pixel, not on its top-left corner.
    # Checked against the *drawn item's* bbox, not by re-deriving the number --
    # re-deriving it would pass even if show_stroke_overlay ignored the centring.
    px, py = src_w // 2, src_h // 2
    window.canvas.show_stroke_overlay([(px, py)], "#ff0000", 1, False)
    ov = window.canvas._overlay_items
    ob = window.canvas.bbox(ov[0]) if ov else None
    # Comparative, not a tolerance: is the drawn item nearer the pixel's centre
    # than its top-left corner? That holds at any zoom, whereas an absolute
    # tolerance passes by luck when the fit scale is small (half a pixel is only
    # ~1.4 screen px here, but ~15 on the blown-up pixel art this bug showed up on).
    centre = (left + (px + 0.5) * sx, top + (py + 0.5) * sy)
    corner = (left + px * sx, top + py * sy)
    item = ((ob[0] + ob[2]) / 2, (ob[1] + ob[3]) / 2) if ob else None
    check("mapping: stroke preview is centred on the pixel, not its corner",
          item is not None
          and abs(item[0] - centre[0]) < abs(item[0] - corner[0])
          and abs(item[1] - centre[1]) < abs(item[1] - corner[1]),
          f"item {item} | centre {tuple(round(v, 1) for v in centre)} "
          f"| corner {tuple(round(v, 1) for v in corner)}")
    window.canvas.clear_overlay()

    # The regression itself: a tk.Canvas with no scrollregion will scroll over its
    # items' bounding box, and then widget != canvas coordinates.
    window.canvas.yview_scroll(3, "units")
    window.canvas.xview_scroll(2, "units")
    root.update()
    check("mapping: the preview refuses to scroll away from the origin",
          window.canvas.canvasx(0) == 0 and window.canvas.canvasy(0) == 0,
          f"canvasx={window.canvas.canvasx(0)} canvasy={window.canvas.canvasy(0)}")

    # And belt-and-braces: even with the view forced off the origin, the dispatch
    # converts widget -> canvas coordinates, so a press still lands on the pixel
    # under the cursor.
    window.canvas.configure(scrollregion=(-500, -500, 2000, 2000))
    window.canvas.xview_moveto(0.3)
    window.canvas.yview_moveto(0.3)
    root.update()
    off_x, off_y = window.canvas.canvasx(0), window.canvas.canvasy(0)
    check("mapping: view forced off the origin for the test", off_x or off_y,
          f"({off_x}, {off_y})")
    window._set_fg_color((0, 255, 0, 255))
    window._brush_size = 1
    window._select_tool("pencil")
    tx, ty = src_w // 2, src_h // 2
    window.canvas._on_press(_XY(int(hand_x - off_x), int(hand_y - off_y)))
    window.canvas._on_release(_XY(int(hand_x - off_x), int(hand_y - off_y)))
    root.update()
    hit = controller.doc[controller.index].image.getpixel((tx, ty))
    check("mapping: a scrolled view still paints under the cursor",
          hit == (0, 255, 0, 255), f"{hit} at {(tx, ty)}")
    controller.undo()
    window._select_tool("cursor")
    root.update()  # _redraw re-pins the scrollregion and restores the origin
    check("mapping: redraw restored the origin",
          window.canvas.canvasx(0) == 0 and window.canvas.canvasy(0) == 0)

    # --- crop as a tool: one dispatch path, same rubber-band ------------
    # Crop used to be a bespoke mode on the canvas; it is now a Tool like the
    # paint ones, so this drives the *shared* _on_press/_on_drag/_on_release with
    # fake widget events, exercising the display->image mapping and the tool.
    root.update()
    before_crop_size = controller.doc.size
    window._select_tool("crop")
    check("crop: tool active", window.canvas.has_tool)
    check("crop: palette shows crop selected", window._tool_var.get() == "crop",
          window._tool_var.get())
    check("crop: selecting the tool paused playback", not controller.playing)
    geom = window.canvas._image_geom
    check("crop: image geometry known", geom is not None, str(geom))
    left, top, fw, fh = geom
    # Drag the central half of the image -> crop to roughly half in each axis.
    window.canvas._on_press(_XY(left + fw // 4, top + fh // 4))
    window.canvas._on_drag(_XY(left + (fw * 3) // 4, top + (fh * 3) // 4))
    check("crop: marquee drawn during drag", len(window.canvas._overlay_items) >= 1,
          f"{len(window.canvas._overlay_items)} items")
    check("crop: gesture in progress", window.canvas.active_tool.is_gesturing)
    window.canvas._on_release(_XY(left + (fw * 3) // 4, top + (fh * 3) // 4))
    root.update()
    check("crop: gesture finished on release", not window.canvas.active_tool.is_gesturing)
    check("crop: overlay cleared on commit", len(window.canvas._overlay_items) == 0)
    cw, ch = controller.doc.size
    check("crop: canvas shrank in both dimensions",
          cw < before_crop_size[0] and ch < before_crop_size[1],
          f"{before_crop_size} -> {(cw, ch)}")
    check("crop: every frame matches the new canvas",
          all(f.image.size == (cw, ch) for f in controller.doc.frames))
    check("crop: preview refit to the cropped canvas", window.canvas._photo is not None)
    check("crop: recorded as an undoable edit", controller.can_undo)
    check("crop: made the doc dirty", controller.dirty)
    controller.undo()
    root.update()
    check("crop: undo restored the canvas size",
          controller.doc.size == before_crop_size,
          f"{controller.doc.size} vs {before_crop_size}")

    # --- two-stage Esc: abandon the gesture, then put the tool away -----
    size_before_cancel = controller.doc.size
    geom = window.canvas._image_geom
    left, top, fw, fh = geom
    window._select_tool("crop")
    window.canvas._on_press(_XY(left + fw // 4, top + fh // 4))
    window.canvas._on_drag(_XY(left + fw // 2, top + fh // 2))
    handled = window.canvas._on_escape()
    root.update()
    check("crop: first Esc was consumed by the canvas", handled == "break", str(handled))
    check("crop: first Esc kept the tool", window.canvas.has_tool)
    check("crop: first Esc cleared the marquee", len(window.canvas._overlay_items) == 0)
    check("crop: first Esc changed nothing", controller.doc.size == size_before_cancel,
          str(controller.doc.size))
    window.canvas._on_escape()
    check("crop: second Esc put the tool away", not window.canvas.has_tool)
    check("crop: palette back to Cursor", window._tool_var.get() == "cursor",
          window._tool_var.get())
    check("crop: Esc with no tool defers to the global binding",
          window.canvas._on_escape() is None)

    # --- a stray click in crop mode commits nothing ---------------------
    window._select_tool("crop")
    size_before_click = controller.doc.size
    undo_before_click = controller.undo_label
    window.canvas._on_press(_XY(left + fw // 2, top + fh // 2))
    window.canvas._on_release(_XY(left + fw // 2, top + fh // 2))
    root.update()
    check("crop: a click (zero area) changed nothing",
          controller.doc.size == size_before_click)
    check("crop: a click added nothing to the undo stack",
          controller.undo_label == undo_before_click, str(controller.undo_label))
    window._select_tool("cursor")

    # --- a resize mid-gesture cancels it (stale geometry) ---------------
    # Crop always had this guard; folding painting into the same dispatch gave
    # strokes the same protection, which they previously lacked.
    window._select_tool("pencil")
    geom = window.canvas._image_geom
    left, top, fw, fh = geom
    window.canvas._on_press(_XY(left + fw // 3, top + fh // 3))
    window.canvas._on_drag(_XY(left + fw // 2, top + fh // 2))
    check("resize-guard: stroke in progress", window.canvas.active_tool.is_gesturing)
    root.geometry("820x640")
    root.update()
    check("resize-guard: resize cancelled the gesture",
          not window.canvas.active_tool.is_gesturing)
    check("resize-guard: overlay cleared", len(window.canvas._overlay_items) == 0)
    undo_before_stale = controller.undo_label
    window.canvas._on_release(_XY(left + fw // 2, top + fh // 2))
    root.update()
    check("resize-guard: the stale release committed nothing",
          controller.undo_label == undo_before_stale, str(controller.undo_label))
    window._select_tool("cursor")
    root.geometry("900x680")
    root.update()

    # --- M-paint: pencil / eraser / eyedropper via the canvas dispatch ---
    # Drives the canvas _on_press/_on_drag/_on_release with display coords mapped
    # from known image pixels, so the whole tool -> op path runs, not just the op.
    root.update()
    window._set_fg_color((255, 0, 0, 255))
    window._brush_size = 3
    window._select_tool("pencil")
    check("paint: pencil is the active tool", window.canvas.has_tool)
    cxi, cyi = controller.doc.size[0] // 2, controller.doc.size[1] // 2
    # center=True: aim at the visible middle of each pixel, which is where a user
    # actually clicks -- and the case that used to land on the neighbour.
    d0 = window.canvas._image_to_display(cxi - 3, cyi - 3, center=True)
    dm = window.canvas._image_to_display(cxi, cyi, center=True)
    d1 = window.canvas._image_to_display(cxi + 3, cyi + 3, center=True)
    frames_before = controller.frame_count
    window.canvas._on_press(_XY(int(d0[0]), int(d0[1])))
    window.canvas._on_drag(_XY(int(dm[0]), int(dm[1])))
    check("paint: stroke previewed mid-drag", len(window.canvas._overlay_items) >= 1)
    window.canvas._on_release(_XY(int(d1[0]), int(d1[1])))
    root.update()
    painted = controller.doc[controller.index].image.getpixel((cxi, cyi))
    check("paint: centre pixel is now the fg colour", painted == (255, 0, 0, 255), str(painted))
    check("paint: frame count unchanged", controller.frame_count == frames_before)
    check("paint: one undoable edit recorded", controller.can_undo and controller.dirty)
    check("paint: preview cleared on commit", len(window.canvas._overlay_items) == 0)

    window._set_fg_color((0, 0, 0, 255))  # clear the fg, then pick the red back
    window._select_tool("eyedropper")
    dp = window.canvas._image_to_display(cxi, cyi, center=True)
    window.canvas._on_press(_XY(int(dp[0]), int(dp[1])))
    check("eyedropper: adopted the painted colour", window._fg_color == (255, 0, 0, 255),
          str(window._fg_color))

    window._select_tool("eraser")
    window._brush_size = 3
    de = window.canvas._image_to_display(cxi, cyi, center=True)
    window.canvas._on_press(_XY(int(de[0]), int(de[1])))
    window.canvas._on_release(_XY(int(de[0]), int(de[1])))
    root.update()
    erased = controller.doc[controller.index].image.getpixel((cxi, cyi))
    check("eraser: centre pixel alpha cleared", erased[3] == 0, str(erased))

    window._select_tool("cursor")
    check("tools: put away (back to cursor)", not window.canvas.has_tool)

    # --- bare-key shortcuts yield to a focused text field ----------------
    # Every single-key shortcut is also a text-editing key. bind_all fires after
    # the widget's own class binding, so before the guard, typing in the brush
    # Size box switched tools and BackSpace deleted a *frame* as well as a digit.
    frames_before = controller.frame_count
    window._size_box.focus_set()
    root.update()
    check("keys: focus reported as a text field", window.focus_is_text_field())

    for key in ("<b>", "<e>", "<i>", "<c>"):
        window._size_box.event_generate(key, when="now")
    root.update()
    check("keys: typing letters in Size does not switch tools",
          window._tool_var.get() == "cursor" and not window.canvas.has_tool,
          window._tool_var.get())

    for key in ("<BackSpace>", "<Delete>"):
        window._size_box.event_generate(key, when="now")
    root.update()
    check("keys: BackSpace/Delete in Size does not delete a frame",
          controller.frame_count == frames_before,
          f"{frames_before} -> {controller.frame_count}")

    playing_before = controller.playing
    window._size_box.event_generate("<space>", when="now")
    root.update()
    check("keys: space in Size does not toggle playback",
          controller.playing == playing_before)

    # Park mid-strip so every one of these keys would visibly move the playhead
    # if it got through -- at frame 0 or the last frame, some are no-ops anyway
    # and the check would pass without proving anything.
    controller.seek(3)
    root.update()
    index_before = controller.index
    for key in ("<Right>", "<Left>", "<Home>", "<End>"):
        window._size_box.event_generate(key, when="now")
    root.update()
    check("keys: arrows/Home/End in Size do not move the playhead",
          controller.index == index_before,
          f"{index_before} -> {controller.index}")

    # ...and the same keys still work once focus is back on the preview.
    window.canvas.focus_set()
    root.update()
    check("keys: canvas focus is not a text field", not window.focus_is_text_field())
    window.canvas.event_generate("<b>", when="now")
    root.update()
    check("keys: B still selects the pencil away from a text field",
          window._tool_var.get() == "pencil", window._tool_var.get())
    window._select_tool("cursor")
    window.canvas.event_generate("<Right>", when="now")
    root.update()
    check("keys: Right still steps the playhead away from a text field",
          controller.index == index_before + 1,
          f"{index_before} -> {controller.index}")

    # reset to a clean single edit for the save section
    while controller.can_undo:
        controller.undo()
    root.update()

    # --- save -----------------------------------------------------------
    # Delete a middle frame: the smoke GIF's frames are all distinct, so no
    # identical-consecutive merge, and the saved count round-trips exactly.
    # (Merge-on-save is covered separately in tests/test_gif_write.py.)
    save_path = Path(tempfile.mkdtemp()) / "smoke_out.gif"
    window._pick(3)
    controller.run_op("frames.delete")
    check("save: dirty before saving", controller.dirty)
    # Save-safety: the opened file is still the untouched original, so a plain
    # Ctrl+S would re-encode it in place. window.save_file() is deliberately NOT
    # called here -- it would open a modal confirm and hang a scripted run (the
    # same trap as driving a param op through _invoke_op).
    check("save-safety: opened file is flagged as the source",
          controller.overwrites_source)
    check("save-safety: Save As is offered a non-destructive name",
          controller.suggested_save_name == "smoke_edited.gif",
          controller.suggested_save_name)
    controller.save_as(save_path)
    root.update()
    check("save: file written to disk", save_path.exists())
    check("save: dirty cleared after save", not controller.dirty)
    check("save: title marker gone", not window.root.title().startswith("*"))
    check("save-safety: no longer the source after writing",
          not controller.overwrites_source)

    # A second Ctrl+S with nothing changed must not re-encode. Safe to route
    # through window.save_file() here precisely because it takes the skip branch
    # -- no modal, so no hang.
    check("clean-save: nothing left to save", controller.save_would_change_nothing)
    saved_bytes = save_path.read_bytes()
    saved_mtime = save_path.stat().st_mtime_ns
    window.save_file()
    root.update()
    check("clean-save: file untouched on disk",
          save_path.read_bytes() == saved_bytes
          and save_path.stat().st_mtime_ns == saved_mtime)
    check("clean-save: status line says so",
          window.status["text"] == "No changes to save", window.status["text"])
    check("save-safety: Save As now keeps our own name",
          controller.suggested_save_name == "smoke_out.gif",
          controller.suggested_save_name)
    from giflite.core.io.gif_read import read_gif  # noqa: E402
    check("save: file reopens with the edited frame count",
          len(read_gif(save_path)) == controller.frame_count,
          f"{len(read_gif(save_path))} vs {controller.frame_count}")

    # --- save-safety: the three ways out of the overwrite prompt ---------
    # save_file() opens a modal confirm, which would hang a scripted run, so the
    # answer is stubbed and what's checked is the *routing*: Cancel writes
    # nothing, No diverts to Save As, Yes overwrites in place. (That the dialog's
    # own option set is valid Tk is proved separately -- it constructs with
    # -detail and -default no, and Enter picks the safe button.)
    from tkinter import filedialog as _fd  # noqa: E402
    from tkinter import messagebox as _mb  # noqa: E402

    original_ask = _mb.askyesnocancel
    original_saveas = _fd.asksaveasfilename
    answer = {"value": None}
    _mb.askyesnocancel = lambda *a, **k: answer["value"]

    source = Path(tempfile.mkdtemp()) / "original.gif"
    make_gif(source, frames=4, size=(80, 40))
    source_bytes = source.read_bytes()
    diverted = Path(tempfile.mkdtemp()) / "diverted.gif"
    _fd.asksaveasfilename = lambda *a, **k: str(diverted)

    try:
        # Cancel: nothing written anywhere.
        window.open_path(source)
        window._pick(1)
        controller.run_op("frames.delete")
        answer["value"] = None
        window.save_file()
        root.update()
        check("save-safety: Cancel left the original untouched",
              source.read_bytes() == source_bytes)
        check("save-safety: Cancel left the edit unsaved", controller.dirty)

        # No: diverted to Save As, original still intact.
        answer["value"] = False
        window.save_file()
        root.update()
        check("save-safety: No diverted to Save As", diverted.exists())
        check("save-safety: No left the original untouched",
              source.read_bytes() == source_bytes)
        check("save-safety: No still counts as saved", not controller.dirty)
        check("save-safety: path followed the diversion", controller.path == diverted,
              str(controller.path))

        # Yes: overwrite the original, and don't ask a second time.
        window.open_path(source)
        window._pick(1)
        controller.run_op("frames.delete")
        answer["value"] = True
        window.save_file()
        root.update()
        check("save-safety: Yes overwrote the original",
              source.read_bytes() != source_bytes)
        check("save-safety: Yes cleared dirty", not controller.dirty)
        check("save-safety: warned only once per file", not controller.overwrites_source)
    finally:
        _mb.askyesnocancel = original_ask
        _fd.asksaveasfilename = original_saveas

    # Back to the edited document for the remaining view checks.
    window.open_path(gif)
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

    # --- zoom and pan ----------------------------------------------------
    # The transform's arithmetic is covered headlessly in tests/test_view.py.
    # What can only be checked here is that the *renderer* agrees with it: that
    # the bitmap Tk was handed corresponds to the geometry the tools map
    # against. Those two drifting apart is exactly the class of bug ARCHITECTURE
    # 19.1 records twice.
    view = window.canvas.view
    check("zoom: opens at fit", view.is_fit, view.label)
    fit_photo_w = window.canvas._photo.width()

    window.zoom_actual()
    root.update()
    check("zoom: actual size is 1:1", abs(view.scale - 1.0) < 1e-9, view.label)
    check("zoom: 1:1 geometry equals the source size",
          window.canvas._image_geom[2:] == controller.doc.size,
          f"{window.canvas._image_geom[2:]} vs {controller.doc.size}")

    # An edit must not throw the view back to fit. Crop is the case that
    # matters -- you cropped in order to look closely at what is left -- but the
    # rule is "only a genuinely new document resets", the same distinction the
    # timeline already makes with its own reset_view.
    controller.run_op("frames.duplicate")
    root.update()
    check("zoom: an edit leaves the magnification alone",
          not view.is_fit and abs(view.scale - 1.0) < 1e-9, view.label)
    controller.undo()
    root.update()

    for _ in range(8):
        window.zoom_in()
    root.update()
    check("zoom: the ladder tops out", not view.can_zoom_in, view.label)

    # The claim the whole crop-then-scale rework rests on. At 32x this GIF would
    # compose to tens of thousands of pixels a side if the old scale-the-whole-
    # image path had survived; the composed bitmap must instead stay bounded by
    # the window, whatever the zoom.
    vw = window.canvas.winfo_width()
    vh = window.canvas.winfo_height()
    photo_w = window.canvas._photo.width()
    photo_h = window.canvas._photo.height()
    scaled_w = controller.doc.size[0] * view.scale
    check("zoom: the composed bitmap is viewport-bounded, not image-bounded",
          photo_w <= vw + 2 * view.scale and photo_h <= vh + 2 * view.scale,
          f"photo {photo_w}x{photo_h}, viewport {vw}x{vh}, "
          f"whole image would be {int(scaled_w)}px")
    check("zoom: _image_geom still describes the whole image",
          window.canvas._image_geom[2] > vw,
          f"geom w {window.canvas._image_geom[2]} vs viewport {vw}")

    # Ground truth again, this time zoomed: the drawn item is only the visible
    # slice, so its bbox is the geometry origin plus the cropped-away part. If
    # these disagree, every gesture at high zoom lands somewhere else.
    img_item = [i for i in window.canvas.find_all()
                if window.canvas.type(i) == "image"][0]
    bbox = window.canvas.bbox(img_item)
    left, top, fw, fh = window.canvas._image_geom
    x0, y0, _, _ = view.visible_source_rect()
    sx = fw / controller.doc.size[0]
    sy = fh / controller.doc.size[1]
    check("zoom: the drawn slice sits where the geometry says it should",
          abs(bbox[0] - (left + x0 * sx)) <= 1 and abs(bbox[1] - (top + y0 * sy)) <= 1,
          f"bbox {bbox[:2]} vs expected {(left + x0 * sx, top + y0 * sy)}")

    # The 19.1 trap, at a zoom where it is 32 screen pixels wide rather than
    # hypothetical. Hand-computed widget point, no help from _image_to_display.
    src_w, src_h = controller.doc.size
    for target in ((0, 0), (src_w // 2, src_h // 2)):
        hand_x = left + round((target[0] + 0.5) * sx)
        hand_y = top + round((target[1] + 0.5) * sy)
        got = window.canvas._display_to_image(hand_x, hand_y)
        check(f"zoom: pixel {target} still maps to itself at {view.percent}%",
              got == target, f"got {got}")

    # Pan, and the edge that a button has to be able to detect.
    before_pan = window.canvas._image_geom[0]
    moved = window.canvas.pan(0.25, 0)
    root.update()
    check("pan: the view moved right, so the image moved left",
          moved and window.canvas._image_geom[0] < before_pan,
          f"{before_pan} -> {window.canvas._image_geom[0]}")
    guard = 0
    while window.canvas.pan(0.25, 0) and guard < 500:
        guard += 1
    check("pan: stops reporting movement at the edge", not window.canvas.pan(0.25, 0))
    left, _, fw, _ = window.canvas._image_geom
    check("pan: no pasteboard shows on an axis with image to spare",
          left <= 0 and left + fw >= window.canvas.winfo_width(),
          f"left {left}, right {left + fw}, viewport {window.canvas.winfo_width()}")

    # A view change is the same staleness a resize causes: coordinates already
    # collected now map somewhere else. Crop had this guard, painting gained it
    # when the dispatch was shared, and zoom has to join them.
    window._select_tool("pencil")
    left, top, fw, fh = window.canvas._image_geom
    window.canvas._on_press(_XY(left + fw // 2, top + fh // 2))
    check("zoom: a gesture is in progress", window.canvas.active_tool.is_gesturing)
    edits_before = controller.can_undo
    window.zoom_out()
    root.update()
    check("zoom: a view change mid-gesture abandons it",
          not window.canvas.active_tool.is_gesturing)
    check("zoom: and commits nothing", controller.can_undo == edits_before)
    check("zoom: overlay cleared with it", len(window.canvas._overlay_items) == 0)
    window._select_tool("cursor")

    window.zoom_fit()
    root.update()
    check("zoom: fit restores the original bitmap width",
          view.is_fit and window.canvas._photo.width() == fit_photo_w,
          f"{window.canvas._photo.width()} vs {fit_photo_w}")
    check("zoom: the status line reports the zoom", view.label in window.status["text"],
          window.status["text"])

    # --- the side panel, the palette and the navigator ---------------------
    # The panel replaced a toolbar cluster that did not fit (1087px wanted,
    # 900 available -- Tk silently dropped three widgets off the end). It now
    # carries the tool palette as well, which is *always* shown; only the view
    # section comes and goes with the zoom.
    def state_of(button):
        return str(button["state"])

    window.zoom_fit()
    root.update()
    check("panel: the palette is always present",
          window.side_panel.winfo_ismapped())
    check("panel: view section hidden at fit, where the map would say nothing",
          not window.view_panel.winfo_ismapped())
    check("panel: Fit is disabled when already fitted",
          state_of(window._fit_button) == "disabled")

    window.zoom_in()
    root.update()
    check("panel: view section appears once there is something to navigate",
          window.view_panel.winfo_ismapped())
    check("panel: readout agrees with the transform",
          window._zoom_label["text"] == view.label, window._zoom_label["text"])
    check("panel: Fit is live again", state_of(window._fit_button) == "normal")

    canvas_w_with_panel = window.canvas.winfo_width()
    check("panel: it takes its width off the preview, not the window",
          canvas_w_with_panel < root.winfo_width(),
          f"canvas {canvas_w_with_panel} vs window {root.winfo_width()}")

    while view.can_zoom_in:
        window._zoom_in_button.invoke()
    root.update()
    check("panel: + disables at the top of the ladder",
          state_of(window._zoom_in_button) == "disabled", view.label)
    check("panel: - stays live there", state_of(window._zoom_out_button) == "normal")

    # The map itself. It draws a thumbnail plus a viewport rectangle; at fit the
    # rectangle would cover everything, so it is deliberately not drawn.
    mm = window.minimap
    check("map: it drew a thumbnail", mm._photo is not None)
    check("map: it is fit-locked", mm.view.is_fit, mm.view.label)
    check("map: zoomed in, the viewport rectangle is drawn",
          any(mm.type(i) == "rectangle" for i in mm.find_all()),
          f"{[mm.type(i) for i in mm.find_all()]}")

    # Dragging the map pans the preview -- absolutely, not relatively: the point
    # you press is the point you get.
    mleft, mtop, mfw, mfh = mm.view.geometry()
    target = (controller.doc.size[0] // 4, controller.doc.size[1] // 4)
    mx, my = mm.view.image_to_display(*target)
    mm._on_point(_XY(int(mx), int(my)))
    root.update()
    got = window.canvas.view.center
    check("map: pointing at the map centres the preview there",
          abs(got[0] - target[0]) <= 2 and abs(got[1] - target[1]) <= 2,
          f"asked {target}, got {(round(got[0], 1), round(got[1], 1))}")

    # Off the edge of the map: slide to the edge and stop, don't fling.
    mm._on_point(_XY(mleft + mfw + 400, mtop + mfh + 400))
    root.update()
    left, top, fw, fh = window.canvas._image_geom
    check("map: dragging off the edge clamps instead of flinging",
          left + fw >= window.canvas.winfo_width() and top + fh >= window.canvas.winfo_height(),
          f"geom {(left, top, fw, fh)} in "
          f"{window.canvas.winfo_width()}x{window.canvas.winfo_height()}")

    check("map: the rectangle followed the pan",
          mm._visible == window.canvas.view.visible_source_rect(),
          f"{mm._visible} vs {window.canvas.view.visible_source_rect()}")

    # The case a command-driven refresh would miss: the fit scale changes on a
    # resize with no button behind it.
    window.zoom_fit()
    root.update()
    label_before = window._zoom_label["text"]
    root.geometry("620x520")
    root.update()
    check("panel: a resize refreshes the readout with no command behind it",
          window._zoom_label["text"] != label_before,
          f"{label_before} -> {window._zoom_label['text']}")
    root.geometry("900x680")
    root.update()

    window._actual_button.invoke()
    root.update()
    check("panel: the 1:1 button is 1:1", abs(view.scale - 1.0) < 1e-9, view.label)
    window._fit_button.invoke()
    root.update()
    check("panel: the Fit button returns to fit", view.is_fit, view.label)
    check("panel: the view section hides itself again",
          not window.view_panel.winfo_ismapped())
    # The preview does *not* get width back any more, and that is the point of
    # the restructure: the palette lives here permanently, so the strip's width
    # is a constant rather than something that jumps as you zoom. The canvas
    # jumping 200px sideways every time you crossed fit would be worse than the
    # width it costs.
    check("panel: the preview width is now stable across a zoom change",
          window.canvas.winfo_width() == canvas_w_with_panel,
          f"{canvas_w_with_panel} -> {window.canvas.winfo_width()}")

    # --- the palette survives a cramped window ----------------------------
    # §21's failure was pack silently dropping widgets off a row that didn't
    # fit. The palette is a column now, so the same risk lives on the other
    # axis -- and at the 480x400 minimum it is real: measured, the view section
    # wanted 412px of a 238px panel. `_view_section_fits` hides the section
    # deliberately instead of letting pack amputate half of it.
    root.geometry("480x400")
    root.update()
    root.update()
    unreachable = [tid for tid in window._tool_buttons
                   if not window._tool_buttons[tid].winfo_ismapped()]
    check("cramped: every tool is still reachable at the minimum window size",
          unreachable == [], f"dropped: {unreachable}")
    check("cramped: the view section stands down rather than being amputated",
          not window.view_panel.winfo_ismapped())
    window.zoom_in()
    root.update()
    check("cramped: and stays down even when zoomed, rather than half-drawn",
          not window.view_panel.winfo_ismapped()
          or window._zoom_in_button.winfo_ismapped(),
          "half a navigator is worse than none")
    root.geometry("900x680")
    root.update()
    root.update()
    check("cramped: it comes back when there is room again",
          window.view_panel.winfo_ismapped() and window._zoom_in_button.winfo_ismapped())
    window.zoom_fit()
    window._select_tool("cursor")
    root.update()

    # --- fill and shapes through the real canvas dispatch ------------------
    # The ops are covered headlessly. What only a display answers: does a click
    # at a given screen point reach the pixel the user aimed at, and does the
    # marquee a shape draws match the shape that lands?
    window.zoom_fit()
    root.update()
    doc_w, doc_h = controller.doc.size

    def click(tool_id, ix, iy):
        window._select_tool(tool_id)
        dx, dy = window.canvas._image_to_display(ix, iy, center=True)
        window.canvas._on_press(_XY(int(dx), int(dy)))
        window.canvas._on_release(_XY(int(dx), int(dy)))
        root.update()

    def drag(tool_id, box):
        window._select_tool(tool_id)
        x0, y0, x1, y1 = box
        sx, sy = window.canvas._image_to_display(x0, y0, center=True)
        ex, ey = window.canvas._image_to_display(x1, y1, center=True)
        window.canvas._on_press(_XY(int(sx), int(sy)))
        window.canvas._on_drag(_XY(int(ex), int(ey)))
        window.canvas._on_release(_XY(int(ex), int(ey)))
        root.update()

    # Give the frame a known flat colour to fill into, via a filled rect over
    # the whole canvas -- which also exercises the shape path first.
    window._set_fg_color((10, 20, 30, 255))
    window._fill_var.set(True)
    edits_before = controller.can_undo
    drag("rect", (0, 0, doc_w - 1, doc_h - 1))
    frame = controller.frame_image()
    check("shape: a filled rect drag covers the canvas it was dragged over",
          frame.getpixel((0, 0)) == (10, 20, 30, 255)
          and frame.getpixel((doc_w - 1, doc_h - 1)) == (10, 20, 30, 255),
          f"{frame.getpixel((0, 0))} / {frame.getpixel((doc_w - 1, doc_h - 1))}")
    check("shape: it landed as one undoable edit", controller.can_undo != edits_before)

    window._set_fg_color((200, 100, 50, 255))
    click("fill", doc_w // 2, doc_h // 2)
    frame = controller.frame_image()
    check("fill: clicking a flat region recolours all of it",
          frame.getpixel((0, 0)) == (200, 100, 50, 255)
          and frame.getpixel((doc_w - 1, doc_h - 1)) == (200, 100, 50, 255),
          str(frame.getpixel((0, 0))))

    # A shape's marquee has to enclose the pixels the shape will cover. The
    # preview draws through pixel *corners*, so the far edge is pushed out by
    # one -- get that wrong and the box you drew is a pixel short of the box you
    # get, which is invisible at 1:1 and obvious at 30x.
    # A colour that isn't already on the frame -- the previous fill made every
    # pixel (200, 100, 50), and drawing a rect in that colour would have been
    # invisible *and* declined by the op, which is how the first draft of this
    # check managed to assert nothing at all.
    window._set_fg_color((0, 255, 0, 255))
    window._select_tool("rect")
    sx, sy = window.canvas._image_to_display(2, 2, center=True)
    ex, ey = window.canvas._image_to_display(6, 5, center=True)
    window.canvas._on_press(_XY(int(sx), int(sy)))
    window.canvas._on_drag(_XY(int(ex), int(ey)))
    root.update()
    marquee = [i for i in window.canvas._overlay_items
               if window.canvas.type(i) == "rectangle"]
    check("shape: a marquee is drawn while dragging", len(marquee) == 1,
          f"{len(marquee)} rectangles")
    if marquee:
        mx0, my0, mx1, my1 = window.canvas.coords(marquee[0])
        want_x0, want_y0 = window.canvas._image_to_display(2, 2)
        want_x1, want_y1 = window.canvas._image_to_display(7, 6)  # far edge + 1
        check("shape: the marquee encloses the last pixel rather than bisecting it",
              abs(mx0 - want_x0) < 1.5 and abs(my0 - want_y0) < 1.5
              and abs(mx1 - want_x1) < 1.5 and abs(my1 - want_y1) < 1.5,
              f"drawn {(mx0, my0, mx1, my1)} want {(want_x0, want_y0, want_x1, want_y1)}")
    window.canvas._on_release(_XY(int(ex), int(ey)))
    root.update()
    frame = controller.frame_image()
    # Inclusive at both ends: the pixel you dragged to is inside the shape, and
    # the next one along is not. One pixel of slack here is the whole §19.1
    # class of bug.
    check("shape: the committed rect matches the box that was drawn",
          frame.getpixel((2, 2)) == (0, 255, 0, 255)
          and frame.getpixel((6, 5)) == (0, 255, 0, 255)
          and frame.getpixel((7, 6)) == (200, 100, 50, 255),
          f"near {frame.getpixel((2, 2))}, far {frame.getpixel((6, 5))}, "
          f"outside {frame.getpixel((7, 6))}")

    # Esc mid-drag: same two-stage contract crop has.
    window._select_tool("ellipse")
    window.canvas._on_press(_XY(int(sx), int(sy)))
    window.canvas._on_drag(_XY(int(ex), int(ey)))
    edits_before = controller.can_undo
    check("shape: a drag counts as a gesture in progress",
          window.canvas.active_tool.is_gesturing)
    window.canvas._on_escape()
    check("shape: Esc abandons it", not window.canvas.active_tool.is_gesturing)
    check("shape: and commits nothing", controller.can_undo == edits_before)
    check("shape: the tool stays selected for a second attempt",
          window._tool_var.get() == "ellipse", window._tool_var.get())

    # The palette drives the tools, and every id in it has to resolve.
    for tid in window._tool_buttons:
        window._tool_buttons[tid].invoke()
        root.update()
        ok = (tid == "cursor") if not window.canvas.has_tool else \
            window.canvas.active_tool.id == tid
        check(f"palette: {tid} selects", ok, window._tool_var.get())
    window._select_tool("cursor")
    window._fill_var.set(False)
    while controller.can_undo:
        controller.undo()
    root.update()

    # --- erase mode --------------------------------------------------------
    # The ops are covered headlessly. What only a display answers: does the
    # checkbox actually reach a tool that is already selected, and does the
    # colour swatch stand down when the colour stops being used.
    window.zoom_fit()
    root.update()
    doc_w, doc_h = controller.doc.size
    window._set_fg_color((10, 20, 30, 255))
    window._fill_var.set(True)
    drag("rect", (0, 0, doc_w - 1, doc_h - 1))
    check("erase: a solid frame to clear",
          controller.frame_image().getpixel((4, 4)) == (10, 20, 30, 255),
          str(controller.frame_image().getpixel((4, 4))))

    window._select_tool("fill")
    window._erase_var.set(True)
    window._on_erase_toggle()
    root.update()
    check("erase: the swatch stands down, since the colour is unused",
          str(window._swatch["state"]) == "disabled", str(window._swatch["state"]))
    # The swatch alone cannot show this: its background *is* the colour, and an
    # explicit bg survives being disabled, so the button looks identical either
    # way. The label greying is the half you can actually see.
    check("erase: and the Colour label greys, which is the visible half",
          "disabled" in window._colour_label.state(),
          str(window._colour_label.state()))
    check("erase: the status line says the selected tool now erases",
          "erasing" in window.status["text"], window.status["text"])

    click("fill", doc_w // 2, doc_h // 2)
    check("erase: the bucket cleared the region instead of recolouring it",
          controller.frame_image().getpixel((4, 4))[3] == 0,
          str(controller.frame_image().getpixel((4, 4))))
    check("erase: the undo entry names what happened, not what the op is called",
          controller.undo_label == "Erase Fill", str(controller.undo_label))

    # One edit, checked by undoing it rather than by comparing a boolean to
    # itself -- `can_undo` was already True before the fill, so the obvious
    # version of this check passes without asserting anything.
    controller.undo()
    root.update()
    check("erase: one undo put every cleared pixel back",
          controller.frame_image().getpixel((4, 4)) == (10, 20, 30, 255),
          str(controller.frame_image().getpixel((4, 4))))
    window._select_tool("pencil")
    root.update()
    px, py = window.canvas._image_to_display(3, 3, center=True)
    ex, ey = window.canvas._image_to_display(9, 3, center=True)
    window.canvas._on_press(_XY(int(px), int(py)))
    window.canvas._on_drag(_XY(int(ex), int(ey)))
    window.canvas._on_release(_XY(int(ex), int(ey)))
    root.update()
    check("erase: the pencil erased rather than painted",
          controller.frame_image().getpixel((6, 3))[3] == 0,
          str(controller.frame_image().getpixel((6, 3))))
    check("erase: and it is recorded as an erase",
          controller.undo_label == "Erase", str(controller.undo_label))

    window._erase_var.set(False)
    window._on_erase_toggle()
    root.update()
    check("erase: unticking brings the swatch back",
          str(window._swatch["state"]) == "normal", str(window._swatch["state"]))
    check("erase: and the Colour label with it",
          "disabled" not in window._colour_label.state(),
          str(window._colour_label.state()))
    check("erase: and the hint drops the qualifier",
          "erasing" not in window.status["text"], window.status["text"])

    window._select_tool("cursor")
    window._fill_var.set(False)
    while controller.can_undo:
        controller.undo()
    root.update()

    # --- select / copy / cut / paste ---------------------------------------
    # The ops and the region arithmetic are covered headlessly. What only a
    # display answers: does a drag on screen select the rectangle the user
    # aimed at, does the marquee *survive a redraw* (it is the first overlay
    # here that has to), and do the shortcuts fire while a text box has focus.
    window.zoom_fit()
    root.update()
    doc_w, doc_h = controller.doc.size

    # Paint a known block so a copy has something identifiable in it.
    window._set_fg_color((0, 200, 255, 255))
    window._fill_var.set(True)
    drag("rect", (4, 4, 11, 9))
    window._fill_var.set(False)
    root.update()

    def region_items():
        """The marching-ants pair, by colour.

        By outline rather than by dash: the ants are a dark solid rectangle
        under a light dashed one, so filtering on "has a dash" finds half of
        them and passes anyway -- which is exactly the sort of check that
        looks green while testing nothing.
        """
        ants = (canvas_module.ANTS_DARK, canvas_module.ANTS_LIGHT)
        return [i for i in window.canvas.find_all()
                if window.canvas.type(i) == "rectangle"
                and window.canvas.itemcget(i, "outline") in ants]

    drag("select", (4, 4, 12, 10))
    check("select: the drag became a region",
          controller.region is not None, str(controller.region))
    check("select: the region is the rectangle that was dragged",
          controller.region == Region(4, 4, 8, 6),
          str(controller.region))
    check("select: marching ants drawn", len(region_items()) == 2,
          f"{len(region_items())} dashed rectangles")
    check("select: the gesture overlay is gone", len(window.canvas._overlay_items) == 0)

    # The point of the whole persistent-overlay exercise: every earlier overlay
    # died on the next `delete("all")` and was redrawn by the next mouse event.
    # A region has no next mouse event.
    controller.seek(3)
    root.update()
    check("select: the marquee survives scrubbing to another frame",
          len(region_items()) == 2 and controller.region is not None,
          f"{len(region_items())} rectangles")
    window.zoom_actual()
    root.update()
    check("select: and survives a zoom", len(region_items()) == 2,
          f"{len(region_items())} rectangles")
    window.zoom_fit()
    controller.seek(0)
    root.update()

    ants = region_items()
    if len(ants) == 2:
        ax0, ay0, ax1, ay1 = window.canvas.coords(ants[0])
        want0 = window.canvas._image_to_display(4, 4)
        want1 = window.canvas._image_to_display(12, 10)
        check("select: the marquee sits on the region's own edges",
              abs(ax0 - want0[0]) < 1.5 and abs(ay0 - want0[1]) < 1.5
              and abs(ax1 - want1[0]) < 1.5 and abs(ay1 - want1[1]) < 1.5,
              f"drawn {(ax0, ay0, ax1, ay1)} want {want0 + want1}")

    window.copy_region()
    check("copy: the clipboard matches the region",
          controller.clipboard_size == (8, 6), str(controller.clipboard_size))

    edits_before = controller.can_undo
    window.cut_region()
    root.update()
    frame = controller.frame_image()
    check("cut: the region is now empty", frame.getpixel((5, 5))[3] == 0,
          str(frame.getpixel((5, 5))))
    check("cut: outside the region is untouched", frame.getpixel((13, 11))[3] == 255,
          str(frame.getpixel((13, 11))))
    check("cut: it is one undoable edit named for the action",
          controller.undo_label == "Cut", str(controller.undo_label))
    check("cut: the marquee stays put after the edit", len(region_items()) == 2,
          f"{len(region_items())} rectangles")

    # Ctrl+V now *floats* the clipboard rather than landing it. Enter with no
    # drag in between is the old paste-in-place, exactly.
    window.paste_region()
    root.update()
    check("paste: it floats rather than landing", controller.floating is not None,
          str(controller.floating))
    check("paste: the document is untouched while it floats",
          controller.doc[controller.index].image.getpixel((5, 5))[3] == 0,
          str(controller.doc[controller.index].image.getpixel((5, 5))))
    check("paste: the preview shows it anyway",
          window.canvas._source.getpixel((5, 5)) == (0, 200, 255, 255),
          str(window.canvas._source.getpixel((5, 5))))
    check("paste: it brought the Move tool with it",
          window._tool_var.get() == "move", window._tool_var.get())
    window._commit_float()
    root.update()
    frame = controller.frame_image()
    check("paste: Enter with no drag is a paste in place",
          frame.getpixel((5, 5)) == (0, 200, 255, 255), str(frame.getpixel((5, 5))))
    check("paste: recorded as its own edit", controller.undo_label == "Paste",
          str(controller.undo_label))

    # Paste across a selection, and the playhead rule that makes it usable.
    controller.run_op("paint.cut", index=controller.index, x=4, y=4, width=8, height=6)
    controller.set_selection(Selection(frozenset({0, 1, 2})))
    controller.seek(2)
    root.update()
    window.paste_region()
    window._commit_float()
    root.update()
    check("paste: it stamped every selected frame",
          all(controller.doc[i].image.getpixel((5, 5))[3] == 255 for i in (0, 1, 2)),
          str([controller.doc[i].image.getpixel((5, 5)) for i in (0, 1, 2)]))
    check("paste: the playhead stayed where it was", controller.index == 2,
          f"index={controller.index}")
    check("paste: the selection stayed as the user made it",
          controller.selection.ordered == (0, 1, 2), str(controller.selection.ordered))

    # --- the floating move -------------------------------------------------
    # The third state, through the real canvas: drag places it, the document is
    # untouched until Enter, and Esc puts it back with nothing on the undo stack.
    window._select_tool("select")
    controller.set_selection(Selection.single(0))
    controller.seek(0)
    root.update()
    drag("select", (4, 4, 12, 10))
    before_pixels = controller.frame_image().tobytes()
    edits_before = controller.undo_label

    window._select_tool("move")
    sx, sy = window.canvas._image_to_display(6, 6)
    ex, ey = window.canvas._image_to_display(11, 9)
    window.canvas._on_press(_XY(int(sx), int(sy)))
    window.canvas._on_drag(_XY(int(ex), int(ey)))
    root.update()
    check("move: a drag started a float", controller.floating is not None)
    check("move: it is offset by the distance dragged",
          controller.float_offset == (5, 3), str(controller.float_offset))
    check("move: the document has not been touched",
          controller.frame_image().tobytes() == before_pixels)
    check("move: but the preview shows the hole it will leave",
          window.canvas._source.getpixel((5, 5))[3] == 0,
          str(window.canvas._source.getpixel((5, 5))))
    check("move: and shows the pixels in their new place",
          window.canvas._source.getpixel((10, 8)) == (0, 200, 255, 255),
          str(window.canvas._source.getpixel((10, 8))))
    check("move: the status line says nothing has happened yet",
          "Enter to drop it" in window.status["text"], window.status["text"])
    window.zoom_in()
    window.zoom_fit()
    root.update()
    check("move: and survives a view change, because it is derived from state",
          "Enter to drop it" in window.status["text"], window.status["text"])
    check("move: the marquee followed it rather than staying on the hole",
          len(region_items()) == 2 and
          abs(window.canvas.coords(region_items()[0])[0]
              - window.canvas._image_to_display(9, 7)[0]) < 1.5,
          str(window.canvas.coords(region_items()[0]) if region_items() else None))

    window.canvas._on_release(_XY(int(ex), int(ey)))
    root.update()
    check("move: releasing did not commit it", controller.floating is not None)
    check("move: nor did it touch the document",
          controller.undo_label == edits_before, str(controller.undo_label))

    # A view change disturbs the drag, not the float -- the offset is in image
    # pixels, so nothing about the view can invalidate it.
    window.zoom_in()
    root.update()
    check("move: a zoom leaves the float exactly where it was",
          controller.floating is not None and controller.float_offset == (5, 3),
          str(controller.float_offset))
    window.zoom_fit()
    root.update()

    # Arrows nudge instead of stepping frames.
    index_before = controller.index
    window._arrow(1, 0)
    window._arrow(0, 1)
    root.update()
    check("move: arrows nudge the float", controller.float_offset == (6, 4),
          str(controller.float_offset))
    check("move: and do not step the playhead", controller.index == index_before,
          f"index={controller.index}")

    # Esc puts it back, and leaves nothing behind.
    window.canvas._on_escape()
    root.update()
    check("move: Esc cancelled the float", controller.floating is None)
    check("move: and the document was never touched",
          controller.frame_image().tobytes() == before_pixels)
    check("move: with nothing on the undo stack",
          controller.undo_label == edits_before, str(controller.undo_label))
    check("move: the tool is still selected for another go",
          window._tool_var.get() == "move", window._tool_var.get())

    # Now do it for real.
    window.canvas._on_press(_XY(int(sx), int(sy)))
    window.canvas._on_drag(_XY(int(ex), int(ey)))
    window.canvas._on_release(_XY(int(ex), int(ey)))
    window._commit_float()
    root.update()
    frame = controller.frame_image()
    check("move: Enter landed it", frame.getpixel((10, 8)) == (0, 200, 255, 255),
          str(frame.getpixel((10, 8))))
    check("move: and cleared the source", frame.getpixel((5, 5))[3] == 0,
          str(frame.getpixel((5, 5))))
    check("move: as one undoable edit", controller.undo_label == "Move",
          str(controller.undo_label))
    controller.undo()
    root.update()
    check("move: one undo puts everything back",
          controller.frame_image().tobytes() == before_pixels)

    # Anything else you do settles a float rather than stranding it.
    window.canvas._on_press(_XY(int(sx), int(sy)))
    window.canvas._on_drag(_XY(int(ex), int(ey)))
    window.canvas._on_release(_XY(int(ex), int(ey)))
    root.update()
    window._select_tool("pencil")
    root.update()
    check("move: reaching for another tool committed it rather than losing it",
          controller.floating is None and controller.undo_label == "Move",
          str(controller.undo_label))
    controller.undo()
    window._select_tool("cursor")
    root.update()

    # Ctrl+C in a text field belongs to the text field. bind_all fires *after*
    # the widget's class binding, so an unguarded binding would copy the number
    # and silently replace the image clipboard as well.
    window._select_tool("cursor")
    controller.set_region(Region(0, 0, 3, 3))
    window._size_box.focus_set()
    root.update()
    before_clip = controller.clipboard_size
    root.event_generate("<Control-c>")
    root.update()
    check("keys: Ctrl+C in the Size box does not touch the image clipboard",
          controller.clipboard_size == before_clip,
          f"{before_clip} -> {controller.clipboard_size}")
    window.canvas.focus_set()
    root.update()
    root.event_generate("<Control-c>")
    root.update()
    check("keys: Ctrl+C away from a text field copies the region",
          controller.clipboard_size == (3, 3), str(controller.clipboard_size))

    # Esc: region before frames, because the region is the thing on the canvas
    # you are looking at.
    controller.set_selection(Selection.single(1))
    window._clear_selection()
    check("esc: the region goes first", controller.region is None)
    check("esc: the frame selection is still there", bool(controller.selection))
    window._clear_selection()
    check("esc: a second press clears the frames", not controller.selection)

    # A crop the region does not survive.
    controller.set_region(Region(20, 12, 8, 6))
    controller.run_op("canvas.crop", x=0, y=0, width=10, height=8)
    root.update()
    check("select: a crop that excludes the region clears it and its marquee",
          controller.region is None and len(region_items()) == 0,
          f"region={controller.region} items={len(region_items())}")

    while controller.can_undo:
        controller.undo()
    controller.set_region(None)
    controller.set_selection(Selection.empty())
    root.update()

    # --- per-frame delay --------------------------------------------------
    # The op is M4; what only a display answers is whether the box reports the
    # right frames, edits the right frames, and stays out of the undo stack when
    # it is merely tabbed past.
    controller.set_selection(Selection.empty())
    controller.seek(2)
    root.update()
    check("delay: the box shows the playhead frame's own delay",
          window._delay_var.get() == str(controller.current_delay_ms),
          f"box {window._delay_var.get()!r} vs frame {controller.current_delay_ms}")
    check("delay: the status line reports the frame, not just the total",
          f"frame {controller.current_delay_ms} ms" in window.status["text"],
          window.status["text"])

    window._delay_var.set("450")
    window._commit_delay()
    root.update()
    check("delay: typing a value retimes the frame",
          controller.doc[2].duration_ms == 450, str(controller.doc[2].duration_ms))
    check("delay: and only that frame",
          controller.doc[1].duration_ms != 450 and controller.doc[3].duration_ms != 450)

    # A selection the playhead sits inside: the box speaks for all of it.
    controller.set_selection(Selection(frozenset({0, 1})))
    controller.seek(0)
    root.update()
    check("delay: the label names how many frames it would retime",
          "2 frames" in window._delay_label["text"], window._delay_label["text"])
    window._delay_var.set("333")
    window._commit_delay()
    root.update()
    check("delay: it retimes the whole selection as one edit",
          controller.doc[0].duration_ms == 330 and controller.doc[1].duration_ms == 330,
          f"{controller.doc[0].duration_ms}, {controller.doc[1].duration_ms}")
    check("delay: quantised to 10ms, and the box shows what landed rather than "
          "what was typed", window._delay_var.get() == "330",
          window._delay_var.get())

    # Frames that disagree cannot be shown as one number.
    controller.set_selection(Selection(frozenset({0, 2})))
    controller.seek(0)
    root.update()
    check("delay: a mixed selection blanks the box rather than lying",
          window._delay_var.get() == "", window._delay_var.get())

    # Stepping away from a selection: the box has to follow your eyes, or it
    # reports frame 0 while the preview shows frame 3.
    controller.set_selection(Selection(frozenset({0, 1})))
    controller.seek(3)
    root.update()
    check("delay: a selection the playhead has left is ignored",
          window._delay_var.get() == str(controller.doc[3].duration_ms),
          f"box {window._delay_var.get()!r} vs frame 3 {controller.doc[3].duration_ms}")

    # Tabbing through the box fires <FocusOut> with the value unchanged. That
    # must not cost an undo entry -- this is the reason the timing ops learned
    # to decline.
    #
    # What this can actually observe is the *frontend* guard, and it took two
    # goes to write a check with teeth. Comparing `undo_label` proved worthless:
    # the op declines as well, so no undo entry appears whether the guard is
    # there or not, and both mutations passed. The one visible difference is
    # that reaching the op at all costs a "nothing to do" status message, so
    # that is what gets asserted. The op-level decline is covered headlessly in
    # tests/test_timing_ops.py, which is the right layer for it.
    window.status.configure(text="untouched")
    window._commit_delay()
    root.update()
    check("delay: tabbing past an unchanged value never reaches the op",
          window.status["text"] == "untouched", window.status["text"])

    # Garbage in the box is put back, not treated as zero.
    window._delay_var.set("abc")
    window._commit_delay()
    root.update()
    check("delay: garbage is replaced with the real value",
          window._delay_var.get() == str(controller.doc[controller.index].duration_ms),
          window._delay_var.get())

    # The timeline labels: one per visible thumbnail, virtualised like the rest.
    tl_texts = [window.timeline.canvas.itemcget(i, "text")
                for i in window.timeline.canvas.find_all()
                if window.timeline.canvas.type(i) == "text"]
    check("timeline: every thumbnail carries its delay as well as its number",
          len(tl_texts) >= 2 * controller.frame_count,
          f"{len(tl_texts)} labels for {controller.frame_count} frames")
    check("timeline: the delay labels show real durations",
          any(t == "330" for t in tl_texts), str(sorted(set(tl_texts))))

    controller.set_selection(Selection.empty())
    while controller.can_undo:
        controller.undo()
    root.update()

    # --- the pixel grid ---------------------------------------------------
    # The arithmetic is covered headlessly in tests/test_view.py. What only a
    # display can answer: are the rules actually *drawn*, do they land on the
    # boundaries the mapping names, and does the menu report the mode.
    def grid_items():
        """Canvas lines that are grid rules. The frame border is a rectangle and
        a tool overlay is tagged, so a line item here is a rule."""
        return [i for i in window.canvas.find_all() if window.canvas.type(i) == "line"]

    # Note for anyone reading the numbers below: this 160x80 GIF *fits* at 552%
    # in a 900px window, so fit is already past auto's threshold and the grid is
    # on the moment the file opens. That is the rule working as specified rather
    # than a bug -- but it is why these checks drive the scale explicitly
    # instead of assuming fit means "zoomed out". The first draft assumed it and
    # failed here, which is the cheapest place to find that out.
    window.set_grid_mode("off")
    window.zoom_fit()
    while view.scale > 2.0 and view.can_zoom_out:
        window.zoom_out()
    root.update()
    check("grid: nothing drawn when off", len(grid_items()) == 0,
          f"at {view.label}: {len(grid_items())} lines")

    window.set_grid_mode("auto")
    root.update()
    check("grid: auto draws nothing below its threshold", len(grid_items()) == 0,
          f"at {view.label}: {len(grid_items())} lines")
    check("grid: and says so rather than silently doing nothing",
          "not shown" in window.status["text"], window.status["text"])

    while view.scale < 8.0:
        window.zoom_in()
    root.update()
    rules = grid_items()
    check("grid: auto draws rules once past the threshold", len(rules) > 0,
          f"at {view.label}: {len(rules)} lines")

    # Ground truth: a drawn rule must sit exactly where the mapping puts the
    # boundary it claims to be. This is the check that would have caught 19.1
    # if the grid had existed then -- a grid half a pixel off from the pixels it
    # divides is worse than no grid, because you would trust it.
    lines = view.grid_lines()
    drawn_x = sorted({window.canvas.coords(i)[0] for i in rules
                      if window.canvas.coords(i)[0] == window.canvas.coords(i)[2]})
    check("grid: every vertical rule is drawn where the transform says",
          len(drawn_x) == len(lines.xs)
          and all(abs(a - b) < 0.01 for a, b in zip(drawn_x, sorted(lines.xs))),
          f"{len(drawn_x)} drawn vs {len(lines.xs)} expected")

    # And that the boundary is the real one: the pixels either side of a drawn
    # rule are the two the mapping names, through the same dispatch a click uses.
    mid = drawn_x[len(drawn_x) // 2]
    left_px, _ = window.canvas._display_to_image(mid - 0.25, lines.top + 1)
    right_px, _ = window.canvas._display_to_image(mid + 0.25, lines.top + 1)
    check("grid: a rule separates exactly the two pixels it sits between",
          right_px == left_px + 1, f"{left_px} | {right_px} at x={mid}")

    check("grid: the rule count is viewport-bounded, not image-bounded",
          len(rules) < window.canvas.winfo_width() // 8 + window.canvas.winfo_height() // 8 + 8,
          f"{len(rules)} lines at {view.label}")

    # The count has to stay bounded across a redraw, not grow: `_draw` clears
    # everything first, and a grid appended without that would accumulate.
    controller.seek(controller.index + 1)
    root.update()
    check("grid: a frame change redraws the rules rather than stacking them",
          len(grid_items()) == len(rules), f"{len(grid_items())} vs {len(rules)}")

    # Always reaches below auto's threshold; both stop before the rules touch.
    while view.scale > 2.0 and view.can_zoom_out:
        window.zoom_out()
    root.update()
    check("grid: auto is still quiet at 200%", len(grid_items()) == 0, view.label)
    window.set_grid_mode("always")
    root.update()
    check("grid: always draws there instead", len(grid_items()) > 0,
          f"at {view.label}: {len(grid_items())} lines")

    window.zoom_actual()
    root.update()
    check("grid: even always stops at 1:1, where the rules would touch",
          len(grid_items()) == 0, view.label)

    # The menu is driven by the variable, so it reports the mode for free -- as
    # long as every path writes it. The keyboard shortcut is the path that
    # would forget.
    window.cycle_grid_mode()
    check("grid: the shortcut cycles always -> off",
          view.grid_mode == "off" and window._grid_var.get() == "off",
          f"{view.grid_mode} / {window._grid_var.get()}")
    window.cycle_grid_mode()
    check("grid: and on round to auto",
          view.grid_mode == "auto" and window._grid_var.get() == "auto",
          f"{view.grid_mode} / {window._grid_var.get()}")

    # A view change mid-gesture abandons it, and the grid goes through the same
    # funnel -- because `_draw` deletes the overlay whether or not the image
    # moved, so a gesture surviving a grid toggle is a gesture whose preview has
    # silently vanished.
    window._select_tool("pencil")
    window.canvas._on_press(_XY(*[int(c) for c in window.canvas._image_to_display(4, 4)]))
    window.canvas._on_drag(_XY(*[int(c) for c in window.canvas._image_to_display(9, 9)]))
    check("grid: a stroke is in progress", window.canvas.active_tool.is_gesturing)
    edits_before = controller.can_undo
    window.set_grid_mode("always")
    check("grid: toggling it abandons the gesture rather than orphaning the overlay",
          not window.canvas.active_tool.is_gesturing)
    check("grid: and commits nothing", controller.can_undo == edits_before)
    check("grid: overlay cleared with it", len(window.canvas._overlay_items) == 0)
    window._select_tool("cursor")

    window.set_grid_mode("auto")
    window.zoom_fit()
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

    # --- import / export frames -------------------------------------------
    # The pickers are modal, so the menu commands themselves can't be driven in
    # a scripted run; what is checked here is everything either side of them --
    # the menu state, the controller calls they make, and the title.
    from PIL import Image as _Image

    src = tmp / "seq_in"
    src.mkdir()
    for i in range(1, 13):        # >9 on purpose: the natural-sort case
        _Image.new("RGBA", (24, 16), (i * 20 % 256, 60, 90, 255)).save(src / f"f{i}.png")

    # Menu state is only recomputed by the postcommand, so drive that
    # explicitly -- `entrycget` otherwise reports whatever was last configured,
    # which made the first version of this check read a stale "normal" and look
    # like a failure of the wrong thing.
    controller.close()
    root.update()
    window._refresh_file_menu()
    # *Every* document-dependent entry, not just one. Checking a single label
    # proved worthless: the old hardcoded `(2, 3, 5)` happens to include the
    # index Export now sits at, so a one-label check passed against the broken
    # version too. Save and Close are the entries those indices stopped
    # reaching, so they are the ones that make this bite.
    still_enabled = [label for label in
                     ("Export Frames...", "Save", "Save As...", "Close")
                     if str(window.file_menu.entrycget(label, "state")) != "disabled"]
    check("menu: every document-dependent entry disables with no document",
          still_enabled == [], f"still enabled: {still_enabled}")

    window._with_busy_cursor(lambda: controller.import_frames(src, delay_ms=120))
    root.update()
    check("import: frames loaded in natural order", controller.frame_count == 12,
          str(controller.frame_count))
    check("import: the delay from the dialog was applied",
          controller.doc[0].duration_ms == 120, str(controller.doc[0].duration_ms))
    check("import: the canvas followed", window.canvas._photo is not None)
    check("import: the timeline redrew",
          len([i for i in window.timeline.canvas.find_all()
               if window.timeline.canvas.type(i) == "image"]) == 12)
    check("import: the title shows the folder, not just the app name",
          root.title().endswith("seq_in - GIF Editor Lite"), root.title())
    check("import: no path, so Save falls through to Save As",
          not controller.has_path)

    # The menu-state check that matters: entries are configured by *label* now.
    # They used to be indices, and inserting Import/Export after Open silently
    # repointed (2, 3, 5) at Export, a separator and Save As -- no error, just
    # the wrong three items greying out.
    window._refresh_file_menu()
    for label in ("Export Frames...", "Save", "Save As...", "Close"):
        check(f"menu: {label} is enabled with a document",
              str(window.file_menu.entrycget(label, "state")) == "normal",
              str(window.file_menu.entrycget(label, "state")))
    check("menu: Open stays enabled regardless",
          str(window.file_menu.entrycget("Open...", "state")) == "normal")

    out = tmp / "seq_out"
    window._with_busy_cursor(lambda: controller.export_frames(out))
    root.update()
    exported = sorted(p.name for p in out.glob("*.png"))
    check("export: one PNG per frame", len(exported) == 12, str(len(exported)))
    check("export: zero-padded so naive sorts agree",
          exported[0] == "frame_0001.png" and exported[-1] == "frame_0012.png",
          f"{exported[0]} .. {exported[-1]}")
    check("export: a manifest went with them", (out / "giflite.json").exists())
    check("export: it did not claim the document now lives there",
          not controller.has_path)

    # Round trip through the UI's own calls.
    window._with_busy_cursor(lambda: controller.import_frames(out))
    root.update()
    check("round trip: frame count survives", controller.frame_count == 12)
    check("round trip: timing survives the folder rather than resetting",
          controller.doc[0].duration_ms == 120, str(controller.doc[0].duration_ms))

    # NOT checked here: a failing import. It emits ERROR, `_on_error` raises a
    # modal messagebox, and a modal hangs a scripted run forever -- the same
    # trap the param dialogs set, and it hung this run until it was removed.
    # Covered headlessly instead, in test_controller.py::TestImportExport.

    controller.close()
    root.update()
    check("import: closing clears the folder name from the title",
          root.title() == "GIF Editor Lite", root.title())
    window.open_path(gif)
    root.update()
    check("import: opening a real file afterwards restores a path",
          controller.has_path and root.title().startswith("smoke.gif"), root.title())

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
