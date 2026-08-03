# GIF Editor Lite — Architecture

**Status:** draft for review, 2026-07-22 (rev 2, post design-review)
**Package:** `giflite` · **Runtime dep (v1):** Pillow · **First frontend:** Tkinter

---

## 1. Goals and non-goals

**Goals**

- A small desktop GIF editor that opens fast, does a few things well, and is pleasant to extend.
- A core that knows nothing about any UI toolkit, so a second frontend is a weekend, not a rewrite.
- Extension points that are boring to use: adding an operation should be one new file and zero edits elsewhere.

**Non-goals (v1)**

- Not a compositor. No layers, no keyframes, no tweening.
- Not a video editor. Video is an *import source* later, nothing more.
- No plugin sandboxing, no scripting console, no undo of undo. If it smells like an IDE, it's out.

**What "lite" means concretely:** v1 ships frame operations only — delete, duplicate, reorder, reverse, trim. Canvas ops, timing ops and text land later. Export is deliberately *not* in the first milestone (§14).

---

## 2. Design principles

1. **The core is a library.** `giflite.core` imports Pillow and stdlib. Nothing else. It could be pip-installed and driven from a script with no UI present.
2. **One import rule, mechanically checkable.** If a module imports a UI toolkit, it lives under `giflite/ui/tk/`. Nothing else may. This is the entire modularity story, and it's greppable (§11.4).
3. **Documents are immutable; operations are pure functions.** `apply(doc) -> doc`. This buys undo almost for free (§7) and makes every op testable without a window.
4. **The controller owns session state, not just the document.** Playhead, playback and zoom-free view policy live behind the façade. This is the single correction that makes §9 hold up — see the note there.
5. **Build the second thing, not the framework for the Nth thing.** One reader is a dict, not a registry. One dialog is a dialog, not a schema. Registries arrive when the second implementation does — which is a milestone away, not a hypothetical.
6. **Defer anything unproven.** Threading, project files, and lazy frame storage have hooks reasoned about and implementations deliberately absent.

---

## 3. The layer cake

```
┌───────────────────────────────────────────────────────────┐
│  ui/tk/        Tkinter widgets, timers, ImageTk, zoom/pan │  ← swappable
├───────────────────────────────────────────────────────────┤
│  app/          AppController · EventBus · frame cache     │  ← UI-agnostic
│                owns: document, selection, playhead, clock │     session state
├───────────────────────────────────────────────────────────┤
│  core/         Document · Frame · Operations · History    │  ← pure library
│                PlaybackClock · gif read/write             │     Pillow only
└───────────────────────────────────────────────────────────┘
         dependencies point downward, never up
```

The frontend never touches `core` for mutations — it goes through `AppController`. It *may* read `core` types, since it needs `Document` and `Frame` to render. Deliberate asymmetry: shared read-only model, funnelled writes.

---

## 4. Package layout

```
giflite/
├── __main__.py            # python -m giflite [file]
├── core/
│   ├── model.py           # Frame, Document, Selection
│   ├── history.py         # snapshot undo/redo stack
│   ├── playback.py        # PlaybackClock (pure timing logic)
│   ├── ops/
│   │   ├── registry.py    # @register_op, lookup, listing
│   │   └── frames.py      # delete, duplicate, move, reverse, trim
│   └── io/
│       ├── gif_read.py    # v1
│       └── gif_write.py   # M3
├── app/
│   ├── controller.py      # AppController — the frontend-facing API
│   ├── events.py          # tiny synchronous pub/sub
│   └── cache.py           # PIL-level thumbnail cache (no toolkit types)
├── ui/
│   ├── base.py            # Frontend ABC (contract only, ~4 lines)
│   └── tk/
│       ├── app.py         # root window, menu, wiring, timer
│       ├── canvas.py      # preview surface, owns zoom/pan
│       ├── timeline.py    # thumbnail strip, owns PhotoImage cache
│       └── dialogs.py     # the one or two dialogs M2 needs
└── tests/
    ├── fake_frontend.py   # headless event recorder (a fixture, not a product)
    └── ...
```

Realistically **~2,000 lines for M0–M2**, of which the virtualised timeline widget alone is 200–300. (Rev 1 guessed 1,200; that was wrong, and a tripwire set too low makes you cut the wrong things when you cross it.)

---

## 5. Core data model

```python
@dataclass(frozen=True, slots=True)
class Frame:
    image: Image.Image        # RGBA, coalesced, exactly canvas-sized
    duration_ms: int          # quantised to 10 ms, floor 20 ms (§12.3)

@dataclass(frozen=True, slots=True)
class Document:
    frames: tuple[Frame, ...]
    size: tuple[int, int]
    loop: int = 0             # 0 == forever
    meta: Mapping[str, Any] = EMPTY_MAP     # MappingProxyType, not dict
```

**Invariants** (`Document.validate()`, called in tests and debug builds): every `frame.image.size == doc.size`, every image is mode `RGBA`, `duration_ms >= 20`.

**`frozen=True` is a reminder, not a guarantee — and undo depends on the reminder being obeyed.** `Frame.image` is a mutable Pillow object shared by reference across every history snapshot. A single in-place `ImageDraw` or `paste` in some future operation silently rewrites history, and nothing in the type system will stop it. So, as a hard rule:

> **Operations never mutate `frame.image` in place. They allocate a new image and a new `Frame`.**

That rule is the actual load-bearing invariant behind §7. `meta` is a `MappingProxyType` for the same reason.

**Why fully coalesced RGBA.** GIF frames on disk are often partial deltas with disposal methods attached. Editing deltas is a nightmare — delete frame 3 and frames 4-onward render garbage. So: composite to full frames on import, edit full frames, re-optimise on export. Pillow already does the compositing (§12.2), so this costs one `.convert("RGBA")` per frame.

**The cost.** 640×480 × 120 frames = **147 MB** of RGBA (measured). Acceptable for a lite tool; not a permanent decision. Escalation path, in order: cap-and-warn above ~250 MB at load → store frames as PNG-compressed bytes behind a `FrameStore` interface → memory-map a scratch file. **Only the warning ships in v1.** Naming the rest now just means `Frame.image` is reached through the document rather than passed around loose, so a later swap stays contained.

**The empty state is a real state.** `python -m giflite` with no argument is the first thing anyone types. `AppController.doc` is `Document | None`; ops are unreachable while it's `None`; the frontend renders a drop-target empty state. A zero-frame `Document` is *not* how we represent "nothing loaded" — that ambiguity would leak into every op.

**Selection** lives in `core.model` because operations consume it, and holds no UI state:

```python
@dataclass(frozen=True, slots=True)
class Selection:
    indices: frozenset[int]
    anchor: int | None = None     # for shift-click range extension
```

---

## 6. Operations

```python
class Operation(Protocol):
    id: str                       # "frames.duplicate"
    label: str                    # "Duplicate Frames"
    accel: str | None = None      # "Ctrl+D" — frontends translate to their own syntax
    needs_selection: bool = True

    def apply(self, doc: Document, sel: Selection) -> OpResult: ...

@dataclass(frozen=True)
class OpResult:
    doc: Document
    selection: Selection          # what should be selected afterwards
```

**`OpResult` carries the new selection.** After deleting frames 3–5, "what is selected now?" has a right answer only the operation knows (clamp to whatever slid into position 3). After duplicating, it's "the new copies". Letting each op state this removes a whole class of stale-index bugs and any need for per-frame identity tracking.

**`accel` lives on the operation** so keymaps don't drift between frontends.

**No parameter schema yet.** Rev 1 specified a declarative four-type `Param` system plus a generic dialog generator. Counting what the v1 ops actually need — delete (none), reverse (none), trim (none, it trims to selection), move (drag gesture), duplicate (one integer) — that machinery existed to render **a single spinbox**. Cut. Hardcode the duplicate dialog in M2; introduce `Param` at M3, when writer options (dither mode, quality, loop count) make it genuinely plural and the generator pays for itself.

**Registration:**

```python
@register_op
class DuplicateFrames:
    id = "frames.duplicate"
    label = "Duplicate Frames"
    accel = "Ctrl+D"
```

Menus and keybindings read from `op_registry.all()`; menu grouping comes from the dotted id prefix (`frames.*` → Frames menu). The registry stores instances, constructed once at import.

**v1 op set (M2):** `frames.delete`, `frames.duplicate`, `frames.move`, `frames.reverse`, `frames.trim`.
**Added since:** `timing.set_delay`, `timing.scale_speed`, `canvas.resize`, `canvas.rotate`, `canvas.flip` (M4); `canvas.crop` (gesture-driven — see §11.3).
**Later:** `text.caption`.

---

## 7. History

```python
@dataclass(frozen=True, slots=True)
class Snapshot:
    doc: Document
    selection: Selection
    index: int                    # playhead
    label: str
```

A stack of `Snapshot`, `limit=64`. Viable only because frames are immutable and shared by reference: a snapshot after reordering 200 frames is 200 pointers, ~1.6 KB.

**Snapshots carry session state, not just the document.** Rev 1 stored `(doc, label)` — which contradicted §6 within two pages. Restoring frames while leaving the selection and playhead from the post-op world is wrong on the way back and out-of-range on the way forward. Selection and index are a handful of ints; store them.

**Dirty tracking** is a saved-marker: `History` records the stack position at the last successful save, and `dirty == (position != saved_position)`. Undoing back to the saved state correctly clears the asterisk, which a simple boolean flag can never do.

Pixel operations (crop, resize) are the memory exception — they allocate real images and history keeps the old ones alive. `limit=64` bounds it; when canvas ops land in M4, consider dropping to ~20 while a document exceeds 100 MB.

*Rejected:* the inverse-command pattern, where each op implements its own `undo()`. It's the textbook answer and it's the wrong trade here — more code in every operation, more places to be subtly wrong, in exchange for memory we aren't short of.

---

## 8. File formats

For v1, a module-level dict:

```python
READERS = {".gif": read_gif}      # Callable[[Path], Document]
WRITERS = {}                      # M3
```

Rev 1 specified `Reader`/`Writer` Protocols, a registry, and an `available()` capability check gating optional dependencies. With exactly one reader and no writers, that was a framework for a hypothetical. It becomes worth building at M3–M4, when the second and third formats arrive; the promotion is mechanical and touches one call site.

**What to preserve for that promotion**, because it's the part that actually matters: optional dependencies must never break startup. When video import lands, its module does its `imageio-ffmpeg` import inside a try/except and simply doesn't register itself on failure — so the app starts fine and quietly doesn't offer MP4. Dialog filter strings are generated from whatever registered, never hardcoded.

Ship order: `gif_read` (M0) → `gif_write` (M3) → image sequence (M4) → WebP/APNG/video (M5).

---

## 9. The frontend seam

The part worth getting right, since swapping frontends is an explicit requirement.

**The anti-pattern to avoid:** a widget abstraction layer — `giflite.ui.Button`, `giflite.ui.Slider` — that each backend implements. It's a large amount of work to build a worse version of every toolkit it wraps, and it always leaks.

**What we do instead:** the frontend is a *consumer of an observable façade*. It writes real Tk widgets, real Qt widgets, whatever it likes. What it may not do is own application state.

**The correction that makes this work:** rev 1's façade modelled the *document* but not the *session* — playhead, playback, undo affordances. All of that would have landed in the frontend by default, and both frontends would then independently reimplement clamp-on-delete, clamp-on-undo, timeline/canvas sync and play-pause semantics. That's precisely the duplication the seam exists to prevent. Session state belongs behind the façade; only the *timer tick* and *pixels-on-screen* belong to the frontend.

