# Worklog

## 2026-07-22 — Design session

Greenfield. Agreed scope and wrote `ARCHITECTURE.md`.

**Decisions**

- Package `giflite`, first frontend Tkinter (stdlib, zero install, forces a clean core/UI split).
- v1 "lite" = frame operations only: delete, duplicate, move, reverse, trim.
- Export deliberately deferred to M3 — Matthew's call, he's fine without save for a while.
- All other formats (image sequence, video import, WebP/APNG) are roadmap, not v1, and slot into an IO plugin registry gated by `available()` so optional deps never break startup.
- Immutable `Document`/`Frame` + pure operations → undo is a snapshot stack of shared pointers rather than the inverse-command pattern. Cheap because reorder/delete only move references.
- Frontend seam is an observable `AppController` façade, **not** a widget abstraction layer. Enforced by one greppable rule: only `giflite/ui/tk/` may import tkinter.
- Operations return `OpResult(doc, selection)` — the op decides what's selected afterwards, which removes stale-index bugs and the need for per-frame identity tracking.

**Findings (measured against Pillow 12.2.0, not recalled)**

- `ImageSequence.Iterator` yields one mutated object, not N — must `.convert("RGBA")` per frame.
- Pillow coalesces disposal on seek, so we get full frames free. Big chunk of work avoided.
- Delays floor to 10 ms centiseconds: `[33,17,5,125]` → `[30,10,0,120]`. A 5 ms frame becomes 0 and viewers clamp it to ~100 ms. Model must quantise to 10 ms with a 20 ms floor.
- **Identical consecutive frames merge on save with durations summed** — 3 frames became 2. Frame count is not round-trip stable, which collides with "duplicate a frame to hold it". Open question, flagged for Matthew.
- 640×480 × 120 frames RGBA = 147 MB. Cap-and-warn at load; `FrameStore` swap-in reserved.

**Deviations from the working agreement**

- None. `git init` left for Matthew as agreed (noted in ARCHITECTURE.md §17).

**Constraint discovered**

- My sandbox has no `tkinter` and no display. Core is fully testable by me; the Tk layer needs Matthew at the keyboard. Reinforces keeping logic out of the UI layer.

**Design review → rev 2**

Ran an adversarial pass over the rev 1 doc before handing it over. One root cause produced most of the findings: *the controller modelled the document but not the session*. Changes made:

- `AppController` gains `index`, `playing`, `seek/play/pause/tick`, and menu-state readers (`can_undo`, `undo_label`, `can_run`). Without these the playhead lived in the frontend, nothing clamped it when frames were deleted (crash on delete-last-frame), and every future frontend would reimplement the same glue.
- `History` snapshots `(doc, selection, index, label)` — rev 1 stored `(doc, label)`, which contradicted §6's whole reason for existing two pages earlier. Dirty is now a saved-marker, so undoing back to the saved state clears the asterisk.
- `PlaybackClock.set_durations()` added; rev 1's clock froze durations at construction and would be stale the instant any M2 op ran.
- Event ordering made contractual: one `doc_changed` carrying doc + selection + index per mutation.
- Documented that `frozen=True` protects nothing here — `Frame.image` is a mutable Pillow object shared across every snapshot, so "ops never mutate in place" is the real invariant behind undo. Added a byte-identity test for it.
- Cache key changed from `id(frame.image)` to an explicit frame uid — CPython reuses addresses after GC, so the original would eventually serve wrong pixels. Split the cache: PIL thumbnails in `app/`, `PhotoImage` in `ui/tk/`, because `ImageTk` imports tkinter and would have bound the app layer to the toolkit while the §11.4 grep stayed green. Widened the grep.
- Zoom/pan handed wholly to the frontend; rev 1 split it half-and-half, the worst option.
- Empty state (`doc is None`) is now explicit — `python -m giflite` with no argument was undefined behaviour.
- Dropped the undefined `Progress` parameter in favour of `status()` + busy cursor until threading is real.

**Cut for over-engineering** (the likelier failure mode for a one-person lite tool): the `Param` schema + generic dialog generator existed to render one spinbox — deferred to M3 where writer options make it plural. IO Protocols + registry + `available()` → a plain dict until the second format. Frontend registry + `--ui` switch → a direct call. LRU eviction → plain dict (120 thumbnails is 2 MB). Ping-pong/speed → M4. Line estimate corrected 1,200 → ~2,000, since a tripwire set too low makes you cut the wrong things.

Kept unchanged as correct: snapshot undo over inverse-command, full-coalesce-to-RGBA, the import rule, pure ops, §12 in full, milestone ordering, the deferred `FrameStore` ladder.

Also added the gesture rule (§11.3): gestures render their own preview locally and commit exactly one op on release. That one sentence is what keeps drag-to-reorder from dragging a transaction system into the core.

**Repo**

