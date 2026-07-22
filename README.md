# GIF Editor Lite

A small, modular GIF editor in Python. Tkinter frontend, Pillow for pixels,
nothing else.

Status: **M0** — opens a GIF and shows a frame. See [TODO.md](TODO.md) for
what's next and [ARCHITECTURE.md](ARCHITECTURE.md) for why it's shaped this way.

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
giflite/core/   pure library — model, IO, (soon) ops and history. Pillow only.
giflite/app/    controller and events. UI-agnostic; owns session state.
giflite/ui/tk/  the Tkinter frontend. The ONLY place a toolkit may be imported.
```

That last line is the whole modularity story, and `tests/test_boundaries.py`
enforces it both statically and at runtime.