```python
class AppController:
    # ---- readable state -------------------------------------------------
    doc: Document | None
    selection: Selection
    index: int                     # playhead — clamped here, on every change
    playing: bool
    path: Path | None              # single source of truth (not on Document)
    dirty: bool                    # derived from History's saved-marker
    events: EventBus

    # ---- menu/toolbar state (so frontends don't re-derive it) -----------
    can_undo: bool;  undo_label: str
    can_redo: bool;  redo_label: str
    def can_run(self, op_id: str) -> bool      # honours needs_selection

    # ---- mutation surface -----------------------------------------------
    def run_op(self, op_id: str, **params) -> None
    def undo(self) -> None
    def redo(self) -> None
    def open(self, path: Path) -> None         # resets history, selection, index
    def save_as(self, path: Path) -> None      # marks history saved-position

    # ---- session ---------------------------------------------------------
    def set_selection(self, sel: Selection) -> None
    def seek(self, index: int) -> None
    def play(self) -> None
    def pause(self) -> None
    def set_speed(self, factor: float) -> None
    def tick(self, dt_ms: int) -> None         # frontend supplies dt; nothing else
    def frame_image(self, index: int) -> Image.Image     # full-res, from cache
```

**Events** — synchronous, no threads, no queue in v1:

| Event | Fired when | Frontend response |
|---|---|---|
| `doc_changed(doc, selection, index, reason)` | any op, open, undo/redo | redraw canvas + timeline, refresh menus |
| `selection_changed(sel)` | selection-only edits | restyle timeline items |
| `playhead_moved(i)` | playback tick or scrub | redraw canvas + timeline highlight |
| `playback_state(playing)` | play, pause, auto-stop at end | toggle the play/pause button |
| `title_changed(path, dirty)` | path or dirty changes | frontend formats the title string |
| `status(msg)` | informational | status bar |
| `error(exc)` | operation or IO failure | message box |

`playback_state` was added in M1. Playback can stop on its own — a non-looping
GIF reaching its last frame — so "are we playing?" is session state the
frontend must be told about, not something it can derive from its own button
clicks.

**Ordering is part of the contract, not an accident.** `run_op`, `undo`, `redo` and `open` emit **exactly one** `doc_changed`, carrying document, selection and playhead together. `selection_changed` fires only for selection-only edits. Without this rule a frontend can receive a new selection, restyle against the old document, and index past the end.

**Zoom and pan are entirely the frontend's.** `frame_image(index)` returns the full-resolution PIL image; the frontend scales it, owns fit-to-window semantics, and caches its own toolkit bitmaps. Rev 1 split this (`frame_image(index, zoom)`) which was the worst of both worlds — core doing the scaling while the frontend owned the zoom value, with the cache key silently needing to include it.

**A frontend is then:**

```python
class Frontend(ABC):
    @abstractmethod
    def run(self, controller: AppController) -> None: ...
```

`__main__.py` does `TkApp().run(controller)`. No frontend registry, no `--ui` switch, until there is a second frontend to switch to.

**Honest accounting of what a second frontend costs.** Not zero, and rev 1's "genuinely thin" oversold it. A Qt port would rewrite: widget layout, the timeline canvas, the timer, toolkit bitmap caching, file pickers, dialogs, and — from M3 — its own parameter-dialog builder, since `run_op(op_id, **params)` requires params already collected. Call it a weekend plus a couple hundred lines of glue. What it would *not* touch: operations, history, the document model, playback logic, IO, or any state management. That's the right split, and it's worth stating plainly rather than claiming the seam is free.

---

## 10. Playback

Timing logic is pure and lives in core; the controller owns the instance; the frontend owns only the timer.

```python
class PlaybackClock:
    def __init__(self, durations: Sequence[int], loop: int = 0, speed: float = 1.0): ...
    def set_durations(self, durations: Sequence[int]) -> None   # called on every doc_changed
    def tick(self, dt_ms: int) -> int      # -> frame index
    def reset(self) -> None
```

**`set_durations` is not optional.** Every M2 operation changes the frame count, so a clock that snapshots durations at construction is stale the moment the editor edits. The controller rebuilds it on `doc_changed` and clamps the playhead in the same step.

Tk drives it with `root.after(16, ...)` calling `controller.tick(dt)`; Qt would use `QTimer`; tests feed synthetic `dt` values. The timer runs **continuously** and `tick()` is a no-op while paused — one always-on timer has no start/stop race, and a 60fps idle callback costs nothing.

M1 ships **forward and loop**, plus a **speed** multiplier — speed turned out to be a single scale on `dt` and the controller already promised `set_speed`, so pulling it forward from M4 cost one line and a dropdown and makes the thing more fun to poke at. **Ping-pong** stays at M4: it needs a `Mode` enum and its own reversal logic at the loop boundary, which is real work M1's goal ("watch a GIF, drag the playhead") doesn't need.

The frontend caps `dt` at `MAX_TICK_MS` (250ms) before calling `tick()`: after a stall — window dragged, laptop asleep — real elapsed time can be seconds, and fast-forwarding through the whole animation in one frame is never what anyone wants.

---

## 11. Extension recipes

### 11.1 Add an operation

One file in `core/ops/`, one decorator. It appears in the menu, gets its accelerator, and participates in undo with no further work.

```python
@register_op
class ReverseFrames:
    id = "frames.reverse"
    label = "Reverse"
    needs_selection = False

    def apply(self, doc, sel):
        n = len(doc.frames)
        return OpResult(replace(doc, frames=tuple(reversed(doc.frames))),
                        Selection(frozenset(n - 1 - i for i in sel.indices)))
```

### 11.2 Add a file format

Write `read_x(path) -> Document` / `write_x(doc, path, **opts)`, add it to the dict. Guard optional imports with try/except at module import so a missing dependency means "format not offered", never a crash.

### 11.3 Add a frontend

Implement `Frontend.run(controller)`. Subscribe to the events in §9, call the methods in §9, own your own widgets, zoom and toolkit bitmaps. Touch nothing under `core/` or `app/` — if you need to, that's a genuine gap in the façade and the fix belongs in `AppController`.

**The gesture rule.** Drag-to-reorder has transient state mid-drag ("frame 7 would land at index 3") that is not in the document, is not undoable, and must still be rendered. Rather than build a provisional-transaction API for it:

> **Gestures render their own preview locally and commit exactly one operation on release.**

That single rule is what keeps drag-to-reorder, the crop rubber-band, trim handles and M4's speed slider from leaking a transaction system into the core. Its absence is what would make the seam leak. Crop is the rule's second concrete instance: `canvas.crop` is `in_menu = False` (like `frames.move`), the preview canvas draws the marquee itself and maps it to image pixels, and release commits exactly one op — the core never hears about the drag.

### 11.4 Enforce the boundary

```bash
grep -rEn "import (tkinter|PySide6|PyQt[56])|from (tkinter|PIL import ImageTk)" \
     giflite/ --include="*.py" | grep -v "giflite/ui/tk/"
# must print nothing
```

`ImageTk` is in that pattern deliberately: it pulls in tkinter, so a `PhotoImage` cache under `app/` would quietly bind the app layer to the toolkit while a naive `import tkinter` grep stayed green. Hence the split in §4 — PIL-level thumbnails in `app/cache.py`, toolkit bitmaps in `ui/tk/timeline.py`.

Wire it into a pre-commit hook or a one-line pytest. Modularity that isn't checked decays quietly.

---

## 12. Verified Pillow behaviour

Measured against Pillow 12.2.0, not recalled from memory. Each of these changed a decision.

**12.1 `ImageSequence.Iterator` yields the same object, mutated.** Six iterations returned one distinct object. Retaining frames without copying gives you N references to the last frame — a silent, baffling bug. `.convert("RGBA")` allocates a new image and doubles as our normalisation step, so the correct loop is also the natural one.

**12.2 Pillow coalesces on seek.** Frames arrive full-canvas with disposal already applied — the static background survived in every frame of a delta-optimised test GIF. **Do not hand-roll disposal handling.** The single biggest chunk of work this design gets to skip.

**12.3 Delays truncate to 10 ms.** GIF stores centiseconds and Pillow floors: `[33, 17, 5, 125]` → `[30, 10, 0, 120]`. The 5 ms frame became **0**, which most viewers then clamp to ~100 ms — a 20× timing error from a value the user typed. Therefore **quantise `duration_ms` in the model, not at export**, with a 20 ms floor, so the timeline never lies about timing.

**12.4 Identical consecutive frames merge on save, durations summed.** Three frames (two identical) saved and reloaded as **two**, durations `[200, 100]`. Frame count is *not* round-trip stable — and "duplicate a frame to hold it longer" is a v1 operation, so this is directly in the path of the feature set. The merge is visually equivalent, so the options are: accept it; disable the optimiser and pay in file size; or add a project file storing the authored document. **Recommendation: accept, mention once in the export dialog, revisit if it actually annoys you.** Open question — §13, risk 2.

**12.5 Memory.** 640×480 × 120 frames RGBA = 147 MB. Drives cap-and-warn in §5.

---

## 13. Risks and open questions

| # | Risk | Response |
|---|---|---|
| 1 | Memory on long or large GIFs | Cap-and-warn at load; `FrameStore` escalation path reserved (§5) |
| 2 | Frame count not round-trip stable (§12.4) | **Decided (M3): accept.** Merge is unconditional in Pillow's encoder — "disable the optimiser" was never viable (verified with `optimize=False` + no disposal: still merged). A held-duplicate folds to one longer frame, playback-identical. The UI mentions it on save (`count_merges`). A faithful **project-file / sidecar format** to preserve exact authored frames is deferred — see §18 |
| 3 | An op mutates `frame.image` in place and silently corrupts history | Hard rule in §5; assert-on-copy in tests; the one invariant to actually watch |
| 4 | Palette quality on export — 256 colours, dithering | Deferred to M3 with the writer; expose as an option, don't guess a default now |
| 5 | Tk timeline performance with 200+ thumbnails | One `Canvas` with image items, virtualised to the visible range. Never 200 `Label` widgets |
| 6 | Tk garbage-collects `PhotoImage` if you drop the reference — canvas goes blank | `ui/tk/timeline.py` holds strong refs, keyed by an explicit frame uid assigned at load, **never `id(image)`** (CPython reuses addresses after GC, which would serve wrong pixels) |
| 7 | Blocking load freezes the UI on large files | M0–M2: emit `status("Loading…")` + busy cursor before a blocking read. No `Progress` parameter, no `root.update()` re-entrancy. Revisit at M5 when video import makes it real |
| 8 | The frontend abstraction is unproven until a second frontend exists | Import rule (§11.4) + headless test frontend as cheap insurance; honest cost estimate in §9 |
| 9 | **My sandbox has no `tkinter`** — I can test core headlessly, but cannot run the Tk UI | Core gets real tests from me; the UI needs you at the keyboard. Argues for keeping logic out of the UI layer anyway |

---

## 14. Milestones

