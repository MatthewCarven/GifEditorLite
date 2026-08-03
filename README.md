# GIF Editor Lite

A small, modular GIF editor in Python. Tkinter frontend, Pillow for pixels,
nothing else.

Status: **a working lite GIF editor.** Opens, plays (with ping-pong), edits
frames (delete/duplicate/reorder/reverse/trim), re-times them (set delay,
scale speed), transforms the canvas (resize/rotate/flip/crop), paints
(pencil/eraser/fill/shapes, with Shift constraints), selects/copies/moves
pixels, copies frames to and from the system clipboard, zooms with a
navigator and pixel grid, undo/redo, and saves back to GIF — or losslessly to
its own `.gifproj` project format. See [TODO.md](TODO.md) for what's next and
[ARCHITECTURE.md](ARCHITECTURE.md) for why it's shaped this way.

## Use

**Playback:** Space play/pause, ←/→ step a frame, Home/End jump to the ends,
click a thumbnail to scrub, speed from the dropdown.

**Selecting frames:** click to select one, Shift-click to extend a range,
Ctrl-click to toggle one, Ctrl+A to select all, Esc to deselect.

**Editing:** Frames menu — Delete (Del), Duplicate (Ctrl+D, or the menu for a
count), Reverse, Trim to Selection; drag a frame along the timeline to reorder.
Timing menu — Set Frame Delay, Scale Speed. Image menu — Resize, Rotate, Flip, Crop.
Ctrl+Z / Ctrl+Shift+Z undo and redo everything. Ops that need input open a small
dialog generated from the operation itself.

**Cropping** is a gesture rather than a dialog (typing four numbers is poor UX):
press **C** or pick Image → Crop, drag a rectangle on the preview — a live box
shows the pixel size — and release to crop every frame to it. **Shift keeps the
box square.** Esc cancels. If you have already selected an area (below),
**Image → Crop to Selection** crops straight to it with no second drag.

**Painting** uses the tool palette in the panel beside the preview: pick
**Pencil** (B), **Eraser** (E), **Fill** (F), **Line** (L), **Rect** (R),
**Ellipse** (O) or **Eyedropper** (I), choose a colour and brush size, and drag
on the preview to paint the current frame. The mark previews as you drag and
commits as one undoable edit on release. **Hold Shift to constrain**: lines
snap to 45°, rectangles square up, ellipses become circles — press or release
it mid-drag and the preview follows. **Shift-click the Fill bucket** to fill
every pixel that matches the one you clicked, connected or not (the undo entry
says "Global Fill", because it is one). Hard-edged brushes for now; soft
brushes are on the roadmap.

**Zoom and pan:** Ctrl+`=` / Ctrl+`-` step the zoom, Ctrl+0 fits, Ctrl+1 is
1:1 — or **Ctrl+scroll on the preview to zoom at the cursor**, so the pixel
you are pointing at stays put while everything grows around it. Zoomed in, a
navigator map appears in the side panel: press or drag on it to pan, or use
**Shift+arrows** from the keyboard (bare arrows still step frames). View →
Pixel Grid (Ctrl+') overlays a grid once pixels are big enough to count.

**To erase, tick Erase** — there is no transparent colour to pick, and there
couldn't be: painting composites *over* the frame, so a transparent colour adds
nothing and the editor will tell you there was nothing to do. Erase is the other
half of the same operation, and the checkbox applies it to every painting tool
at once: the pencil erases, the bucket clears a whole region, and a filled
rectangle wipes an area. The Eraser tool is still there for when that's all you
want. The colour swatch greys out while Erase is on, because nothing is using it.

**Select, copy and paste:** press **S** or pick Select, drag a rectangle on the
preview (Shift for a square), and a marching-ants marquee marks it. **Ctrl+C** copies those pixels
from the frame you are on, **Ctrl+X** cuts them, **Ctrl+V** pastes them back
where they came from. Paste lands on every *selected* frame when the playhead is
standing inside the selection, and on just the current frame otherwise — which is
how you stamp one sprite across a whole animation in a single edit. The clipboard
survives opening another file, so you can copy out of one GIF and into another.
Esc clears the region before it clears the frame selection.

**To move a selection**, press **M** or pick Move and drag it. Nothing has
happened to the file yet — you're looking at a preview — so drag again to adjust,
nudge with the arrow keys, then **Enter** to drop it or **Esc** to put it back.
Ctrl+V works the same way: it floats the clipboard for you to place, and Enter
straight away pastes it exactly where it was copied from. A move shifts each
frame's own pixels, so with several frames selected you can nudge a sprite
across the whole animation in one go. Whatever you do next — another edit,
another frame, saving — drops the float first rather than losing it.

**Whole frames, via the Windows clipboard:** **Ctrl+Shift+C** copies the frame
you're on out to the system clipboard, so it can go straight into Paint, Discord
or anything else — as a PNG where transparency is understood, and as a plain
bitmap everywhere else. **Ctrl+Shift+V** goes the other way: whatever image is on
the clipboard *replaces* the current frame, transparency and all, which is how a
screenshot becomes a frame. It keeps the frame's own delay — you replaced the
picture, not the timing. The sizes have to match, and if they don't it says so
and names both rather than quietly scaling anything. Copying out is Windows-only
for now; pasting in works anywhere Pillow can read the clipboard.

**Saving:** Ctrl+S to save, Ctrl+Shift+S for Save As. One GIF quirk to know
about — the format merges identical *consecutive* frames and sums their
durations, so a frame you duplicated to "hold" it comes back as one longer
frame on reopen (playback is identical), and its palette carries exactly one
bit of transparency.

**When that matters, save a project instead**: choose "GIF Editor Lite
project" in Save As and you get a **`.gifproj`** — a zip of lossless PNG
frames plus a small manifest — that reopens exactly as authored: held
duplicates stay separate frames, partial transparency survives, timing comes
back untouched. Ctrl+S over an opened project just saves, no questions asked
(that's what a project is for); Ctrl+S over an opened GIF still warns before
re-encoding your original. Nothing about the file is secret: unzip a
`.gifproj` and you have exactly the folder Export Frames writes, manifest and
all, readable by Import Frames or anything else that eats PNGs.

## Run

```
pip install -e .
python -m giflite            # empty window
python -m giflite some.gif   # opens it
```

Needs Python 3.10+ and Tk. Tk ships with Python on Windows and macOS; on
Debian/Ubuntu it's `sudo apt install python3-tk`.

## Test

```
pip install -e ".[dev]"
pytest
```

The suite is headless by design — the interesting logic lives below the UI, so
none of it needs a display. The Tk layer has a separate manual smoke test:

```
python tests/smoke_tk.py --shot window.png
```

## Layout

```
giflite/core/   pure library — model, IO, playback clock, (soon) ops+history. Pillow only.
giflite/app/    controller, events, thumbnail cache. UI-agnostic; owns session state.
giflite/ui/tk/  the Tkinter frontend. The ONLY place a toolkit may be imported.
```

That last line is the whole modularity story, and `tests/test_boundaries.py`
enforces it both statically and at runtime.
