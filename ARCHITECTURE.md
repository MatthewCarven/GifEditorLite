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