- Matthew ran `git init`. Docs committed as `3d0b350`.
- **Constraint found:** the folder is mounted into my sandbox in a way that forbids `unlink`, so git cannot clean up its own lock and temp files. Plain `git add` dies leaving a stale `.git/index.lock`. Workaround: `GIT_INDEX_FILE=/tmp/gl.index git add …` keeps the lock off the mount and commits succeed — at the cost of orphaned `tmp_obj_*` files in `.git/objects` (24K so far, harmless, needs a periodic sweep from Matthew's side).
- Consequence: I can keep committing, but `.git/index` doesn't exist on disk until Matthew runs a git command locally, and the stale `index.lock` must be deleted before his first one.

---

## 2026-07-22 — M0 built

Matthew green-lit building in parallel with his doc review. M0 is complete:
`python -m giflite some.gif` opens a window showing frame 0, and bare
`python -m giflite` shows a real empty state.

**Shipped**

- `core/model.py` — `Frame`/`Document`/`Selection`, validation, duration quantisation.
- `core/io/gif_read.py` — coalescing reader plus a cheap `probe_gif` so the size warning can happen before the memory is committed.
- `app/events.py`, `app/controller.py` — session state (doc, selection, playhead, path) behind the façade.
- `ui/base.py`, `ui/tk/{app,canvas}.py` — the Tk frontend.
- 51 headless tests, a 12-check Tk smoke test, `pyproject.toml`, README.

**Two bugs caught while wiring, both worth remembering**

1. `__main__` originally opened the CLI file *before* constructing the window, so `STATUS` and `ERROR` fired into a bus with no subscribers — `giflite missing.gif` would have failed in complete silence. Fixed by moving the initial open into `Frontend.run(controller, initial_path)`, which the frontend performs via `after()` once its window exists and is subscribed.
2. The controller was emitting a formatted summary string ("6 frames, 80x40, 1.15s") as a `STATUS` event. That's a *view of state*, not a transient message, so a frontend that missed the event would show something stale forever. Moved to `MainWindow._summary()`, derived from `controller.doc` on every render. `STATUS` now carries only genuinely transient things — "Loading…", size warnings. Same reasoning as `TITLE_CHANGED` passing `(path, dirty)` rather than a finished title string.

**Deviation from the plan, and it paid off**

The doc says (risk 9) that I can't run Tk in my sandbox, so the UI would need Matthew at the keyboard. Rather than hand over untested UI code I extracted `python3-tk` from the Ubuntu .deb into `/tmp`, pointed `PYTHONPATH`/`LD_LIBRARY_PATH` at it, and ran the real window under Xvfb. All 12 UI checks pass and I have a screenshot of the rendered window. Notes for next time:

- The sandbox kills background processes between bash calls, so Xvfb must be started **in the same call** as the thing using it.
- The Tk smoke test lives at `tests/smoke_tk.py`, deliberately *outside* the pytest run — it needs a display, and CI shouldn't.
- Risk 9 is downgraded, not closed: this proves the code runs on Linux/Tk 8.6, not that it looks right on Windows. Matthew should still eyeball it once.

**Caught by the screenshot, not by the tests:** the status bar read "~0 MB" for a small GIF. Fixed with adaptive B/KB/MB units. Worth noting that twelve passing assertions didn't catch something obvious the moment a human looked at it.

**Git: the workaround hit its ceiling — handing off**

`GIT_INDEX_FILE=/tmp/...` bought exactly one commit. Ref updates need their own
lock files (`.git/HEAD.lock`) and the mount forbids unlinking those too, so the
repo now has two stale locks I can't remove and 41 orphaned `tmp_obj_*` blobs.
Rather than brute-force it further, M0 is uncommitted on disk (all tests green)
and Matthew commits it locally. Standing arrangement from here: **I write files
and leave a `COMMIT_MSG.txt`, Matthew commits.** Cleaner than fighting the mount
every session.

M0 committed locally by Matthew as `95cf542`, 26 files.

Two loose ends from that handover:

- The `tmp_obj_*` sweep failed on Windows with "insufficient access rights" — my sandbox still holds file handles on them. They're inert (git ignores non-object filenames under `.git/objects`), so they can be swept after a reboot or ignored indefinitely.
- `git add` warned about LF→CRLF on all 27 files. Added `.gitattributes` pinning everything to LF, since the repo is written from a Linux sandbox and checked out on Windows; without it each side perpetually "changes" files the other just wrote. Needs `git add --renormalize .` once.

---

## 2026-07-23 — M1 built (playback + timeline)

Matthew was keen to actually watch a GIF, so M1 is done in one sitting. You can
now open a GIF, hit Space, watch it loop, scrub the thumbnail strip, step with
the arrow keys, and change speed. Screenshot verified under Xvfb.

**Shipped**

- `core/playback.py` — `PlaybackClock`, pure dt-driven timing. Forward + loop + speed. No timer inside it; the frontend feeds it elapsed ms. 21 tests.
- Controller playback: `play/pause/toggle_play/tick/seek/step/set_speed`, `playing`/`can_play`, new `PLAYBACK_STATE` event. Clock owned by the controller, durations rebuilt and playhead re-synced in `_emit_doc_changed`. 22 tests.
- `app/cache.py` — `ThumbnailCache`, PIL-level, keyed by frame uid, `retain()` pruning. 8 tests.
- `ui/tk/timeline.py` — virtualised thumbnail strip on one Canvas: only visible thumbnails get items, click-to-seek, current-frame highlight, auto-scroll to follow the playhead, wheel scroll.
- `ui/tk/canvas.py` — added a bounded scaled-frame cache so playback/scrub/resize don't re-run a resize of a big frame every redraw.
- `ui/tk/app.py` — transport bar (play/pause, frame counter, speed dropdown), continuous 60fps timer, Space/←/→/Home/End bindings.

**Design decisions worth remembering**

- **Speed pulled forward from M4.** It's a one-line `dt` multiplier and the controller API already promised `set_speed`, so exposing a speed dropdown was nearly free and makes M1 fun to try. Ping-pong stayed at M4 — it needs a Mode enum and boundary-reversal logic, which is real work. Noted in ARCHITECTURE §10.
- **`PLAYBACK_STATE` is a new event.** A non-looping GIF stops on its own at the last frame, so "are we playing?" can't be derived from the frontend's own button clicks — it's session state the controller must announce. Added to the §9 event table.
- **Continuous timer, `tick()` no-ops when paused.** Simpler than starting/stopping an `after` loop and dodges the start/stop race. 60fps idle callback is free.
- **dt cap (250ms).** After a stall real elapsed time can be seconds; the cap stops playback fast-forwarding through the whole GIF in one frame. The clock crosses multiple frame boundaries per tick correctly, so the cap is the only guard needed.
- **Cache split held.** PIL thumbnails in `app/cache.py`, PhotoImages in `ui/tk/timeline.py`, both keyed by the same uid. The boundary grep still passes — no `ImageTk` leaked into `app/`.

**Bug caught by a test, not by me:** my "stall doesn't fast-forward" test asserted `index == 1` after a 10s tick, but the 250ms cap correctly lands on frame 2 (2.5 frames of travel). The code was right; my arithmetic was wrong. Fixed the assertion.

**Numbers:** 95 headless tests (was 51), 28-check Tk smoke (was 12). All green. Xvfb re-extraction of tkinter worked exactly as noted last session.

**Handover**

- M1 is on disk, uncommitted. `COMMIT_MSG.txt` is ready — `git add -A; git commit -F COMMIT_MSG.txt`.
- Same as before: I write files, you commit. The sandbox still can't hold git locks.
- Please run it on Windows and actually play a GIF — Xvfb proves Linux/Tk 8.6, not your machine.

**"Aspect ratio bug" that wasn't — investigated and resolved**

Matthew tried a real GIF (`claude_advance_1x.gif`) and it looked like a small square sprite, not the wide banner he expected — read as a scaling bug. Chased it properly instead of guessing:

1. Tested the reader on synthetic 2:1 and logical-screen-mismatch GIFs → aspect preserved perfectly.
2. Tested the `_fit` math in isolation → 2:1 in, 2:1 out. Correct.
3. Rendered a synthetic 2:1 GIF through the real Tk window under Xvfb → 200×100 → 884×442, correct.
4. Matthew dropped his three actual GIFs in the folder. Read them directly: `claude_advance` is genuinely **120×26** (4.6:1), 60 frames; the formation ones are 304×90 and 276×90. Reader reports all three exactly right.
5. Rendered the *actual* 120×26 file under Xvfb → 884×191, aspect 4.63. Provably correct — and it looked exactly like Matthew's Windows screenshot: a lone sprite, apparently square, floating in dark space.

**Root cause: not a bug.** The GIF is a wide, short canvas with a small sprite centred in it and transparent pixels filling the rest of the width. Scaled correctly, the transparent margins rendered against the dark background and were invisible, so only the (roughly square) sprite showed. Nothing scaled wrong; the canvas boundary was just undrawable against the letterbox. Matthew's own read — "it's display handling, not scaling" — was right. (His screen-aspect / DPI hunch wasn't the cause: `_fit` uses the canvas widget's pixel size, and DPI scales uniformly rather than distorting aspect.)

**Fix (folded into M1):** the preview now composites each frame over a checkerboard sized to the *fitted image*, with a thin border. Transparent pixels reveal the checker; the checker's extent plus the border show exactly where the canvas is. Standard image-editor behaviour, and it makes a wide-but-sparse GIF legible instantly. Verified against the real file under Xvfb — the 120×26 canvas now reads as a wide checkered strip. Smoke test +1 check (canvas boundary drawn), 29 total.

Lesson worth keeping: "looks wrong" from a screenshot needed the actual file to resolve. Three layers of correct code can still produce a confusing picture, and the fix was making the truth visible, not changing the maths.

**Note:** Matthew's three GIFs are sitting in the project root. They're good real-world fixtures — if we want them in the repo, `tests/fixtures/` is the place; otherwise add `*.gif` to `.gitignore`. Left as-is for now; his call.

---

## 2026-07-23 — M2 slice 1: editing core

Matthew asked to slice M2 rather than land it in one go, so this is the core:
the editing engine and undo, fully headless and tested. No UI yet — that's
slice 2.

**Shipped**

- `core/ops/registry.py` — `@register_op`, `Operation` protocol, `OpResult`, `menu_groups()`.
- `core/ops/frames.py` — the five ops: delete, duplicate, move, reverse, trim. Each pure, each returns the post-op selection. None touch pixels (duplicate shares the source image + uid, so copies hit the caches free).
- `core/history.py` — `Snapshot(doc, selection, index, label)` stack, limit 64, saved-marker dirty, `amend_current`.
- Controller: `run_op`/`undo`/`redo`, `can_undo`/`can_redo`/`undo_label`/`redo_label`/`can_run`, `dirty` from history. Refuses ops with no selection and refuses to empty the document, both via STATUS not exceptions.
- 64 new tests (ops, history, immutability, controller editing). 164 total, all green.

**Two things worth remembering**

- **`amend_current` — undo restoring the right selection.** First cut had undo restore the selection frozen at the *previous* op, not where the user was when they invoked the current one. A test caught it (undo after seek+select+duplicate came back with the baseline selection). Fix: snapshots for selection/playhead are amended in place at op time, since scrubbing and selecting between ops aren't undoable steps but *are* the view you should return to. Clean once framed that way.
- **The immutability test earned its keep immediately.** Parametrised over every op × a real selection, asserting source-frame `tobytes()` is unchanged. This is the guard for risk 3 (a frozen dataclass doesn't stop an in-place `paste`). It also has a meta-test that fails if someone adds an op without covering it here.

**Design note:** `run_op(op_id, **params)` passes params through to `apply`; duplicate reads `copies`. No `Param` schema yet — deferred to M3 as planned, where writer options make it plural. Duplicate's count will come via a hardcoded dialog in slice 2.

**Git housekeeping:** the three sample GIFs Matthew dropped are now `/*.gif`-ignored at the root so `git add -A` won't sweep them into history. If we want them as fixtures they move to `tests/fixtures/` (not ignored). Also folded the still-uncommitted checkerboard canvas fix into this checkpoint.

**Next — M2 slice 2 (UI):**

- Selection gestures (click / shift-range / ctrl-toggle) and drag-to-reorder following the gesture rule (local preview, one `move` op on release).
- Edit + Frames menus from the registry with live enable/disable; keyboard shortcuts (Del, Ctrl+D, Ctrl+Z, Ctrl+Shift+Z, Ctrl+A).
- Duplicate-count dialog in `ui/tk/dialogs.py`.
- Extend the Xvfb smoke test with a full edit+undo cycle and a screenshot.
- Risk 2 still open, still not blocking (first bites at M3).

---

## 2026-07-23 — M2 slice 2: editing UI. **v1 lite complete.**

The editor edits. Select frames (click / shift-range / ctrl-toggle / Ctrl+A),
delete, duplicate, reverse, trim, drag to reorder, and Ctrl+Z it all back.
Screenshot verified under Xvfb — menu bar now File / Edit / Frames, a shift-range
of frames 3-5 selected in blue with frame 4 as the playhead.

**Shipped**

- `ui/tk/dialogs.py` — the one hand-written dialog (duplicate count), via `simpledialog`. No `Param` schema; still deferred to M3.
- `ui/tk/timeline.py` — full mouse handling: plain/shift/ctrl click via most-specific binding dispatch, and drag-to-reorder that draws its own insertion marker and commits one `move` op on release (the gesture rule, honoured). Click-on-already-selected defers collapse-to-single until release so a drag moves the whole multi-selection.
- `ui/tk/app.py` — Edit + Frames menus built from `menu_groups()`, enable/disable refreshed on open via `postcommand` (so the frontend never tracks menu state per event — it asks `can_run`/`can_undo` at the moment the menu appears). Keyboard shortcuts for undo/redo/delete/duplicate/select-all/deselect. Timeline keeps its scroll position across edits, resets only on open/close.
- Tk smoke test: 50 checks (was 29), including the real drag path — fake events through `_on_press`/`_on_motion`/`_on_release`, verifying `_index_at`/`_gap_at` and that the drag lands one undoable `move`.

**Design notes worth keeping**

- **Menu state via `postcommand`, not event tracking.** Tk menus refresh themselves each time they open, so the frontend reads `can_undo`/`undo_label`/`can_run` at that instant rather than maintaining shadow state on every doc/selection change. Fewer moving parts, and it leans on exactly the read-only controller members the seam was designed to expose.
- **The gesture rule held.** Drag-to-reorder needed zero new core plumbing — no provisional-transaction API. The preview is a yellow line on the timeline canvas; the commit is one `frames.move`. Exactly what §11.3 promised, and a good sign the seam is real.
- **Modifier dispatch is free in Tk.** Binding `<Button-1>`, `<Shift-Button-1>`, `<Control-Button-1>` separately means the toolkit routes each to the right handler; no manual `event.state` bit-testing.

**Verification note:** the smoke test drives selection through the app handlers *and* the drag through the timeline's own mouse path, so both the app→controller wiring and the timeline's gesture geometry are covered. What's still only smoke-tested (not unit-tested) is pixel-level rendering — by design, per §16.

**Handover**

- M2 slices 1+2 are both on disk. Slice 1 has its own `COMMIT_MSG` if not yet committed; this slice's message follows. If slice 1 is already committed, `git add -A; git commit -F COMMIT_MSG.txt` lands slice 2.
- Please drive it on Windows: shift-click a range, drag a frame, mash Ctrl+Z. The formation GIFs (92 frames) are the real test of timeline feel.

**Next — M3 (save):** `gif_write` + Save/Save As, and the `Param` schema finally earns its place for export options (dither, loop, optimise). Risk 2 (identical-frame merging on save) stops being hypothetical here — decision time on accept / disable-optimiser / project-file. Risk 4 (palette quality) too.

---

## 2026-07-23 — M3: save. **The editor is a full loop now.**

Edits can leave the building. Open, edit, Ctrl+S, and the GIF is written back.
Verified end-to-end on Matthew's real transparent GIFs.

**Decisions taken (Matthew):** accept the identical-frame merge for GIF; keep this first cut to *just Save* with smart defaults (no options dialog); defer a faithful project/sidecar format to the roadmap (ARCHITECTURE §18).

**A test that reshaped the decision.** Before asking Matthew, I checked what actually controls the merge. Turns out it's **unconditional** — Pillow's GIF encoder merges identical consecutive frames even with `optimize=False` and no disposal (4 frames → 3, durations summed). So "disable the optimiser," which rev-1 me floated in the design doc as a real option, was never viable. Good thing I tested rather than presenting it. The honest choices were only accept vs project-file, and accept is right for "lite" (playback-identical, zero machinery).

**Shipped**

- `core/io/gif_write.py` — Document → GIF. Adaptive palette + Floyd–Steinberg dither, per-frame transparency (GIF's single transparent index, not alpha; pixels under the alpha cutoff get index 255), disposal=2 so coalesced frames round-trip, durations + loop preserved. `count_merges` reports the fold count.
- `core/io/__init__.py` — `WRITERS` dict, `writer_for`, `save_filter`.
- Controller — `save` / `save_as` / `has_path`; `history.mark_saved()` clears dirty on write; merge count surfaced in the save STATUS message.
- `ui/tk/app.py` — File menu Save / Save As with shortcuts; Save falls back to Save As when there's no path; File menu enable/disable via postcommand.
- 18 tests (writer round-trip incl. transparency-as-shape and held-duplicate merge; controller saving incl. dirty-clears-on-save and undo-back-to-saved-is-clean). Smoke +5 (54→ then fixed →55). 182 headless total, all green.

**The transparent-RGB false alarm, twice.** My first fidelity check reported a *catastrophic* 112/255 mean colour error — panic-worthy. It was measuring RGB under fully-transparent pixels, where the colour is undefined and nobody sees it. Masking to opaque pixels: **0.000/255**, pixel-perfect. It bit me again in the reverse+save integration check (an `edited[0] == original[-1]` byte-compare that failed only on transparent-pixel RGB). Lesson logged: for RGBA fidelity, compare visible pixels + transparency *shape* separately, never raw RGBA `tobytes()`.

**`Param` schema deferred a third time.** "Just save" needed no options dialog, so the declarative schema still hasn't been built. It now lands with the next feature that genuinely has plural options — M4 timing/canvas ops or M5 WebP/APNG export. Each deferral has been correct; building it speculatively would've been the mistake.

**Handover**

- M3 on disk, uncommitted. `COMMIT_MSG.txt` ready.
- Try Ctrl+S on a real edit, reopen the result. And note the one behaviour to expect: duplicate-a-frame-to-hold-it comes back as one longer frame (by design now).

**Next — M4** (canvas + timing ops): crop, resize, rotate/flip, per-frame delay, speed-scale, ping-pong. This is where `Param` finally gets built (resize needs dimensions, delay needs a value), the IO dict graduates to a real registry (image-sequence import/export arrives), and the memory ladder (risk 1) gets its first real test since canvas ops actually allocate new pixels. Or take the project-file want off the shelf first — Matthew's call.

---

## 2026-07-23 — M4: canvas & timing ops. **The Param schema finally exists.**

Matthew picked M4 over the project file, and told me to take my time on correct logic — so I did, sliced into three, and it paid off immediately.

**A real bug, found before writing a line of the feature.** Working through Scale Speed's math, I checked what `quantise_duration` does to a sped-up frame: a 60ms frame at 5× → 12ms → **100ms**. Speeding up made it *slower*. The function conflated two jobs — "unknown/zero delay → default 100" (a reader concern) and "genuine small delay → clamp to floor" (an editing concern). Split them: the quantiser now floors sub-20ms to 20ms and is monotonic (smaller in never yields larger out), and the browser-clamp (tiny → ~100, matching how viewers actually play sub-2cs frames) moved into `gif_read` where it belongs. Guarded with a monotonicity test. This is exactly the kind of thing "take your time on correct logic" is for — Scale Speed built on the old quantiser would have shipped a feature that lies.

**Slice 1 — Param schema + timing ops.** `core/params.py`: Int/Float/Bool/Choice, each with `coerce` (parse + clamp + fallback) and a `default_params(doc, sel)` hook so a dialog can seed from the current document (Resize pre-fills the current size). One subtlety caught: a `default` *property* on the base class is a data descriptor and would shadow the subclasses' `default` field — removed it, left a comment. Timing ops (`set_delay`, `scale_speed`) are pure and selection-or-all. Migrated `duplicate` to declare a `copies` param, which retired the M2 hand-written dialog.

**Slice 2 — canvas ops.** `resize` (keep-aspect derives height), `rotate`, `flip`. First ops to *allocate pixels*, so each output frame gets a fresh uid (stale-cache guard) and history now holds real image memory (risk 1's first real load; 64-cap bounds it, `FrameStore` is the escape hatch if a big GIF ever hurts). Rotation directions verified against Pillow, not guessed — `ROTATE_270` is clockwise, `ROTATE_90` is counter-clockwise. Immutability test extended to all five new ops; still byte-identical source pixels.

**Slice 3 — the payoff UI.** `ui/tk/dialogs.py` is now a generic `ParamDialog` built from any op's param tuple (Bool→check, Choice→combo, Int→spinbox, Float→entry), values back through `coerce`. Menus are built generically per op-group — Frames / Timing / Image — straight from the registry, so adding an op needs nothing here beyond the group→title map. The "..." dialog convention lives in the UI (menu display), not the op label, so undo still reads "Undo Resize". Ping-pong: a bounce mode in the clock (reflects off both ends, ignores loop count) plus a transport checkbox.

**Bug I caught in my own smoke test:** I called `_invoke_op("canvas.flip")` in a scripted run — flip has a param, so it opened a modal dialog and hung the test forever. Removed it; the lesson is that any op-with-params can't be driven through `_invoke_op` headlessly (it waits on a human), so the smoke test drives param ops via `run_op(..., **values)` directly and only sends param-free ops through `_invoke_op`.

**Numbers:** 243 tests (was 182). Tk smoke 61 checks (was 55), incl. dialog-seeding-from-current-size, resize+refit, and ping-pong toggle. Boundary rule still clean — `params.py` and all the new ops are toolkit-free.

**Deferred, deliberately:** crop wants a rubber-band selection on the preview canvas (typing x/y/w/h is poor UX), so it's its own future slice with a canvas gesture. Image-sequence IO (folder of PNGs) is a different *shape* of source and pairs with promoting the IO dict to a real registry — also deferred. Both noted in TODO.

**Handover**

- M4 on disk, uncommitted. `COMMIT_MSG.txt` ready.
- Try the new menus on a real GIF: Image → Rotate, Timing → Scale Speed, and the Ping-pong toggle on the formation GIFs. Resize keeps aspect by default.

**Next:** crop (canvas rubber-band), or image-sequence IO + registry promotion, or the project-file format, or M5 (video import / WebP export). All genuinely optional now — the editor is complete and useful. Matthew's call.
- Reminder: the `/tmp/tkroot` tkinter extraction and Xvfb don't survive a reboot — re-extract with `apt-get download python3-tk tk8.6-blt2.5 blt libtk8.6`, `dpkg-deb -x` into `/tmp/tkroot`, point `PYTHONPATH`/`LD_LIBRARY_PATH` at it, and start Xvfb in the *same* bash call as the smoke test.

## 2026-07-24 — Crop: a rubber-band gesture on the preview

Matthew picked crop from the post-M4 fork. It's the first *canvas gesture* op — the thing M4 deferred on purpose, because typing x/y/w/h is a poor way to choose a rectangle. Sliced core-then-UI like M2, green in between.

**Slice 1 — the core op.** `canvas.crop` joins resize/rotate/flip in `core/ops/canvas.py`: pure, takes an image-space box, clamps it into the canvas (a gesture can overshoot the edges), allocates fresh-uid frames, shrinks `doc.size`. Three things worth recording:

- **`in_menu = False`.** Crop is gesture-driven, exactly like `frames.move` — the op still carries its params as the data contract the gesture fills in, but there's no generated dialog. Keeping it a normal op (rather than a bespoke non-op) means it's testable headlessly and the seam stays uniform.
- **Decline the no-op.** A box covering the whole canvas, or with zero area, returns the *same* document, so `run_op`'s `result.doc is doc` check reports "nothing to do" instead of pushing an identity snapshot onto undo — the same guard delete-everything uses.
- Checked up front that Pillow's `Image.crop` fully materialises a new image (it `load()`s then does a C-level copy), so it can't alias a source buffer and break the immutability invariant. The immutability suite's coverage guard then *forced* me to add crop to its cases — the test insisting is exactly the point.

**Slice 2 — the gesture.** §11.3's rule ("gestures render their own preview and commit one op on release") already had drag-to-reorder as its instance in the timeline; crop is the same rule on the preview canvas. `PreviewCanvas` now remembers where the fitted image landed (`_image_geom`, recomputed every redraw) and maps widget pixels back to image pixels through it. Press/drag draws a dashed marquee with a live "W×H" label in image pixels; release maps + clamps the box and fires `canvas.crop`; Esc cancels. Wiring notes:

- The Image menu is registry-generated, so `_build_op_menu` now hands its menu back and I append Crop (a non-registry item) to it, reusing the group's `can_run` refresh so it greys out with no document.
- Esc: the canvas binds its own `<Escape>` (the widget bindtag runs before the global `bind_all`) and returns `"break"` *only while cropping*, so normal Esc-to-deselect is untouched otherwise. Entering crop mode focuses the canvas so the key lands there, and pauses playback so a tick can't repaint over the marquee. A window resize mid-crop cancels — the box would otherwise map against stale geometry.

**Numbers:** 253 headless tests (was 243: +8 crop, +2 immutability cases). Tk smoke 74 checks (was 61: +13, incl. the display→image mapped drag producing an exact half-size crop, undo restoring the canvas, and Esc changing nothing). Boundary rule still clean — the op is toolkit-free.

**Handover**

- Crop on disk, uncommitted. `COMMIT_MSG.txt` refreshed (it still held the M4 message).
- Try it on a real GIF: press **C** or Image → Crop, drag a box on the preview, release. Esc to bail, Ctrl+Z to undo.
- Xvfb smoke screenshots (marquee mid-drag + the post-save window) are in my scratch outputs, not committed to the repo.

**Next (all optional, your call):** image-sequence IO + IO-registry promotion, the `.gifproj` project format you want eventually, or M5 (video import / WebP·APNG export). A second frontend would finally exercise the seam for real.

## 2026-07-24 — Painting: design session

Matthew wants painting tools and asked to co-design a coherent modular plan first. Painting is a bigger shift than crop — crop fit the pure-op model, painting stresses it — so we talked it through and locked a shape before any code.

**The decision that keeps it modular: Tools (frontend) commit Operations (core).** Crop was a one-off gesture in the canvas; painting generalises it. A Tool owns the drag, its settings and its cursor and lives in `ui/`; on release it hands a finished stroke to a pure core op that bakes pixels — the same shape as every other op. The clincher for making "Tool" its own concept rather than "an op with a drag": the eyedropper (and pan/zoom) commit *no op* — they only read a pixel or move the view. Full rationale in ARCHITECTURE §19.

**Brush = mask.** A brush produces a coverage mask; the op composites colour through it (paint) or subtracts it from the frame's alpha (erase). Hard vs soft is only *which mask you generate* — the op and the tool don't change. That's how "hard now, soft/AA later" costs a mask function and nothing else, which was Matthew's explicit ask.

**Forks he called:** tool set = Pencil + Eraser + Eyedropper (minimal, proves the seam end to end); hard-edged brushes now; current frame only; paint at fit scale, zoom later. Defaults he took: one snapshot per stroke on the existing 64-cap, and destructive painting (no layers in "lite").

**Noted for later at his request:** undo becomes memory-aware — track the bytes `History` holds and warn/report past ~128 MB rather than trimming silently. Not now ("so long as there's *some* undo available"); it's in TODO as the bespoke follow-up.

**Plan:** slice 1 = the pure ops + tests (headless), slice 2 = the tool system + palette + canvas dispatch + smoke.

**Slice 1 built (core ops).** `core/ops/paint.py` — `paint.stroke` and `paint.erase`, two thin registered ops over a shared `_apply_stroke`, so undo reads "Paint" / "Erase" correctly. The mask idea works cleanly: `_brush_mask` stamps a round hard brush (discs at each point + a thick round-joined line; single pixels for size 1, and `draw.*` clips off-canvas points for free), paint alpha-composites the colour through it, erase `ImageChops.subtract`s it from the frame's alpha. Both copy the target frame before drawing (immutability) and only that frame gets a fresh uid. Decline is byte-based: if the composited result equals the source (empty stroke, off-canvas, or erasing already-transparent pixels) it returns the same doc, so no identity snapshot lands on undo — same convention as crop. Verified with a pure-PIL proof render (green stroke + red dot + an erased hole over a checkerboard — transparency reads correctly) as well as the suite. 268 tests (was 253: +11 paint, +4 immutability instances). Boundary rule clean — the op is toolkit-free. **Next: slice 2, the tool layer + palette.**

**Slice 2 built (the tool layer).** `ui/tk/tools.py` is the new concept: a `Tool` base with a shared `StrokeTool` (Pencil/Eraser differ only in op + colour) and the op-less `EyedropperTool`. Tools are toolkit-neutral — they get image-space coords and talk to a duck-typed `ToolContext` (implemented by MainWindow: `frame_index`, `brush_size`, `fg_color`, `commit`, `pick_color`, `preview`, `clear_preview`), so the interaction logic could lift to another frontend unchanged. The canvas grew one mouse-dispatch that routes to crop-mode first, then the active tool, plus an `_image_to_display` inverse of crop's mapping and a scaled-polyline preview overlay. A tool palette rides across the top (Cursor/Pencil/Eraser/Eyedropper radios + a colour swatch via `colorchooser` + a size spinbox), with B/E/I shortcuts; selecting a paint tool pauses playback so a tick can't repaint over the live stroke.

**The bug worth its weight:** painting a frame while some *other* frame was selected made the playhead jump away on commit — because `run_op` sets the index to `result.selection.first`. Fix: the paint op returns `Selection.single(index)`, so the frame you painted stays current (and selected) for the next stroke. Caught by reasoning about the commit path, then locked with a test. This is the kind of cross-seam interaction the "take your time on correct logic" rule is for.

**Numbers:** 269 headless tests (+1, the selection guard); Tk smoke 83 checks (was 74: +9 — pencil stroke via the real dispatch painting the centre pixel, eyedropper picking it back, eraser clearing alpha). Boundary rule still clean: `tools.py` imports no toolkit (only `typing`), all the Tk lives in `canvas.py` / `app.py`. Screenshotted a multi-colour paint + a live preview + the palette under Xvfb.

**Handover**

- Painting slice 2 on disk, uncommitted. `COMMIT_MSG.txt` refreshed.
- Take the pencil for a spin: pick a colour, drag on the preview; B/E/I switch tools; each stroke is one Ctrl+Z. Erase reveals the checkerboard (real transparency). Crop still works alongside (C).
- **Next (optional):** fold crop into the tool system (one mechanism), fill bucket + shapes, soft/AA brushes (a new mask generator), zoom/pan, or the memory-aware undo you flagged.

## 2026-07-27 — Crop becomes a tool; saving stops eating originals

Matthew asked to spend the session on editing and saving. Both were already
functionally complete at v1, so this was extension: he picked the two
"recommended" forks — fold crop into the tool system, and stop Ctrl+S quietly
degrading source files. Two small, low-risk slices rather than one big one.

**Slice 1 — crop is a `CropTool`.** Painting's tool layer landed last session and
immediately made crop look wrong: the canvas carried a `_crop_mode` flag with its
own press/drag/release/escape handlers *alongside* the tool dispatch, two
mechanisms doing the identical job. `CropTool` now lives in `ui/tk/tools.py` with
the others, and the canvas has exactly one mouse path (`_dispatch`) and one
coordinate mapping. Deleted: `begin_crop`, `_end_crop`, `_clamp_to_image`, the
four `_crop_*` handlers and the mode flag. Design notes in ARCHITECTURE §19.1.

**The fold paid for itself immediately — it fixed a real painting bug.** Crop
cancelled itself on a window resize, because rescaling the image makes any
coordinates collected so far stale. Strokes had *no* such guard: resize mid-drag
and the release would have committed a stroke mapped against the old geometry,
painting in the wrong place. Sharing one dispatch means the canvas now cancels
whatever gesture is in progress, so painting inherited the guard for free. This
is the second time this project has found a bug by making two similar things
actually be the same thing.

Two hooks made that generic instead of crop-specific: `is_gesturing` (is a press
outstanding?) and `on_cancel(ctx)` (abandon, commit nothing) on the `Tool` base.
They also buy **two-stage Esc**: mid-gesture Esc abandons the gesture but keeps
the tool (you meant to redraw the box, not leave crop), otherwise it puts the tool
away. With no tool active the canvas returns `None` so the global Esc still
deselects — the widget bindtag runs before `bind_all`, so an unconditional
`"break"` would have swallowed frame deselection.

Behaviour changes worth knowing: crop is **sticky** now, like every other tool
(it stays selected after a commit, so a second crop is another drag) instead of a
one-shot armed mode; and selecting *any* tool now focuses the canvas, so Esc
lands there. The status-line hint moved onto the tool (`Tool.hint`), which
retired the per-tool if-chain in the frontend. The preview overlay generalised:
`show_stroke_overlay` + `show_rect_overlay`, the latter taking a box in *image*
pixels and labelling it with the image-pixel size — a free rect-select/shape
overlay later.

**Slice 2 — save safety.** Writing a GIF rebuilds the palette and merges
identical consecutive frames; both are unconditional. So Ctrl+S on a
freshly-opened file re-encodes and overwrites the user's original, irreversibly,
and it's one keystroke away at all times. Split along the seam (ARCHITECTURE
§19.2): the controller reports the fact — `overwrites_source` and
`suggested_save_name` — and the frontend owns the policy, a warning dialog with
overwrite / save-elsewhere / cancel whose *default* button is the safe one. The
flag clears on the first write to any path, so it warns once per opened file, not
on every save. `suggested_save_name` suffixes `_edited` idempotently, so saving
twice can't produce `a_edited_edited.gif` (a test insists).

Naming policy sits in the controller on purpose: a second frontend should
inherit "don't clobber the original" rather than reinvent it.

**Testing note worth keeping.** `ui/tk/tools.py` imports no toolkit, so the whole
interaction layer — crop included, now — is testable headlessly against a fake
`ToolContext`: `tests/test_tools.py` is 25 tests with no display. Only the
display↔image mapping still needs Xvfb. That's the boundary rule paying rent.

The modal confirm can't be driven by a scripted run (it would block forever —
same trap as `_invoke_op` on a param op, noted at M4). So it's covered in two
halves: the smoke test stubs the answer and checks the *routing* (Cancel writes
nothing / No diverts to Save As / Yes overwrites and doesn't ask again), and a
throwaway Xvfb probe showed the real dialog and had Tcl click its default button,
proving the `-detail` + `-default no` option set actually constructs and that
Enter picks the safe answer. Guessing that a dialog "probably renders" is how you
ship a `TclError` to the user.

**Numbers:** 299 headless tests (was 269: +25 tools, +11 save-safety, minus none).
Xvfb smoke 110 checks (was 83: +27). Boundary rule still clean.

**Handover**

- On disk, uncommitted. `COMMIT_MSG.txt` refreshed.
- Try it: **C** or Image → Crop, drag, release — then note crop stays armed for a
  second drag; Esc once clears a half-drawn box, Esc again puts the tool away.
  The palette now reads Cursor / Crop / Pencil / Eraser / Eyedropper.
- Then open one of your real GIFs and press **Ctrl+S** — that's the new warning.
  "No" should offer `<name>_edited.gif`. Worth confirming the wording reads right
  to you, since it's the one dialog that stands between you and a lost original.
- **Next (optional):** fill bucket + shape tools (now cheap — one op + one Tool),
  soft/AA brushes, zoom/pan, frame clipboard (cut/copy/paste + insert blank), or
  the `.gifproj` project format you want eventually.
