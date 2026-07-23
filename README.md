# GIF Editor Lite

A small, modular GIF editor in Python. Tkinter frontend, Pillow for pixels,
nothing else.

Status: **M2 — v1 lite is complete.** Opens a GIF, plays it back, and edits it:
select frames, delete, duplicate, reorder, reverse, trim, with undo/redo.
Saving is M3. See [TODO.md](TODO.md) for what's next and
[ARCHITECTURE.md](ARCHITECTURE.md) for why it's shaped this way.

## Use

**Playback:** Space play/pause, ←/→ step a frame, Home/End jump to the ends,
click a thumbnail to scrub, speed from the dropdown.

**Selecting frames:** click to select one, Shift-click to extend a range,
Ctrl-click to toggle one, Ctrl+A to select all, Esc to deselect.

**Editing** (Frames menu): Delete (Del), Duplicate (Ctrl+D), Reverse, Trim to
Selection, and drag a frame along the timeline to reorder. Ctrl+Z / Ctrl+Shift+Z
undo and redo everything.

Nothing saves yet — that's M3 — so edits live only in the session for now.

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