| | Scope | Done when |
|---|---|---|
| **M0** | Package skeleton, `Frame`/`Document`, `gif_read`, controller, Tk window | `python -m giflite some.gif` shows frame 0; bare `python -m giflite` shows an empty state ✅ |
| **M1** | Timeline strip, playback (forward + loop + speed), scrubbing | You can watch a GIF and drag the playhead ✅ |
| **M2** | Selection, the five frame ops, undo/redo | **"v1 lite" is complete** — the editor edits ✅ |
| **M3** | `gif_write`, Save / Save As | Edits can leave the building ✅ (Param schema deferred again — see below) |
| **M4** | Param schema, timing ops, canvas ops (resize/rotate/flip), ping-pong | Resize/rotate/flip/speed ✅ |
| **M4+** | Crop, as a preview rubber-band gesture (§11.3) | Draw a box on the image, release crops every frame ✅ (image-sequence IO still deferred) |
| **M5** | Video import, WebP/APNG export | Optional deps prove the try/except registration pattern |

M0–M2 is the real project; everything after is additive by construction. Export sitting at M3 is deliberate and matches "I don't care if save doesn't show up for a while" — but it does mean M0–M2 cannot produce output, so keep test GIFs handy and don't edit anything precious before M3.

---

## 15. Dependencies

- **Runtime, v1:** Pillow. That's the whole list.
- **Optional, M5:** `imageio-ffmpeg` for video import, registered via try/except.
- **Dev:** pytest.
- **Python:** target 3.10+ (`slots=True` needs it; the sandbox confirms 3.10 works). Staying 3.10-compatible costs nothing even if your Windows Python is newer.

Deliberately absent: numpy (Pillow suffices for v1 ops), any UI framework beyond stdlib Tk, any packaging tooling until there's something worth packaging.

---

## 16. Testing

The point of §2.1 is that the interesting code is testable without a display:

- **Operations** — build a `Document` of solid-colour frames, apply, assert frame count, order, and the returned `Selection`.
- **History** — apply a sequence, undo to the start, assert equality with the original; assert the saved-marker clears `dirty` correctly.
- **Immutability** — after every op, assert source frame images are byte-identical to before. This is the guard for risk 3 and the cheapest high-value test in the suite.
- **`PlaybackClock`** — feed synthetic `dt`, assert the index sequence for forward and loop-count.
- **Playhead clamping** — park on the last frame, delete it, assert the index is in range. Same for undo/redo and open.
- **IO round-trip** — write, read back, assert what the format actually guarantees (durations to 10 ms, frames modulo §12.4 merging). Encode the known lossiness in the assertions rather than pretending it isn't there.
- **Headless frontend** — `tests/fake_frontend.py` records events; open a file, run ops, assert the exact event sequence and ordering contract from §9. This is the regression test for the seam.

Untested by design: Tk widget layout. If a bug can only be caught by looking at the window, that logic is in the wrong layer.

---

## 17. Handover

- **`git init` is yours to run** — done; the repo exists.
- **Decisions made:** risk 2 → accept the merge, defer a faithful project file (§18). M0–M3 built and verified.

---

## 18. Deferred: a project / sidecar format

Matthew's call at M3: GIF save accepts the encoder's identical-frame merge (§12.4, risk 2), and a faithful format that preserves exact authored frames is a **future want, not now**. Captured here so it isn't lost.

The need: a GIF can't represent "two identical frames held separately" — it merges them — nor sub-10ms timing, nor >256 colours, nor partial alpha. Anyone doing real iterative editing eventually wants to save *the document as authored* and reopen it byte-identical, independent of what GIF can encode.

The shape, when it comes: a small container (a zip is the obvious choice — `.gifproj` or similar) holding each frame as lossless PNG plus a manifest (JSON) of durations, loop, and canvas size. It slots into the existing IO layer as one more `read_x`/`write_x` pair (§8, §11.2) — no new architecture, just a reader and writer that happen to be lossless. The `Document` model already carries everything such a format needs, which is the point of having kept it toolkit- and format-agnostic.

Not designed further until there's a felt need; the hook is that adding it changes nothing else.

---

## 19. Painting: the tool layer

Crop was the first *gesture* op, wired straight into the canvas. Painting turns that one-off into a concept, because "draw on the frame" is many tools, not one, and because a stroke stresses three things crop didn't: it isn't dialog- or param-shaped (a stroke is a live polyline, not a form), it allocates a fresh full-frame image every time (so undo memory is no longer nearly-free), and it needs interactive, stateful behaviour on the canvas.

**The split: Tools live in the frontend and commit Operations in the core.**

- A **Tool** is a frontend object. It owns interactive behaviour (press/drag/release on the preview, mapped to image pixels via the canvas's `_image_geom` — the same mapping crop introduced), its settings (brush size, colour, mode), and its cursor. Tools live under `ui/`, never in `core/`.
- On commit (release) a tool calls a **pure core op** with the finished stroke — `paint.stroke(index, points, size, colour)` / `paint.erase(index, points, size)` — which bakes pixels into one frame and returns a new document, exactly like every other op. Pure, headless-testable, undoable.

This keeps the seam the whole project rests on: the core never hears about mouse events; the frontend never implements pixel algorithms. It also names the thing crop couldn't — some tools commit *no op at all*. The **eyedropper** reads a pixel and sets the foreground colour; **pan/zoom** only moves the view. Those are tools, not operations, which is exactly why "Tool" has to be its own concept and not just "an Operation with a drag."

**The brush is a mask — and that is the whole future-proofing story.** A brush does not draw; it produces a coverage mask (an `L`-mode image, 0–255) along the stroke. The op then composites: *paint* alpha-composites the colour through the mask, *erase* subtracts the mask from the frame's alpha. A hard-edged brush is a 0/255 mask; a soft / anti-aliased brush (deferred — Matthew's call) is a feathered mask, and **nothing else changes** — same op, same compositing, same tool. Adding soft brushes is a new mask generator and no more.

**Which frame, which memory.** A stroke targets the *playhead* frame only (the tool passes `index`); it allocates a fresh-uid image for that one frame and shares the rest. Undo is one snapshot per *stroke* (the gesture rule already collapses a drag to a single op), bounded by the 64-snapshot cap — so a long session has a finite stroke-undo horizon. That is the deliberate lite default. The planned escalation (see TODO): make history **memory-aware** — track the bytes it holds and warn/report past a threshold (~128 MB) rather than silently trimming — the concrete form of risk 1's `FrameStore` note for the painting workload.

