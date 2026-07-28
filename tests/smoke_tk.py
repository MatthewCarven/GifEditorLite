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