**Deferred by choice:** layers (painting is destructive, it bakes into the frame; layers would change the `Document` model and aren't "lite"), soft/AA brushes, fill and shape tools, and zoom/pan.

### 19.1 Crop folded in — one interaction mechanism

Crop predates this layer, so for one milestone the canvas carried two parallel mechanisms: a `_crop_mode` flag with its own press/drag/release/escape handlers, and the tool dispatch beside it. Both did the same job — map widget pixels to image pixels, render a local preview, commit one op on release — so crop is now simply a `CropTool`. The canvas has exactly one mouse path and one coordinate mapping.

What the fold bought beyond tidiness:

- **A latent painting bug fixed.** Crop cancelled itself on a window resize, because a rescaled image makes collected coordinates stale. Strokes had no such guard, so a resize mid-drag would have committed a stroke against stale geometry and painted in the wrong place. The canvas now cancels *whatever* gesture is in progress, so painting inherited the guard the moment it shared the path.
- **`is_gesturing` + `on_cancel(ctx)` on the `Tool` base.** These are the two hooks the canvas needs to intervene in a gesture it didn't start, and they're what make the resize guard and the two-stage Esc generic rather than crop-specific.
- **Two-stage Esc.** Mid-gesture, Esc abandons the gesture and keeps the tool (you meant to redraw the box, not to leave crop); otherwise it puts the tool away. With no tool active the canvas returns `None` so the global Esc still deselects frames — the widget bindtag runs before `bind_all`, so returning `"break"` unconditionally would swallow it.
- **A reusable rectangle overlay.** `show_rect_overlay` takes a box in *image* pixels (the tool never sees a widget coordinate) and labels it with the image-pixel size, which is the number that matters — not however many screen pixels it occupies at this fit scale. Any future rect-select or shape tool gets it free.

Crop is now sticky like every other tool rather than a one-shot armed mode: it stays selected after a commit, so a second crop is another drag. A stray click (zero area) commits nothing.

### 19.1.1 Two coordinate conventions, and why the tool picks

The canvas maps a cursor position to an image coordinate, and there is no single right answer — the two kinds of tool are asking different questions, so `Tool.coords` declares which:

- **`"pixel"` — which pixel is under the cursor** (brushes, eyedropper). This is `floor`, not `round`. A pixel spans `[i, i+1)`, so rounding sends everything past its midpoint to the neighbour and clicking the *visible centre* of a pixel paints the one to its right. Not clamped: the paint ops clip off-canvas points for free, and clamping would smear a stroke that runs off the edge along the border instead of letting it leave.
- **`"edge"` — the nearest pixel boundary** (crop). A crop box is described by the lines *between* pixels, so rounding is correct here, and the result clamps to `0..src` inclusive because the box must be a valid rectangle.

The matching subtlety on the way back out: `_image_to_display(..., center=True)` puts a brush preview on the middle of the pixel rather than its top-left corner.

Both errors are half a pixel, which is why they hid: at fit scale on a photo-sized GIF, half a pixel is sub-pixel and invisible. On pixel art blown up 30x it is 15 screen pixels, and the two errors compound in the same direction — the tool visibly draws up and to the left of the cursor. **Anything that converts between screen and image space has to be tested at high zoom, not just at 1:1.**

A related trap fixed alongside it: a `tk.Canvas` with no `scrollregion` will scroll itself over the bounding box of its items, after which widget coordinates no longer equal canvas coordinates and *every* gesture is offset by the scroll amount. `_redraw` now pins the region to the visible area (this is a viewport; panning, when it comes, will be an explicit transform) and `_dispatch` converts through `canvasx`/`canvasy` regardless.

**Recipe — add a tool.** A pure op in `core/ops/` for whatever it commits (skip if it commits nothing, like the eyedropper), plus a `Tool` in `ui/tk/tools.py` that maps the gesture and renders its own provisional preview. Same "one file, no edits elsewhere" shape as adding an op. `tools.py` imports no toolkit, so tools are tested headlessly against a fake `ToolContext` (`tests/test_tools.py`) — only the display mapping needs the Xvfb smoke.

### 19.2 Save is not a round trip

Writing a GIF rebuilds the palette and merges identical consecutive frames into longer holds (§12, §18) — both unconditional in Pillow. So an in-place save *destroys* the file that was opened, and Ctrl+S is one keystroke away at all times.

The guard is split along the seam. The controller reports the fact — `overwrites_source` (the current path is still the file we read, nothing has written to it yet) and `suggested_save_name` (`<stem>_edited.gif`, applied idempotently so saving twice never yields `a_edited_edited.gif`) — and the frontend owns the policy: a warning dialog whose default button is the safe one, offering overwrite / save elsewhere / cancel. The flag clears on the first successful write to any path, so the warning appears once per opened file rather than on every save.

Naming policy lives in the controller deliberately: a second frontend should inherit "don't clobber the original" rather than reinvent it.

The cheapest guard is not writing at all. `save_would_change_nothing` is true when there is a path and no unsaved edits — disk already holds this state, so the encode buys nothing and over an untouched source costs the original. `save()` acts on it rather than only reporting it: this one is protection, not policy, and a frontend that forgets shouldn't be able to lose someone's file. `save_as` is untouched — naming a destination is a different request, and a "save a copy" with no edits must still write. A skipped save reports success (the caller asked for disk to match the document; it does) and leaves `overwrites_source` alone, since an original we didn't touch is still an original.

### 19.3 Bare-key shortcuts yield to text fields

Every single-key shortcut in the frontend is also a text-editing key: Left/Right/Home/End move a caret, BackSpace and Delete remove a character, space types one, and `b`/`e`/`i`/`c` are letters. `bind_all` fires *after* the focused widget's class binding, so both happened — typing "12" then BackSpace in the brush Size box edited the number and deleted two frames.

`_bind_bare_key` wraps any unmodified shortcut so it stands down while `focus_is_text_field()` (the focused widget's `winfo_class()` is in `TEXT_ENTRY_CLASSES`). It returns `None` rather than `"break"`: the field has already had the keystroke by then, and other listeners — a dialog's own Escape, say — still deserve their turn. Modifier combinations (Ctrl+S and friends) are bound raw, since they don't collide with typing.

The rule for anything added later: no modifier, use `_bind_bare_key`.

---

## 20. Zoom and pan

Entirely the frontend's, exactly as §9 promised: `frame_image(index)` still hands
over full-resolution pixels and the controller never learns there is a zoom. No
core or `app/` file changed for this. The whole feature is `ui/tk/view.py` plus a
rewritten render path in `ui/tk/canvas.py`.

**`ViewTransform` imports no toolkit**, the same discipline `tools.py` follows,
so the arithmetic — which is where a feature like this actually goes wrong — is
covered headlessly in `tests/test_view.py`. The canvas owns drawing and owns no
scale arithmetic of its own.

### 20.1 The integration point is `_image_geom`

`geometry()` returns exactly the `(left, top, width, height)` tuple the preview
canvas already published, and `_display_to_image` / `_image_to_display` already
read every mapping through it. So the transform slots in *underneath* the
existing coordinate code and **`tools.py` needed no changes at all** — crop, the
brushes and the eyedropper work at 32x without knowing zoom exists. That is the
seam paying rent for the second time (the first was crop folding into tools).

Two properties of the tuple are load-bearing and easy to break later:

- It describes the **whole image**, not the visible slice. A stroke that runs
  off the edge has to keep making sense, and the paint ops clip for free.
- Its origin is **integers**. Scales on the ladder are exact, so quantising the
  origin to whole display pixels costs at most 1/scale of an image pixel of pan
  precision and buys pixel-exact block alignment when upscaling.

### 20.2 Two representation choices

**Scale is `None` for fit, not a number.** Fit has to *stay* fit across a window
resize and across canvas ops that change the image's dimensions. Baking the
current factor into a float silently unsticks it: the view holds 37.4% while the
window grows around it, which reads as a bug and is tedious to trace.

**Pan is the image point held at the viewport centre**, not a pixel offset. The
centre is invariant under zoom, so zooming holds your place for free, and
re-clamping after a crop is one clamp of a point into new bounds. A pixel offset
must be re-derived on every scale change — precisely the arithmetic §19.1
records going wrong twice.

Clamping happens after every mutation rather than lazily at read time. A stored
centre that is out of bounds but *renders* correctly is a trap: the next zoom-out
resolves it into a jump nobody asked for.

### 20.3 Rendering is crop-then-scale

The old path resized the entire source to fit. At 32x on a 500×500 GIF that is a
16000×16000 RGBA — about a gigabyte, and the checkerboard again behind it. So
the renderer now intersects the image rectangle with the viewport, maps back to
whole source pixels, and crops-then-scales only that. **Cost is bounded by the
window, not by the zoom** (asserted in the smoke test against the real bitmap,
not just in theory).

Three details that are not obvious and are each a visible bug if reversed:

- The crop lands on **whole source pixels**; the sub-pixel remainder is carried
  by *where the composed bitmap is placed*. Folding it into the resample instead
  is what makes upscaled pixel art shimmer as it moves.
- The checkerboard carries a **phase offset** so the pattern stays locked to the
  image rather than to whatever sub-rectangle is on screen. Without it the
  backing slides under a transparent GIF on every pan, which reads as the
  artwork moving rather than the view.
- At fit the visible rectangle is the whole image, so the fit path — including
  its cache keys — is what it was before zoom existed, and playback still runs
  off cached bitmaps.

### 20.4 A view change is the resize bug wearing a hat

`<Configure>` has cancelled in-progress gestures since crop existed: a resize
moves and rescales the image, so coordinates already collected now map somewhere
else. **A zoom or a pan is the identical staleness**, and it is reachable —
Ctrl+`-` fires happily mid-stroke. Every view change therefore routes through
one funnel (`PreviewCanvas._apply_view`) that cancels a pending gesture before
redrawing, rather than each entry point remembering to.

### 20.5 Policy

- **Ladder, not continuous**: 12.5% → 3200%, integers above 1:1. A whole-number
  scale maps each source pixel onto an exact block of screen pixels; a
  fractional one distributes rounding unevenly and shimmers.
- **Fit re-centres, Actual Size does not.** Fitting is a request to see
  everything, so holding a pan offset would defeat it; asking for 1:1 is a
  request to inspect what is under the middle.
- **An edit keeps your magnification**; only a genuinely new document resets to
  fit. Crop is the case that matters — you cropped in order to look closely, and
  being thrown back to fit at that moment is the wrong answer. Same open/close
  distinction the timeline already makes with its own `reset_view`.
- **No view gesture on the preview.** Matthew's call, and it buys something
  real: the wheel and the middle button stay entirely the tools', so no view
  gesture can ever land inside a stroke. Panning happens in the navigator
  instead (§21), which keeps that property while still giving you a drag.

---

## 21. The view panel and the navigator

### 21.1 Why the toolbar cluster failed

Zoom and pan controls were first built as a right-aligned cluster on the
toolbar. It doesn't fit: that row needs **1087px and gets 900**, so Tk silently
dropped the last three widgets off the end — no error, they simply weren't
there. Deleting the readout still leaves it ~57px short, and the window's 480px
minimum makes the idea hopeless rather than merely tight.

Worth recording because the failure is invisible: `pack` does not complain when
it runs out of room, and a screenshot is the only thing that catches it.
Anything added to that toolbar from here needs the same check.

### 21.2 The navigator is a better pan control than buttons were

- **It gives position, not just motion.** Buttons move the view but say nothing
  about where it is. At 3200% on an 82px GIF you can see about 28 pixels, with
  nothing to say *which* 28.
- **The control and the readout are the same object.** The rectangle shows the
  visible region and dragging it is the pan.
- **It keeps the preview's mouse entirely the tools'.** Dragging in the map is
  not a gesture on the preview canvas, so there is still no wheel binding and no
  middle-drag that could land inside a stroke. That was the whole appeal of
  buttons-only, and the map keeps it *and* gives you a drag.

Pointing is **absolute, not relative**: the position you press is the position
you get. A relative drag needs a grab offset and makes a plain click do nothing.

### 21.3 It reuses the transform rather than reimplementing it

`MiniMap` owns a second `ViewTransform`, locked to fit, and asks it the same two
questions the preview asks its own: where the image lands (`geometry`) and which
coordinate is under the cursor (`display_to_image`). It never converts anything
itself.

That drove a refactor worth having anyway: `image_to_display` /
`display_to_image` **moved out of the canvas and onto `ViewTransform`**. They
are pure functions of `geometry()` and the source size, they are the arithmetic
§19.1 records going wrong twice, and having two copies against two different
geometries is exactly how that returns on one side only. As a bonus it fixed a
weak test — `tests/test_view.py` had been testing *its own copies* of these
functions, which proves self-consistency and nothing else.

`ViewTransform` also gained `center_on(ix, iy)` (clamped, so dragging past the
edge of the map slides to the edge rather than flinging) and a configurable
`fit_pad`, since the preview's 16px of breathing room is a tenth of a 168px
panel.

**A guard stopped being hypothetical.** §20 kept the clamp in `_axis_origin`
despite it being unreachable, on the grounds that "the next pan input would set
a centre from raw deltas, and that is where it lands". The navigator is that
input. Removing `center_on`'s own clamp now leaves the *rendering* correct
because `_axis_origin` catches it — the stored centre is wrong but invisible,
which is precisely the trap §20.2 warns about. Both clamps are checked, at both
levels: headlessly for the stored centre, in the smoke for the geometry.

### 21.4 Panel policy

- **Shown only when zoomed in.** At fit the map's rectangle covers the whole
  image, which is to say it tells you nothing, so the strip would be pure cost.
- **Packed `before=canvas`**, so it takes its width off the preview rather than
  appearing below it.
- **Re-entrancy is real here.** Packing changes the canvas width, which fires
  `<Configure>` → redraw → back into the refresh. Visibility depends only on
  `is_fit`, which does not depend on the width, so the state settles after one
  bounce; the early return on "already in that state" is what stops it fighting
  the geometry manager.
- **The status line refreshes with the panel.** Pointing `on_view_change` at the
  narrower controls-only refresh was a real bug: showing or hiding the panel
  re-fits the canvas, and the status line kept a percentage the readout had
  already moved past. Both are derived from the same state, so both refresh
  together.

## 22. The pixel grid

A rule on every source-pixel boundary once you are zoomed in far enough for a
pixel to be a block rather than a dot. Asked for as "gridlines beyond 4x", which
is exactly what `Auto` means.

### 22.1 Three states, not a checkbox

"Should the grid be on" and "is the grid useful at this zoom" are different
questions, and answering both with one boolean makes the control lie in one
direction or the other — either it does nothing at fit and you assume it is
broken, or it fills the window with mush at 25% and you assume it is broken.

- **Off** — never.
- **Auto** (default) — from `GRID_AUTO_SCALE` (4x). The first rung at which a
  1px rule is a quarter of a cell rather than most of it.
- **Always** — from `GRID_MIN_SCALE` (2x).

`Always` still has a floor, and the floor is not a second opinion about what the
user asked for: at 1:1 adjacent rules touch, so the "grid" is a flat fill that
conveys nothing while costing one canvas item per source pixel on every redraw,
during playback, at 60fps. 2x is the last rung where cells are still cells.
`GRID_MAX_LINES` sits above both as a guard for a future non-ladder scale — it
is unreachable through the ladder today.

Two of the three modes can therefore be switched on and change nothing visible.
The frontend says so in the status line ("Auto (from 400%) - not shown at 200%").
That is not politeness: a setting that silently does nothing is the failure this
project has now hit from three directions (§21's dropped toolbar widgets, §21's
stale zoom readout, §19.2's Save that re-encoded for no gain).

The mode survives `reset()`. Scale and pan describe *this* document; whether you
like a grid is a fact about you, and clearing it on every open would be the same
mistake as clearing the active tool.

### 22.2 Where the rules come from

`ViewTransform.grid_lines()` generates them by calling `image_to_display`, the
same mapping every tool reads through — not by walking `left + i * scale`.

This is the entire reason the grid lives in `view.py` rather than in the canvas.
A grid derived from its own copy of the arithmetic can disagree with the mapping
that decides which pixel you clicked, and §19.1 is the record of what two
derivations of one coordinate cost: two half-pixel errors, invisible at 1:1 and
15 screen pixels wrong at 30x. A grid half a pixel off from the pixels it claims
to divide is worse than no grid, because you would trust it.

`center=False`, so the rules are pixel *boundaries* — the same coordinates the
crop marquee snaps to (`snap="edge"`). The grid and the crop box agree about
where a pixel ends because they ask the same function the same question.

The spans come from `visible_source_rect()`, which gives two properties for
free: the rules stop at the artwork instead of running out over the pasteboard,
and the count is bounded by the viewport rather than by the image — 32x on a
4000px image is the same handful of rules as 32x on a 40px one.

**Float dust, deliberately not rounded away.** `image_to_display` computes
`ix / sw * fw`, and that division leaves ~1e-14 of a screen pixel of error:
panned onto pixel 287 of a 400px source at 8x, a boundary comes back as
-3.999999999999993. Tk rounds it away when drawing. `grid_lines` does *not*
round, because rounding is how the grid would stop agreeing exactly with the
mapping; the test asserts `abs(v - round(v)) < 0.01`, which tells dust apart
from a genuine half-pixel.

### 22.3 Canvas items, not baked pixels

`PreviewCanvas._draw_grid` strokes plain line items after the bitmap. The
alternative — drawing the rules into the composed bitmap in `_compose` — was
rejected for three reasons, in ascending order of what they would have cost to
learn the hard way:

1. The bitmap cache stays pure, so toggling the grid invalidates no frame and
   nothing that reads pixels can read a grid line.
2. Baking would mean deriving the rule positions a *second* way, from the crop
   rectangle and the resample placement. See §22.2.
3. The item count is already viewport-bounded, so the thing baking would have
   bought (cheap redraws at high zoom) was never actually at risk.

Stippled (`gray50`) rather than solid: Tk canvas items have no alpha, and a 50%
dither is the only way to get a rule that reads as a guide over both a dark
sprite and a light one. A mid grey at full strength was legible *and louder than
the artwork*, which is the wrong way round for a guide. `GRID_COLOR` and
`GRID_STIPPLE` are two constants at the top of `canvas.py`.

### 22.4 It goes through the gesture funnel

A grid toggle changes nothing about where the image sits, so cancelling an
in-progress stroke for it looks like overkill. It isn't: `_draw` opens with
`delete("all")`, which takes the overlay items with it while `_overlay_items`
goes on holding their ids. A gesture that survives a redraw is a gesture whose
preview has silently disappeared — so the grid uses `_apply_view` like every
other view change, and gets §20's staleness guarantee unchanged.

### 22.5 Consequence worth knowing

Fit is not a synonym for "zoomed out". A 160x80 GIF fits at 552% in a 900px
window, so with `Auto` the grid is on the moment the file opens. That is the
rule working as specified — you are, in fact, past 4x — but it is a surprise
worth naming, and `GRID_AUTO_SCALE` is the one constant that changes it.

## 23. Fill and shapes, and the palette moving house

Four new tools -- flood fill, line, rectangle, ellipse -- plus the layout change
they forced.

### 23.1 The mask bet paid

§19 claimed "the brush is a mask", meaning a soft brush later would swap the
stamp and change nothing above it. Fill and shapes are the first real test of
that claim from a different direction, and they cost two mask *generators* and
nothing else:

    _brush_mask   points + diameter    -> L        (§19)
    _fill_mask    seed + tolerance     -> L        new
    _shape_mask   kind + box + width   -> L        new
                                          |
                                    _apply_mask -> OpResult

`_apply_stroke` became a thin caller of a new `_apply_mask`, which is now the
single commit path for every painting op. Alpha compositing, immutability, fresh
uids, declining a no-op and the playhead rule are therefore stated once and
inherited three times, rather than reimplemented and got subtly wrong in one of
them.

`paint.shape` is one op with a `kind`, not three ops, for the same reason Pencil
and Eraser are one `StrokeTool`: they differ by a single `ImageDraw` call. An
unknown kind declines rather than defaulting, so a typo in a future tool
surfaces as "nothing to do" instead of quietly drawing a rectangle.

### 23.2 Fill: two stages, one in C and one in Python

`_fill_mask` answers two different questions separately.

*Which pixels match?* is a colour question, answered by `_match_mask` in whole-
image Pillow operations -- `ImageChops.difference` plus a 256-entry LUT, both C.
Tolerance is Chebyshev (the largest single-channel difference), not Euclidean:
"tolerance 8" then means "no channel differs by more than 8", which is a
sentence a user can hold in their head rather than a radius in a 4-space they
cannot picture.

*Which of those are reachable?* is a connectivity question, answered by
`ImageDraw.floodfill` walking the match mask. Marking the reachable run with 128
means the pixels left at 255 are exactly the matching-but-unreachable ones, and
a LUT separates them -- which is the whole of "contiguous, not global", and
means a global variant would be this function without the flood.

Writing one combined flood in Python would put a per-pixel colour comparison
inside a per-pixel walk. As it stands the walk is the only Python, and it costs
~1µs/pixel: instant at GIF sizes, ~1.1s for a whole-canvas fill at 1000x1000.
Acceptable for "lite"; noted in TODO if it ever bites.

### 23.3 Pixel-inclusive, unlike crop

A shape addresses the pixels it covers; a crop box addresses the boundaries
*between* pixels. So `ShapeTool.coords = "pixel"` while `CropTool.coords =
"edge"`, and a rectangle dragged from pixel 2 to pixel 7 covers both ends.

That convention has to be undone in one place. `preview_rect` draws corner-to-
corner through `image_to_display(..., center=False)`, which returns a pixel's
top-left corner -- correct for crop, whose numbers are already boundaries. For a
shape the far edge must be pushed out by one, or the marquee is a pixel short on
each far side and the committed shape does not match the box the user drew.
`ShapeTool.preview_box` is that adjustment, and it is a static method so it can
be tested without a gesture.

Fill is the one committing tool that fires on *press*. There is nothing to
preview -- the affected region depends on pixels the frontend would have to
reimplement the op to know -- so waiting for the release would add latency and
change nothing. `is_gesturing` stays False, which also means Esc puts the tool
away rather than cancelling a gesture that was never in progress.

### 23.4 The toolbar became a panel

Five tools fitted across the top. Nine would not, and §21 records precisely what
this window does when a row runs out of width: `pack` drops widgets off the end
with no error, and only a screenshot catches it. The 480px minimum width makes
that permanent rather than merely tight.

So the palette moved into the strip beside the preview, which stops being
view-only and becomes the side panel: **tools and settings always, view section
conditional**. The top row is gone entirely, which gives the preview back ~34px
of height.

Two consequences worth stating:

- **Tools are a two-column grid.** Nine stacked radios are ~190px of panel
  height; two columns are ~105px, and that difference is what keeps the view
  section fitting underneath at the default window size.
- **The preview's width is now constant.** It no longer widens when you return
  to fit. That is a deliberate trade: the canvas jumping 200px sideways every
  time you crossed fit would be worse than the width it costs.

### 23.5 The same failure, on the other axis

Turning a row into a column turns "runs out of width" into "runs out of height",
and at the 480x400 minimum that is not hypothetical: measured, the panel wanted
412px and had 238. What `pack` did with it was instructive -- the map survived
and the zoom row silently vanished. Half a navigator, no error.

`_view_section_fits` makes that a decision instead. If the panel is too short
for the whole view section, none of it is shown: half a navigator is worse than
none, because the half that remains looks like it works. The tools above it are
never at risk, both because they are packed first and because the smoke test
asserts every palette entry is mapped at the minimum window size.

## 24. Per-frame timing, made reachable

`timing.set_delay` has existed since M4, behind a menu item and a dialog. This
adds no new op: it adds the fast path, because retiming a frame is the *correct*
way to hold a pose and duplicating frames to do it bloats the file and
multiplies the work of every later edit. A control for the right technique
should be at least as reachable as the button for the wrong one.

Three surfaces, one underlying op:

- a delay box in the side panel, in milliseconds;
- the frame's own delay in the status line;
- each frame's delay under its thumbnail in the timeline.

### 24.1 Scope: the selection, but only one you are standing in

The controller answers "which frames would a delay edit touch" via
`delay_targets`, and the rule has two halves, each fixing a distinct trap.

**Never everything.** The menu op treats "no selection" as "the whole
animation", which is right for a deliberate action with a dialog in front of it.
An inline box beside the frame counter reads as "this frame", and having it
quietly retime all forty is the kind of surprise that costs an afternoon. Same
op, different affordance, different default.

**And not a selection you have walked away from.** Opening a file selects frame
0, and `seek`/`step` deliberately leave the selection alone -- so arrowing to
frame 3 leaves frame 0 selected. A box keyed on the selection alone would report
and edit frame 0's delay while the preview and the status line both showed frame
3. So the selection counts only when the playhead is inside it. A selection you
are standing in is what you are working on; one you have stepped away from is
not.

`target_delay_ms` returns `None` when the targets disagree, and the frontend
renders that as an empty box. "Mixed" has to be representable: a single number
would be wrong for most of them, and blank is the only display that isn't a lie.
The label carries the count -- "Frame delay (4 frames)" -- so the box says what
it would do before you type.

`set_frame_delay` scopes the selection to `delay_targets` and then runs the
existing op, so quantisation, the 20ms floor, validation, history and events all
behave exactly as they do from the menu. A decline restores the previous
selection, because a no-op must not leave frames selected that the user never
selected.

### 24.2 A decline the timing ops never had

Every other op family returns the *same* document when nothing changes, so
`run_op` reports "nothing to do" rather than pushing an identity snapshot onto
undo. The two timing ops were the exception -- `replace(doc, frames=...)`
unconditionally -- and it went unnoticed because the only way in was a dialog,
and nobody opens a dialog to retype the value already in it.

An inline box asks the question on every commit, so it surfaced immediately.
`_retimed` now applies the convention to both ops. It compares *results*, not
requests: durations are quantised on the way in, so "typed 103ms, already 100ms"
is a no-op that a request-level check would miss.

The frontend has its own guard on top, skipping the call entirely when the value
is unchanged -- because reaching the op still costs a "nothing to do" status
message, which is noise for a box the user merely tabbed past. The two guards
are belt and braces, and that has a testing consequence: see §24.4.

### 24.3 Committing, and what the box then shows

Enter, focus-out, or the spinbox arrows -- never per keystroke, which would push
"1", "10", "100" onto undo as three edits. Garbage or an empty box is ignored
and the real value put back, rather than being read as zero.

After a commit the box is refreshed *from state*, not from what was typed. The
op quantises to 10ms and floors at `MIN_DURATION_MS`, so typing 333 leaves 330
and typing 1 leaves 20. Showing the result is the only honest option, and it
makes the quantisation discoverable rather than mysterious.

`default_params` now seeds the *dialog* from the same reality (the long-deferred
polish item). Where the targets disagree the shortest wins -- not the first, not
the average: retiming a mixed run is nearly always about slowing part of it
down, and the minimum is a delay some frame actually has rather than a number
invented for the box.

### 24.4 Two tests that had no teeth

Worth recording because the pattern keeps recurring in this project.

The obvious check for §24.2 was "committing an unchanged value doesn't change
`undo_label`". It passed against a build with the decline removed: the preceding
edit was *also* a delay edit, so an identity entry left the label reading "Set
Frame Delay" either way. Making the neighbouring label differ still didn't help
-- with both guards in place, no undo entry appears whether or not either guard
is present, so the assertion could not distinguish any of the three worlds.

What the display test can actually observe is the frontend guard, via the status
message that reaching the op would produce. That is what it asserts now, and the
op-level decline is covered headlessly instead -- the right layer for it. Both
were verified by mutation rather than assumed.

### 24.5 Timeline labels

Drawn inside `_draw_slot`, so they inherit the virtualisation for free: only
slots in view cost anything, which is the property that lets a 200-frame GIF
draw like a 20-frame one (risk 4). Milliseconds below a second and seconds above
it, because "1500" sitting next to "80" invites reading the strip as though
every number were the same magnitude.

This is the surface that makes uneven timing *findable*. A number in a box tells
you about one frame; a row of numbers tells you which frame is wrong.

## 25. Image-sequence IO, and the registry it forced

Import a folder of stills as frames; export frames back out as numbered PNGs.

### 25.1 Why a folder is what broke the dict

`READERS = {".gif": read_gif}` was always going to become a registry "when the
second and third formats arrive" (§8). But more *file* formats would never have
forced it -- `.webp` and `.apng` are two more keys. A folder is different in
kind: it has no suffix, so `READERS[path.suffix]` has nothing to look up, and no
number of extra keys fixes that.

What survives untouched is the reader *signature*. A directory is still a
`Path`, so `Path -> Document` holds. Only **dispatch** changes, from "index by
extension" to "ask each format whether it claims this path".

A `Format` is a record: id, label, extensions, `is_folder`, read, write,
`read_params`, `available`. Two details are deliberate:

- **`is_folder` is a field, not an inference from empty extensions.** Inferring
  it would make "a format with no extensions" mean "folder" by accident, and the
  next unusually-shaped format would inherit that meaning by surprise.
- **`available` is a callable, not a bool.** The guarantee carried over from the
  dict -- a format whose optional dependency is missing must not break startup --
  requires checking when it matters, not at import time. M5's video import is the
  first real customer; the filters already honour it and it is tested with a
  format that reports itself unavailable.

`format_for` asks file formats first, so a not-yet-existing `out.gif` is a file
while a not-yet-existing `frames` is a folder. Without that ordering the suffix
someone typed would mean nothing.

### 25.2 The three questions a single-file reader never asks

**Order.** A directory listing has none worth trusting, and lexicographic order
puts `frame10.png` between `frame1.png` and `frame2.png`. This is not a corner
case: it is every sequence longer than nine frames, and it looks perfect on a
small test folder. `_natural_key` reads digit runs as numbers. Export writes
zero-padded names so that *other* tools -- file managers, shells, scripts, all
of which sort naively -- agree with us on the way back.

**Size.** Stills need not agree; a `Document` has exactly one canvas. Frames are
padded onto the union of their sizes, placed top-left, never scaled. Scaling
would silently resample the user's pixels to make an import succeed, which is
the wrong trade for an editor aimed at pixel art. Top-left rather than centred
because a mismatched sequence is nearly always one where something grew at the
right or bottom, and centring would shift *every* frame -- including the ones
that were already the right size.

**Timing.** Stills carry none. The manifest supplies it if there is one;
otherwise the reader is told, via `Format.read_params`.

### 25.3 Import is not open; export is not save

Each distinction is one field wide and both matter.

An opened file is a document's *home*: Save writes back to it. An imported
folder is a *source* -- the document it produces has never been saved anywhere,
and pointing `_path` at the folder would aim Ctrl+S at writing a GIF over
somebody's PNGs. So import leaves `_path` as None and Save falls through to Save
As, which is what "no file yet" already means everywhere else.

`_source_label` carries the folder name for the title bar, because "Untitled"
after importing a named folder throws away the only context the user has.

Export leaves `_path`, the dirty flag and the history alone. Writing a copy of
your frames somewhere is not the claim "this document now lives here", and
conflating them would clear the unsaved marker on a document that still has no
file.

### 25.4 The manifest is the project format, unzipped

§18 deferred a `.gifproj`: "each frame as a lossless PNG plus a JSON manifest,
in a container". An exported folder is exactly that minus the zip, so the
manifest schema is designed once, here, and versioned from the first line of it
existing. A reader must refuse a version it doesn't know rather than guess --
half-understanding a manifest produces a document with the wrong timing, which
is the sort of thing noticed three edits later.

Filenames are stored per frame rather than implied by position, so a container
can name its members however it likes and a hand-edited folder can reorder
frames without renaming files.

This also delivers, for folders, the property GIF cannot offer: identical
consecutive frames stay separate rather than being merged into one longer hold
(§12.4, risk 2), and durations round-trip exactly as authored.

`Format.read_params` is what lets the frontend generate the import options
dialog without knowing what format it is talking to. `ask_values` was split out
of `ask_params` for it: operations had stopped being the only things with
parameters, and the `Param` schema was always general enough -- only the
function's signature was not.

### 25.5 A correction worth keeping

Adding Import and Export to the File menu broke `_refresh_file_menu`, which
configured entries by hardcoded index `(2, 3, 5)`. It is now by label.

The comment justifying that fix originally claimed the old code would fail
*silently* -- the wrong three items greying out with no error. A mutation run
disproved it: index 3 had become a separator, and a separator has no `-state`,
so Tk raises `TclError` the moment the File menu opens. Loud, not silent.

Kept as written-down history because the reasoning still lands, just
differently: the loudness was luck. Had the insertion landed one entry earlier,
all three indices would have hit real entries and it would have been exactly as
silent as first claimed.

---

## 26. Select, copy, cut, paste

Slice 1 of two. This adds the rectangular selection, the clipboard, and paste
*in place*; making the paste floating and draggable is slice 2, and is a genuine
interaction mode with its own rules, which is why it is not here.

### 26.1 A region is Selection's other axis

`Selection` answers "which frames". `Region` answers "which part of the canvas".
They are deliberately independent, because the thing this was asked for -- copy a
sprite out of one frame and stamp it into twenty -- is one region and twenty-one
frames, and neither number constrains the other.

`Region` lives in `core/model.py` beside `Selection`, in **edge coordinates**:
`x`/`y` are lines between pixels and `width`/`height` count pixels, which makes a
region exactly the argument list `canvas.crop` already takes. That is not tidiness
for its own sake. It means `SelectTool` declares `coords = "edge"` for the same
reason `CropTool` does (§19.1.1), the marquee needs no `preview_box`-style
correction (§23.3), the same drag produces the same rectangle through both tools
-- pinned by a test -- and a future Crop-to-Selection is a call, not a second
derivation of the same arithmetic.

The one place the two rectangle conventions still meet is `_region_mask`, which
converts an edge box to the pixel-inclusive one `_shape_mask` wants. Named, and
alone, because §19.1 is the record of what two derivations of one coordinate cost.

### 26.2 It is session state, so it lives in the controller

The region and the clipboard sit in `AppController` beside the playhead, not in
the frontend and not in `Document`. Three consequences that are the whole reason:

- **Not undoable.** Undoing a paste gives back your pixels; it does not
  rearrange what you had selected. A region is not part of the document's
  history, and `undo` restoring one would be a surprise with no upside.
- **Re-clamped, not dropped, when the canvas changes shape.** A region names
  pixels, so a frame-count change is none of its business -- unlike the frame
  selection, which is clamped for exactly that -- but a crop, resize or rotate
  can move the canvas out from under it. That happens in `_emit_doc_changed`,
  the same funnel the selection clamp uses, so there is one place to get right
  rather than one per op. It is trimmed while it still overlaps, because after a
  crop the part you were working on is usually still on screen, and dropped only
  when nothing is left. `REGION_CHANGED` is emitted *after* `DOC_CHANGED`: a
  listener told the region shrank while it still held the old document would
  draw the marquee against a canvas that no longer exists.
- **The clipboard outlives the document.** `open` clears the region -- it names
  pixels in the file it left with -- and deliberately does *not* clear the
  clipboard, because copying a sprite out of one GIF and stamping it into
  another is a thing people do, and a clipboard that emptied whenever the
  document changed is one nobody could plan around.

### 26.3 The canvas's first overlay that survives a redraw

Every overlay before this belonged to a gesture, so `_draw`'s `delete("all")`
taking it was correct and the tool redrew on the next mouse event. §20.4 even
turns that into a safety property: any view change cancels a gesture, partly
because a gesture that survived a redraw is one whose preview has silently
disappeared.

A region has no next mouse event. It persists while you scrub, play, zoom, pan
and paint. So it is drawn from state inside `_draw`, next to the grid, and is
deliberately not in `_overlay_items` -- `clear_overlay()` must not touch it. The
two mechanisms now coexist and `SelectTool.on_release` uses both: it clears the
provisional marquee and hands over a region, and for an instant there is neither
and then there is one.

It is drawn as a dark solid rectangle under a light dashed one: marching ants
standing still. The alternation is what makes the outline legible over both a
dark sprite and a light one; the animation was never the part doing that work.
A different colour from the gesture marquee on purpose -- a rectangle you are
dragging and a rectangle you have committed are different things, and a preview
that looked identical to a selection would leave you unable to tell whether
releasing the mouse had done anything.

### 26.4 Paste made the playhead rule show its age

`run_op` sent the playhead to `result.selection.first`. That is right for the
ops it was written for: after moving or duplicating frames you want to be
looking at what moved. But it is a rule about *frames that shifted*, and an op
that edits pixels in place shifts nothing. The painting ops have worked around
it since M4 by returning `Selection.single(index)` -- which keeps the playhead
still at the cost of throwing away the user's frame selection.

That trade is invisible while an op edits one frame and unacceptable once one
edits many. Pasting into frames 0-20 while standing on frame 7 must not yank the
playhead to frame 0, and must not collapse the selection to one frame either --
otherwise a second paste lands on one frame instead of twenty-one.

So `OpResult` gained an optional `index`: an op may say where the playhead
belongs, and `None` means "wherever the existing rule puts it". Optional because
every existing op is correct without it. It is the smallest change that makes
the multi-frame case expressible rather than a special case in the controller.

### 26.5 Scope: cut is one frame, paste is many

Matthew's call, and the asymmetry is deliberate.

**Paste** targets `frame_targets` -- the property the delay box already used,
renamed from `delay_targets` because a scope rule with two callers is a policy
(§24.1): the selection, but only one the playhead is standing in, otherwise just
the playhead frame. Stamping across an animation is the case this was asked for,
and the qualification is exactly as necessary here as it was there: a selection
you have arrowed away from is not what you are working on.

**Cut** clears the playhead frame alone. Copy can only read the frame you are
looking at, so cut clears the frame it read. The two are not symmetric because
the risks are not: paste is additive and was explicitly asked for across frames,
while clearing frames you cannot see, on the strength of a selection you may
have made for another reason, is destruction that is undoable and unnoticeable
at the same time.

The op is `paint.cut` and labelled "Cut" rather than "Clear", because what it
does is only half of what happened -- the other half is the clipboard, which is
session state and not the op's business. "Undo Clear" after pressing Cut would
be an accurate description of an implementation detail and a wrong description
of the user's action.

### 26.6 Paste is one more mask generator, and it found a bug

The claim in §23.1 held again: paste is the pasted image's own alpha as the
mask, which is what makes the transparent corners of a copied sprite land as
*nothing* rather than as a rectangular bite out of the frame. The one thing it
adds is a colour that varies per pixel, which is a parameter on `_composite`,
not a second pipeline.

What it also did was falsify §19's other promise -- that a soft or
anti-aliased brush would be "a feathered mask and nothing else changes".

`_composite` built the stroke with `Image.paste(colour, mask)`. `paste` with a
mask *blends*: `dst*(1-m) + src*m`, on every channel. Pasting an opaque colour
into a transparent layer through a mask of 128 therefore yields
`(r/2, g/2, b/2, 128)` -- premultiplied colour sitting in a straight-alpha image
-- and `alpha_composite` then applies the alpha a second time. Black at half
coverage over white came out at about 64 instead of 128.

It had been correct by accident for as long as it existed, because every mask in
the codebase was hard: 0 or 255, and at 255 the blend is an exact copy. A pasted
sprite with a partly transparent edge is the first soft mask this project has
ever produced, which is why it surfaced now and not when the fill bucket landed.

The stroke's alpha is now *set* rather than blended in, and a translucent colour
multiplies with the mask rather than one of them winning. Nothing about hard
masks changes, which the existing 502 tests confirm by not moving.

### 26.7 Ctrl+C is a text-editing keystroke

§19.3 was written as "bare keys yield to text fields", on the theory that only
unmodified keys collide with typing. Cut/copy/paste disproved it: `bind_all`
fires after the focused widget's class binding, so an unguarded `<Control-c>`
would copy the number out of the Size spinbox *and* replace the image clipboard
with a rectangle of canvas -- silently, since nothing on screen reports what the
clipboard holds.

`_bind_bare_key` is now `_bind_guarded_key`, and the rule it implements is the
one that was always true: a keystroke yields when the focused widget has a
better claim on it. Whether it carries a modifier is not the test.

### 26.8 Esc is a four-stage ladder

Abandon the gesture, put the tool away (both the canvas's, since it owns Esc
while a tool is active), clear the region, clear the frame selection. Ordered by
how recent and how transient each thing is, so each press undoes the most recent
commitment -- the only ordering nobody has to memorise.

Region before frames deliberately: the region is the thing visible on the canvas
you are looking at, so it is what Esc appears to be aimed at, and a frame
selection that quietly vanished first would look like Esc had done nothing.

`Delete` is *not* rerouted. With a region selected it still deletes frames, not
pixels. That is arguably a footgun and is noted in TODO rather than fixed on a
hunch: changing what an existing destructive shortcut does is not a change to
make while adding a feature.

---

## 27. Erase mode

Prompted by a question with a wrong premise, which is the useful kind: *what
colour do I fill with to get transparent?*

### 27.1 There is no such colour, and there could not be

Painting alpha-composites the colour *through* the mask. A fully transparent
colour composited over a frame contributes nothing, so the op declines and
reports "nothing to do" -- which is correct, and completely unhelpful if you
were expecting a hole. Alpha is removed by `ImageChops.subtract` on the frame's
own alpha channel, which is the `"erase"` branch of `_composite` and has been
there since M4 behind exactly one tool.

So the answer is not a colour, it is the other branch of the same operation.
`test_no_colour_can_erase` pins the premise, because "why doesn't a transparent
colour work" is a question someone will ask again.

### 27.2 One flag, not an erase variant of each tool

`erase_mode` on the `ToolContext`, driven by a checkbox beside `Fill`. It
reaches the tools two different ways, and the difference is not an
inconsistency:

- **Strokes swap the op.** Pencil and Eraser have been two ops since M4, so a
  pencil in erase mode is not a parameterised stroke, it is `paint.erase`.
- **Fill and shapes take a `mode` param**, because they have one op each and the
  mask does not care what happens to it. `_fill_mask` and `_shape_mask` are
  untouched: which pixels and what happens to them were already separate
  questions, and this is the first thing to ask the second one differently.

`StrokeTool._erasing` is `self.erase or ctx.erase_mode`, not the flag alone.
Reading the flag straight would turn the *Eraser* into a pencil whenever the box
happened to be off, which is the sort of inversion that looks like a
double-negative bug for ten minutes before it looks like a design error.

The Eraser stays in the palette rather than being absorbed. Erasing is common
enough to deserve one click, and a tool you can see is selected is a better
default than a checkbox you have to remember to untick.

### 27.3 The undo menu had to be told

`paint.fill` filling and `paint.fill` clearing are correctly one op, and "Undo
Fill" after removing pixels describes the implementation while misdescribing the
action -- in the one place a user looks to find out what they just did.

`OpResult` could not carry it: the label is wanted whether the op applies or
declines. So the *op* names the run, through an optional `label_for(**params)`
hook resolved by `op_label`. Same shape as the `default_params` hook that
already existed, and for the same reason: the general case is a constant, so the
exception should cost one method on the op that has it rather than a mechanism
every op pays for.

`run_op` resolves it once, up front, so the undo entry and both status messages
cannot disagree about what was attempted.

### 27.4 Two mutations that were not caught, and what they meant

The mutation run over this section found two gaps, and they failed differently.

A `_mode()` helper normalised anything that was not `"erase"` to `"paint"`
before handing it on. Replacing its body with `return mode` broke nothing --
because `_composite` compares `mode == "erase"` and had already decided it. The
helper was defence in front of a wall. Deleted, and the reasoning moved to the
comparison that actually makes the choice; `_composite` treating any non-`paint`
value as erase *is* caught, which is the check that was wanted all along.

The second was a real hole. Every label test called `op_label` directly, so
`run_op` falling back to `op.label` passed everything. `op_label` being correct
and `run_op` *using* it are two claims, and only one of them was tested.
`TestUndoLabels` in `tests/test_controller_region.py` tests the second.

### 27.5 The swatch cannot show this on its own

Erase mode makes the foreground colour irrelevant, so the swatch is disabled --
and a `tk.Button` whose background *is* the colour looks identical disabled,
because the explicit `bg` wins over the disabled style. Greying the "Colour"
label beside it is the half you can actually see. Checked in the smoke test in
both directions, because "we disabled it" and "it looks disabled" are, once
again, two claims.

---

## 28. The floating edit

Slice 2. Asked for as "can I move a selection?", which is the same feature as
"make the paste draggable" approached from the other side — and that is the
design: **one floating layer, two ways to produce it.** A move lifts pixels off
the current frame, a paste brings them from the clipboard, and from there they
are placed and committed identically.

### 28.1 A third state, and why that is the expensive part

Everything in this editor has been either *committed to the document* or *a
gesture in flight*, and that binary is load-bearing. It is why Esc has a clean
ladder, and why §20.4 can safely cancel anything outstanding whenever the view
changes.

A float is neither. It outlives the drag that made it, the document does not
know it exists, and — unlike every gesture here — **it survives a resize**,
because its offset is in image pixels and nothing about the view can invalidate
it. `MoveTool.on_cancel` therefore clears only the drag anchor, where every
other tool must abandon everything.

So the pixel-pushing was easy and the rules were the work: what commits it, what
cancels it, what a keystroke means while it exists, and what happens to it when
you do something else entirely.

### 28.2 The preview is the op, run and thrown away

`float_preview` applies the commit op to the document and returns the frame,
discarding the result. Two lines, because ops are pure.

This is worth stating as a *design consequence* rather than a trick. The preview
is not merely consistent with the commit — it is the same call, so a move that
lands wrong looks wrong first, and there is no second implementation of "what a
move looks like" to drift. The alternative, compositing the float as canvas
items over the real frame, would have been exactly that second implementation,
and it is the one place a half-pixel disagreement (§19.1) would have reappeared.

The cache key becomes `("float", uid, dx, dy)` — distinct from the frame's own
uid, so a committed frame can never be served preview pixels (§5).

### 28.3 One op, because one Ctrl+Z

`paint.move` erases the source and lands the pixels in a single op, despite
being exactly a cut followed by a paste. As two ops it would put two entries on
the undo stack, and Ctrl+Z after moving a sprite would hand back the hole while
you were still holding the sprite.

It also made the commit path honest. `_apply_mask_frames` could not express a
move — an erase *and* a composite on the same frame is not one coverage mask —
so the loop split out into `_apply_frames`, which takes any `image -> image`
transform. That was the right shape all along: everything it states (fresh uids,
unchanged frames stay shared, no change anywhere means decline, the playhead is
named) is a fact about *frames*, and none of it depended on masks.

**A move shifts each frame's own pixels**, where a paste stamps one image
everywhere. That difference is the whole reason `FloatingEdit.image` is None for
a move: nudging a sprite three pixels left through an animation is the case, and
stamping frame 7's version of it over the other twenty is not an answer to it.

### 28.4 The rules, and the reason they all point the same way

- **Enter commits, Esc cancels.** Cancelling is free because the document was
  never touched.
- **Anything else you do commits it first** (`_settle_float`, called from
  editing, scrubbing, undo, open, close and save). Committing rather than
  discarding, because an unwanted commit is one Ctrl+Z away and work discarded
  on your behalf is simply gone. The same reasoning made Save warn rather than
  refuse (§19.2), and it is the only principle here that is not a matter of
  taste.
- **Reaching for another tool commits it — except Move**, which is the tool for
  manipulating one.
- **Esc gains a stage**: gesture, float, tool, region, frames. Ordered by how
  recent each commitment is, so each press undoes the most recent one. The float
  stage appears in two places because a paste can float with no tool active,
  and then Esc never reaches the canvas.
- **Arrows nudge instead of stepping frames.** One binding doing two things,
  which is usually a smell — but stepping settles the float, so without this the
  same key would place pixels and then, one press later, commit them and jump to
  another frame.
- **The status line lives in `_summary`**, not in the float's own event. Every
  view change refreshes the status from there, so a message written anywhere
  else survives until the first zoom and then silently vanishes — and "nothing
  has actually happened yet" is the one thing on screen that nothing else says.

`_settle_float` is re-entrant by construction: it calls `commit_float`, which
calls `run_op`, which calls it. The flag is not optional, and the mutation that
removes it is caught.

### 28.5 Ctrl+V floats now

It costs one keystroke — Ctrl+V, Enter is the old paste-in-place, exactly — and
buys the thing paste-in-place could not do at all. It selects the Move tool on
the way, because arriving in a state you cannot manipulate without first hunting
for the right tool would be a worse trade than the extra keystroke.

### 28.6 A guard standing in front of a wall, again

`_move_pixels` had a zero-offset early-out. A mutation run showed removing it
changed nothing: erasing a region and compositing the same pixels straight back
into it is the identity — including for partial alpha, and for the RGB that
erase leaves *under* transparent pixels — so `_apply_frames` sees no change and
declines on its own.

That is the second such guard in two sessions (§27.4 was the first), and the
pattern is worth naming: **a check placed in front of something that already
decides is not defence, it is duplication that tests cannot distinguish from
correctness.** Deleted, and the identity it relied on is now pinned by a test
of its own — which is what makes the claim in the docstring checkable rather
than merely plausible.

`commit_float` keeps its own unplaced-move check, for a different reason: to
keep "nothing to do" off the status line when you pick a selection up and put
it straight back down. That one is caught by a mutation, so it earns its place.

## 29. The panel decides, not `pack`

Three sections of the side panel had, between them, one hand-written fit check.
This replaces it with a rule that covers all of them and any that come later,
and the measuring done to write it found the guarded section had been broken the
whole time.

### 29.1 A per-section guard only protects the sections someone remembered

The history is four instances of one failure. §21: a toolbar row wanted 1087px,
got 900, and `pack` dropped three widgets off the end with no error. §23.5: the
palette became a column, so the same risk moved to the vertical axis, and
`_view_section_fits` was written to stand the view section down whole. Then the
frame-delay section arrived with no guard of its own and was silently amputated
at the 480x400 minimum. And measuring for *this* section found the fourth: at
the 900x680 default, four sections wanted 516px of a 505px panel,
`_view_section_fits` under-counted the padding by ~45px and said "fits", and
`pack` clipped the Fit/1:1 row to 9px of the 28 it asks for. The one section
with a guard, broken at the default window size, for the whole life of it.

So the check does not belong to a section. `PANEL_SECTIONS` names them in
priority order, `_relayout_panel` walks that order accumulating heights, and a
new section joins the rule by being added to the tuple. There is no place left
to add a section *without* the rule, which is the property the previous three
fixes lacked.

### 29.2 One frame per section, or "whole" is not expressible

A section built as loose siblings — a label, two rows, a separator — can only be
amputated: `pack` drops whichever child it reaches with no room left. Wrapping
each section in its own frame is what makes "show it whole or not at all" a
thing the code can say. The separator moved *inside* the section it heads for
the same reason: a divider that outlived its section would be the layout
pointing at something that isn't there.

### 29.3 Stop at the first section that doesn't fit

Not "skip it and try the next", which is the tempting greedy variant. Sections
are in priority order, so showing a later one where an earlier one didn't fit
says both "there is no room" and "the missing one ranks below what replaced it".
It also flickers: the delay box is 59px and the view section 196, so at the
sizes where the delay box doesn't fit, *shrinking* the window would make it
appear. Monotonic is simpler to explain and steadier to use.

Two consequences fall out and are worth stating because tests depend on them:
the sections on screen are always a prefix of `PANEL_SECTIONS`, and a section
coming back is therefore always the last one showing — which is why
`_show_section` needs no `pack(before=...)`. That started life as one, and is
recorded here as the third guard standing in front of something that already
decides (§27.4, §28.6). The claim is pinned by a smoke check on the drawn order
rather than by the guard, because a guard cannot be watched working.

### 29.4 The default window grew, because the panel outgrew it

516px of sections against 505 of panel is not a bug in the arithmetic, it is a
window that was sized when the panel had two sections. 900x720 leaves ~29px of
slack, which matters more than it looks: these numbers come from X11 font
metrics and Windows' are not the same, so a fit that is exact on the test
machine is a coin toss on the real one. The 480x400 minimum is unchanged, and
there the rule bites hard — the palette, and nothing else.

### 29.5 Threshold arithmetic needs a sweep, not a size

Three mutations of the padding constants survived every fixed-size check and
died on a sweep of window heights. The reason generalises: a constant that is
24px light only changes the outcome inside a ~24px band, and outside that band
the wrong number and the right one agree. Any test pinned to one window size is
testing the constant only if it happens to sit in the band — and every threshold
moves the moment a section is added.

The sweep also had to be told what to look at. Asserting on `_sections_shown`
checks the panel's own account of itself, and `pack` disagreeing with that
account *is* the bug; asserting on `pack_slaves` checks the order things were
packed in, which a `side="bottom"` mutation leaves untouched while turning the
panel upside down. So the checks read `winfo_ismapped`, `winfo_height` against
`winfo_reqheight`, and `winfo_y` — three questions Tk answers about what it
actually did. Same shape as "we disabled the swatch" not being "the swatch looks
disabled" (§27.5).

## 30. Crop to Selection

Nearly free, and deliberately so: `Region` is held in edge coordinates
*because* that makes it `canvas.crop`'s argument list (§26.1). The command is
that correspondence spelled out — four attributes into four params — and it
lives on the controller rather than in the frontend because the controller owns
the region and a second frontend should not have to learn the correspondence
too.

No new op, and no `can_crop_to_region` either: the menu entry asks the two
questions it can already ask, because a `can_crop_to_region` would be `can_copy`
under a second name, and two names for one predicate is duplication no test can
catch. What it did force is that op-menu entries now carry a *predicate* instead
of an op id — the appended non-registry items are no longer all "can this op
run", and encoding that as a special case in the generic refresh would have put
frontend knowledge in the half that has none.

The region is dropped afterwards, because the rectangle it named has become the
whole canvas: keeping it would leave either a marquee around everything, which
says nothing, or — since `_emit_doc_changed` re-clamps a region when the canvas
changes shape — a smaller rectangle trimmed against an origin that just moved.
But *only* if the crop actually happened. A full-canvas region makes this the
identity, the op declines rather than stacking a no-op undo entry, and taking
the marquee away as a consolation prize would be the one case where the command
changes nothing except something you didn't ask it to change.

Undo restores the pixels and not the marquee. That is not an oversight: the
region is session state and session state is not on the undo stack (§26.3), and
a marquee resurrected onto the pre-crop canvas would be worse than none.

## 31. Python 3.11 broke the package, and nothing said so

`Document.meta` defaulted to `MappingProxyType({})` — a *read-only* mapping,
chosen precisely so a frozen dataclass could share one instance safely. Until
3.11, dataclasses rejected mutable defaults by asking "is it a list, dict or
set". 3.11 replaced that with "is its type unhashable", which is a better
question with a wider net: `mappingproxy` is unhashable, so the class body
raised `ValueError` at import and `import giflite` failed outright on 3.11,
3.12 and 3.13. `pyproject.toml` says `>=3.10`.

The fix is `field(default_factory=lambda: EMPTY_MAP)` — the same singleton, a
different spelling. The interesting half is the test. Importing the package
catches this on a new interpreter and is silent on an old one, which is exactly
backwards: the person who needs telling is the one on 3.10, writing the next
one. So `test_boundaries.py` restates 3.11's rule directly — walk every
dataclass in the package, fail on any default whose type is unhashable — where
it fails on every version. Verified by reintroducing the bad default under 3.10
and watching it fail there.

## 32. The system clipboard

Whole frames, in and out, through the operating system. The internal clipboard
from §26 stays exactly as it was; this is a second door, and most of the design
is about which door does what.

### 32.1 It lives in `app/`, not in `ui/tk/`

The opposite call from the one §19 makes about Tools, and for the opposite
reason. A Tool is about mouse gestures, which every toolkit spells differently,
so it belongs to the frontend. "Put these pixels on the Windows clipboard" is
the same three Win32 calls no matter what drew the window, so a second frontend
should inherit it rather than reimplement it. `sysclip.py` imports Pillow and
`ctypes` and nothing else — the boundary test is unmoved.

### 32.2 Reading is easy, writing is not, and that shapes the file

Pillow ships `ImageGrab.grabclipboard()` and it works on Windows, macOS and
X11/Wayland with a helper installed. Pillow ships nothing for the other
direction, so writing is a `ctypes` shim onto `user32`/`kernel32`, Windows-only.
`can_copy()` says so out loud so the caller can behave, rather than discovering
it at the moment someone presses the key.

**Everything that can be tested without a clipboard is a pure function**: the
DIB encoder, its inverse, and the two decision rules all take arguments and
return values. `put_image` — open, empty, allocate, set, close — is the only
thing CI cannot run, and it is written to be boring for exactly that reason: no
branching on the data, one ordered sequence, the close in a `finally`. **The
untestable code should be the code with nothing in it.**

The test that earns its keep is `test_pillow_reads_it_as_a_bitmap`. A round
trip through this module's own decoder proves only that two functions here
agree, and a flipped row order implemented consistently in both would agree
perfectly while producing a wrong picture in every other application. So the
DIB gets a 14-byte BITMAPFILEHEADER bolted on and is handed to Pillow's BMP
decoder, which somebody else wrote. Three details it pins, each of which fails
*silently* — the clipboard accepts the handle, nothing raises, and the picture
merely arrives upside down or blue: DIB rows run bottom-up, the channel order
is BGRA, and there is no file header on a clipboard DIB.

### 32.3 Two formats, because they serve different readers

PNG *and* CF_DIB. 32bpp BI_RGB has somewhere to put alpha but no promise that
anyone reads it; a PNG's alpha is not optional. Modern applications ask for the
registered "PNG" format and get transparency, everything else takes the DIB.
Writing one without the other means either losing alpha everywhere or being
unpasteable in half of Windows.

### 32.4 One clipboard, two doors

Matthew's call, and it needs stating precisely because the two halves are not
symmetric:

* **Every copy goes out.** Copy Area and Copy Frame both mirror to the OS, so
  the last thing you copied is the thing another application gets. A sprite is
  as worth having in Discord as a frame is.
* **The internal slot is still what Ctrl+V reads**, and that is not redundancy.
  It carries `_clipboard_origin`, which is what makes paste land back where it
  was copied from. No system clipboard format has anywhere to put that, so a
  round trip through the OS would silently turn paste-in-place into
  paste-at-the-corner.
* **Paste Frame reads the OS.** That asymmetry is the feature: this is the door
  *in*. A screenshot, a frame exported from something else, a drawing from a
  paint program — Ctrl+V could never have offered any of them.

Copy Frame stores its image at origin (0, 0), so Copy Frame followed by Ctrl+V
is a paste-in-place of the whole picture with no special case anywhere: a frame
*is* the canvas.

Failing to reach the OS clipboard is **not** a failed copy. The pixels are in
hand either way, so it reports in the status line and returns normally —
raising would turn "another application had the clipboard open for a moment"
into a copy that didn't happen.

### 32.5 Replace, not composite

`paint.replace_frame` exists separately from `paint.paste` for one reason: paste
composites, so a clipboard image with transparent corners leaves the old frame
showing through them. A frame that is half the old picture is not the frame
anyone copied. It is also the first op here with no mask at all, which
`_apply_frames` takes in its stride — it asks for `image -> image`, and "return
the other image" is as valid a transform as any. The frame keeps its own
**duration**: you replaced the picture, not the timing.

Size is refused, not resized, and the message names both sizes. Scaling to fit
would be a silent answer to a question the user should be asked, and "wrong
size" tells you something is wrong where "60x60 against 40x20" tells you what to
do. Nothing lands on the undo stack for a refusal.

### 32.6 `grabclipboard()` has three return shapes, not two

It returns an `Image`, or **a list of file paths** — which is what Windows puts
there when you copy a file in Explorer — or None. A caller assuming "image or
nothing" gets an `AttributeError` on the good day and a confusing no-op on the
bad one. The list case gets its own message rather than being folded into
"nothing on the clipboard", because a GIF copied in Explorer is a *file*, and
opening it as a single frame would be the wrong answer to a reasonable action.

### 32.7 A fourth guard in front of a wall

`ReplaceFrame` had `lambda _old: incoming.copy()`, defending the document
against holding a reference to the clipboard's own pixels. A mutation removed
the `.copy()` and nothing broke — correctly, because `convert("RGBA")` one line
above already returns `self.copy()` when the mode matches, so the image was
detached before the lambda ever saw it. §27.4, §28.6, §29.3, and now this.

The claim was real and worth keeping — the clipboard outlives the call and can
be written to again, and a Document holding that reference would have its
history rewritten from outside (risk 3, reached by a new route). So it is
pinned by a test that mutates the passed-in image and checks the document,
rather than by a line of code that cannot be observed doing anything.

### 32.8 Ctrl+Shift+C needed the guard more than Ctrl+C did

§26.4 recorded that `Ctrl+C` is a text-editing keystroke and `bind_all` fires
after the class binding, so it must yield to a focused text field. Ctrl+Shift+C
is a *selection* keystroke in the same fields — and where the unguarded Ctrl+C
would quietly replace the image clipboard, an unguarded Ctrl+Shift+V would
replace the frame you were looking at while you typed a brush size.
