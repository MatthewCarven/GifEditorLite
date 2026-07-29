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

### Same session — the drawing tool was offset (Matthew spotted it)

Matthew tried the pencil on `Claude.gif` and the tool drew up and to the left of
the cursor. Screenshot made it obvious once measured: at ~29 display pixels per
image pixel, the preview sat visibly off the crosshair. **Two independent
half-pixel errors, compounding in the same direction.** Both predate this
session; blowing pixel art up 30x is what made them visible.

**1. `_display_to_image` rounded when it should floor.** A pixel spans
`[i, i+1)`, so `round()` sends everything past its midpoint to the neighbour:
clicking the *visible centre* of a pixel painted the one to its right. Probed it
before touching anything — 3 of 5 pixel centres mapped to the wrong pixel (and
which ones varied, because Python rounds half to even, so it looked flaky on top
of being wrong).

**2. `_image_to_display` returned the pixel's top-left corner**, so the stroke
preview was drawn half a pixel up-left of where the brush would actually land.

The fix isn't simply "use floor everywhere", because **crop wants the old
behaviour**: a crop box is described by the boundaries *between* pixels, so
rounding to the nearest edge is right for it, and clamping to `0..src` inclusive
is right too. Brushes address pixels; crop addresses the lines between them.
Different questions, so `Tool.coords` is `"pixel"` or `"edge"` and the tool
declares which — see ARCHITECTURE §19.1.1. That also means "pixel" mode stopped
clamping: the paint ops already clip off-canvas points, and clamping had been
smearing a stroke that runs off the edge along the border.

**The test hole this exposed is the real lesson.** The existing paint checks
computed their click points with `_image_to_display` and then asserted through
`_display_to_image` — a round trip through the same wrong assumption, which
passes happily while both halves are wrong together. And the surviving paint
check *still* passed with the bug reverted, because brush size 3 covered the
neighbouring pixel. Tests now: derive widget coordinates by hand from the fit
scale, assert `_image_geom` against `canvas.bbox()` of the real image item
(ground truth), sweep pixel centres *and* points at 2%/50%/98% across each pixel,
and check the preview overlay's drawn bbox is nearer the pixel's centre than its
corner — comparative rather than a tolerance, because an absolute tolerance
passes by luck at low zoom. All confirmed to fail on a reverted copy of the tree
before being called done. A regression test that can't fail is decoration.

**Found while investigating, fixed too:** a `tk.Canvas` with no `scrollregion`
scrolls itself over the bounding box of its items, and then widget coordinates
stop equalling canvas coordinates — offsetting every gesture by the scroll
amount. Nothing in our code scrolls the preview today (the timeline's wheel
bindings are on its own canvas instance, not the Canvas *class*), so this wasn't
Matthew's bug, but it was one stray binding away from being a mystery. `_redraw`
now pins the scrollregion to the visible area, and `_dispatch` goes through
`canvasx`/`canvasy` anyway. Panning, when it arrives, will be an explicit
transform rather than an accident.

**Numbers:** 301 headless tests (+2 tool-coords guards), Xvfb smoke 121 checks
(was 110, +11 mapping checks).

**Handover**

- Take the pencil back to `Claude.gif` at size 1 and check it lands exactly under
  the crosshair now, at the corners of the canvas as well as the middle.
- Crop should feel unchanged — if anything it's the one thing I deliberately
  didn't alter.

---

## 2026-07-27 (later) — a transparency index that pointed past its own palette

**Symptom, as reported:** `claude_blinky.gif` frames 2 and 3 (1-indexed — the
two blink frames) show a transparent background in the editor, and Discord
renders them otherwise. Two decoders, two answers, one file.

**The file is malformed, and we wrote it.** Byte-level parse of the container:

    frame 0: LCT= 8 entries, transparent index 7   ok
    frame 1: LCT= 8 entries, transparent index 8   <- past the end
    frame 2: LCT= 8 entries, transparent index 8   <- past the end
    frame 3: LCT= 8 entries, transparent index 7   ok

Frames 1 and 2 are the blink: they carry a ninth colour (the blue eye block,
`(0,0,255)`), which pushes the reserved transparency slot to index 8 — but the
local colour table was still written with 8 entries. Hand-decoding the LZW
confirms index 8 *is* present in the pixel data, 6816 times, so the question
"what colour is index 8" decides the whole background. The GIF spec doesn't
answer it. Pillow honours the transparency declaration and shows transparent;
Discord evidently resolves it some other way and shows fill. Neither is wrong,
because the file never said.

**Cause, in `gif_write._to_palette`.** We quantise to `colors=255` to leave a
slot free, then paste `_TRANSPARENT_INDEX = 255`. But ADAPTIVE returns *only as
many entries as the frame needs* — an 8-colour frame comes back with an 8-entry
palette, so index 255 was already dangling before the encoder saw it. Pillow's
optimise pass then compacts the used indices (`[0..7, 255]` -> `[0..8]`), remaps
`transparency` to 8, and sizes the colour table from a palette that never grew
to hold it. Fix is one line of intent: pad the quantised palette out to a full
256 entries so the index we're about to use is real.

**It fires on exact powers of two.** A sweep of opaque-colour counts 2..40 plus
63/64/65/127/128/129 failed at precisely 4, 8, 16, 32, 64, 128 before the fix and
nowhere after. `claude_blinky` landed on 8. Every other GIF in the repo written
by the editor happened to miss the mark — which is why this survived 301 tests.

**The test lesson, again.** Round-trip assertions cannot see this: our reader
goes through Pillow, and Pillow composites and resolves the dangling index the
same way both before and after the fix. The bug is only visible in the bytes. So
`test_transparent_index_stays_inside_the_colour_table` parses the container
itself — colour table size and GCE transparency index per frame — and asserts
the index is inside the table, across eight colour counts. Confirmed to fail on
a reverted copy of `_to_palette` before being called done.

**`claude_blinky.gif` repaired in place, losslessly.** Padded the two undersized
local colour tables from 8 to 16 entries so index 8 is a legal (black,
transparent) slot; pixel data and LZW streams untouched. Verified: alpha shape
and every visible colour identical to the original render, differences confined
to the RGB of fully-transparent pixels. The pre-repair bytes are kept as
`claude_blinky_broken.gif` — delete it once you're happy, or keep it as a
decoder-disagreement fixture.

**Also, while here:** `tools/countdown_gif.py`, a standalone MM:SS countdown
generator (Pillow only, no giflite import). Its first version had a *different*
palette bug worth recording, because it's the trap next door: per-frame ADAPTIVE
palettes plus `optimize=True` produced partial delta frames each carrying its own
local colour table, so a decoder that composites in index space reads the
untouched pixels through the wrong table — white background, outlined digits.
Now: one fixed bg->fg ramp as the global colour table, full frames,
`optimize=False`.

**Numbers:** 302 headless tests (+1 container-level transparency guard).

**Handover**

- Reopen `claude_blinky.gif` in the editor, then drop it in Discord — both
  should now show the same transparent background.
- Worth a look: `gif_write` still emits a local colour table per frame. That is
  harmless while frames stay full-canvas (they do — `disposal=2`), but it is the
  same ingredient as the countdown bug, so if we ever let the encoder crop to
  deltas, a document-wide palette needs to land first.

---

## 2026-07-27 (later still) — the two "possible follow-ups", one of which was worse than reported

Both had been parked at the bottom of Save safety as nice-to-haves. Design in
ARCHITECTURE §19.2–19.3.

**1. A clean Ctrl+S was a lossy no-op.** Saving re-encodes: rebuilt palette,
merged holds. Doing that with nothing to save spends the original and buys
nothing. `save_would_change_nothing` is true when there's a path and no unsaved
edits, and `save()` *acts* on it rather than merely reporting it — the split we
normally keep (controller reports, frontend decides) is right for policy, but
this one is protection, and a second frontend shouldn't be able to lose
someone's file by forgetting to ask. `save_as` is deliberately untouched: naming
a destination is a different request, and "save a copy" with no edits must
still write.

Two subtleties worth the words. A skipped save returns `True` — the caller asked
for disk to match the document, and it does; returning `False` would send the
frontend into Save As. And it leaves `overwrites_source` alone: that flag says
"an untouched original is still out there", so a save that wrote nothing must
not spend the one warning we get.

**2. The single-key shortcuts were doing more damage than the report said.**
Matthew flagged B/E/I/C switching tools while the brush Size box has focus. The
guard is the same for all bare keys, so I bound the lot through
`_bind_bare_key` — and the revert check showed what was really happening in that
spinbox: BackSpace and Delete deleted **two frames** (8 → 6), space toggled
playback, Home/End threw the playhead across the strip. Tool-switching was the
mildest symptom of the set. `bind_all` fires after the focused widget's class
binding, so the keystroke did its text-editing job *and* ours.

The handler returns `None` rather than `"break"` when it stands down: the field
has already had the key by then, and other listeners still deserve their turn —
a param dialog's own Escape, for instance. Ctrl-combinations stay bound raw;
they don't collide with typing. Rule for anything added later, now in §19.3: no
modifier, use `_bind_bare_key`.

**A test that would have lied.** The obvious assertion for the save skip is
"the file's bytes are unchanged" — and it passes against a *broken* build,
because `make_gif`'s art is a handful of flat colours that our writer reproduces
byte-for-byte. Re-encoding it is genuinely a no-op, so the test proves nothing
about whether we wrote. Replaced with two: a monkeypatched `writer_for` spy that
asserts the encoder is never reached (can't be fooled), plus a bytes check
against a purpose-built many-colour gradient GIF that really does re-quantise
differently. Both fail on a reverted controller; so does `test_it_says_so` and
the new `overwrites_source` guard.

One existing test legitimately changed behaviour:
`test_overwriting_the_source_clears_it_too` used to call `save()` on a freshly
opened document and expect a write. It now makes an edit first. That's the fix
working, not a test bent to fit.

**Numbers:** 312 headless tests (was 302), Xvfb smoke 132 checks (was 110).
Every new smoke check confirmed to fail on a reverted tree.

**Handover**

- Try the brush Size box: type a number, backspace over it, confirm your frames
  are still there. That's the one to sanity-check by hand.
- Ctrl+S twice in a row on a freshly opened file: the first should warn, the
  second should say "No changes to save" and not touch the disk.
- Next slice is still open — zoom/pan, or fill + shape tools, whichever you fancy.

---

## 2026-07-28 — Zoom and pan, slice 1

Matthew picked zoom/pan over fill+shapes, then picked **buttons only** for the
input — no wheel, no drag. That turned out to be a better call than it looked:
the mouse stays entirely the tools', so no view gesture can land inside a
stroke, and the fiddliest part of the feature (zoom-to-cursor anchoring) simply
doesn't exist yet. Design in ARCHITECTURE §20.

Also, before any of that: the previous session's commit had gone in with only
`tools/countdown_gif.py` in it. `git add tools/` staged that directory and
nothing else, so the nine modified tracked files were left behind — and pushed
that way. Same split as `a7a5e2c`/`8fa0775` the session before. Amended and
force-pushed as `3d7b804`. Worth noticing as a pattern rather than an accident:
the handover says "commit", the obvious `git add <the new thing>` is not the
same as `git add -A`, and the commit succeeds either way.

**The seam paid rent for the second time.** `PreviewCanvas._image_geom` was
already the single tuple every coordinate mapping read through, so making the
transform *produce* that tuple meant `tools.py` needed **no changes at all** —
crop, pencil, eraser and eyedropper all work at 3200% without knowing zoom
exists. The first time was crop folding into the tool layer; this is the same
dividend from the same discipline.

**Two representation choices did most of the work.** Scale is `None` for fit
rather than a number, because fit has to *stay* fit across window resizes and
canvas ops — a baked float holds 37.4% while the window grows around it. And pan
is stored as the image point held at the viewport centre rather than a pixel
offset: the centre is invariant under zoom, so zooming holds your place for
free, and re-clamping after a crop is one clamp of a point into new bounds.

**The real work was rendering.** The old path resized the whole source; at 32x
that is roughly a gigabyte of RGBA for a modest GIF, plus the checkerboard
behind it. Now the renderer intersects the image rect with the viewport, maps
back to whole source pixels, and crops-then-scales only that. The smoke test
asserts this against the real bitmap rather than in theory: at 3200% the
composed photo is 960×512 against a 900px viewport, where the whole image would
have been 5120px wide.

Three details there are each a visible bug if reversed, and only one of them was
obvious going in:

- The crop lands on whole source pixels and the sub-pixel remainder is carried
  by *placement*. Folding it into the resample is what makes upscaled pixel art
  shimmer as it moves.
- The checkerboard needed a **phase offset**. Without it the backing is
  generated from the crop's origin, so the pattern slides underneath a
  transparent GIF every time you pan — and with 25%-of-a-viewport button steps
  that is a jump, not a drift. It reads as the artwork moving rather than the
  view. Cost nothing: the phase crop replaced the `.copy()` the composite needed
  anyway.
- At fit the visible rectangle is the whole image, so the fit path and its cache
  keys are what they were before zoom existed. Playback still runs off cached
  bitmaps.

**A view change is the resize bug wearing a hat.** `<Configure>` has cancelled
in-progress gestures since crop existed, because a resize moves and rescales the
image and coordinates already collected now map elsewhere. A zoom or a pan is
the identical staleness — and reachable, since Ctrl+- fires happily mid-stroke.
Every view change now funnels through one `_apply_view` that cancels first,
rather than each entry point remembering to. Recognised from the pattern rather
than found by a bug report, which is the cheap way to get these.

**A test that was right for the wrong reason.** `_axis_origin` clamps the origin
so no pasteboard shows on an axis with image to spare — and deleting that clamp
broke *nothing*, because `_clamp` runs after every mutation and a centre already
in range yields an origin already in range. I'd assumed the two scales in play
(the requested one, and `width // source` after truncation) would disagree
enough to make it bite; instrumenting it showed the rounding cancels exactly.
So the clamp was untested code that happened to be right. It stays — the next
pan input would be a drag setting a centre from raw mouse deltas, and that is
where it lands — but it is now held to that contract by a direct test, and the
docstring says which. Untested-but-correct is a worse state than either
alternative.

**Numbers:** 353 headless tests (was 312), Xvfb smoke 151 checks (was 132). Four
separate mutations of the production code were confirmed to break the new
checks: composing the whole image instead of the visible rect, dropping the
gesture cancel, placing the slice without its crop offset, and resetting the
view on every doc change rather than only on open. Screenshotted at 3200% and at
3200%-plus-pan to confirm it looks right and not merely arithmetically
consistent.

**Handover**

- Slice 1 is menu- and keyboard-driven: **Ctrl+= / Ctrl+- / Ctrl+0 / Ctrl+1**,
  and a View menu. The toolbar cluster and the pan buttons are slice 2, so
  panning currently has no UI at all — `canvas.pan()` exists and is tested, but
  nothing calls it outside the smoke.
- Worth a hand check: zoom to 800% on a pixel-art GIF and paint. The mapping is
  covered at 3200% in the smoke, but you have the eyes for whether the brush
  sits under the cursor.
- Also: Ctrl+- in the middle of a stroke should abandon it, not commit a
  half-stroke somewhere unexpected.

---

## 2026-07-28 (later) — The toolbar didn't fit, so the minimap happened

Slice 2 was meant to be a zoom/pan cluster on the right of the toolbar. It
doesn't fit: that row wants **1087px and gets 900**, and `pack` dropped the
last three widgets — the `−`, the readout and the `+` — with no error at all.
The smoke test passed the whole time, because `invoke()` works fine on a widget
that was never mapped. A screenshot caught it. That's the lesson worth keeping:
**Tk's geometry managers fail silently, so a layout change needs an eye on it,
not just a green run.** Design in ARCHITECTURE §21.

Told Matthew the numbers and offered four relocations; he proposed a minimap
instead, and it's a better control than the buttons I was defending:

- Buttons give **motion without position**. At 3200% on an 82px GIF you can see
  about 28 pixels and nothing tells you which 28.
- The map makes the control and the readout **the same object**.
- It keeps the property that made buttons-only attractive in the first place —
  the preview's mouse stays entirely the tools'. A drag in the map is not a
  gesture on the canvas, so there is still nothing that can land inside a
  stroke. Buttons-only was a trade; this isn't.

So the toolbar goes back to exactly what it was, and everything view-related
moved into a right-hand panel: map on top, then `−  [zoom]  +`, then Fit / 1:1.
Shown only when zoomed in, since at fit the rectangle covers the whole image
and is therefore saying nothing.

**The refactor it forced was worth having anyway.** `MiniMap` needed the same
screen↔image arithmetic as the preview but against a *different* geometry, and
duplicating it is precisely how the two half-pixel bugs in §19.1 come back on
one side only. So `image_to_display` / `display_to_image` moved out of the
canvas onto `ViewTransform`, which delegates now. That also exposed a weak test
I'd written this morning: `tests/test_view.py` had local *copies* of those two
functions and tested those. A copy agreeing with itself proves the arithmetic is
self-consistent, not that it matches what the canvas does. They now exercise the
real methods.

**A guard I called unreachable stopped being hypothetical.** This morning I kept
the clamp in `_axis_origin` with the note that "the next pan input would set a
centre from raw mouse deltas, and that is where it lands". The navigator is
exactly that input, eight hours later. And the interesting part: deleting
`center_on`'s own clamp leaves the *rendering* correct, because `_axis_origin`
catches it — the stored centre is out of range but nothing looks wrong, which is
the invisible-trap case §20.2 warns about (the next zoom-out resolves it into a
jump). Both clamps are now checked, at both levels: headlessly for the stored
centre, in the smoke for the geometry. Neither is decoration.

**A real bug the panel surfaced.** `canvas.on_view_change` pointed at the
controls-only refresh, so showing or hiding the panel — which resizes the canvas
and re-fits it — left the status line holding a percentage the panel readout had
already moved past. Caught by an *existing* check rather than a new one, which
is the nicest way for it to happen. Both derive from the same state, so both
refresh together now.

**Dead API, noted rather than deleted.** `can_pan_x` / `can_pan_y` existed for
the pan buttons' enabled state and now have no production caller — the map
answers "is there more over there?" by drawing a rectangle smaller than the
image. Kept, with the docstring saying plainly that nothing calls them and why:
they're the question any other pan input has to ask, they're three lines, and
they're covered. Flagged in TODO so it's a decision rather than an oversight.

**Numbers:** 360 headless (was 353), Xvfb smoke 170 checks (was 151). Four
mutations confirmed to break the new checks: never hiding the panel, not
refreshing the map after a view change, reintroducing the status staleness, and
removing both clamps. Screenshotted with the panel open at 1600%.

**Handover**

- The map is drag-to-pan: press or drag anywhere on it. Worth checking it feels
  right — absolute pointing was a deliberate choice over a relative grab, and
  that's a matter of taste as much as correctness.
- The panel appears and disappears as you cross fit. If that turns out to be
  jumpy in real use, `_show_view_panel` is the one place to change it.
- Panel width is 168px, minimap height 120px, both constants at the top of
  `app.py` if they feel wrong.

## 2026-07-29 — The pixel grid

Matthew, after confirming zoom worked: "I'd like to have an option to display
gridlines if zoomed in beyond 4x like?" Design in ARCHITECTURE §22.

**Three states rather than a checkbox, at Matthew's choice.** Off / Auto (from
400%) / Always (from 200%). The interesting part is that `Always` still has a
floor. At 1:1 the rules touch, so the grid becomes a flat fill that says nothing
while costing one canvas item per source pixel on every redraw — during
playback, at 60fps. Calling that "always" would have been honest about the label
and dishonest about the result. 2x is the last rung where cells are still cells,
and the floor is documented rather than hidden.

**The grid asks the same function the tools ask.** `grid_lines()` generates rules
by calling `image_to_display`, not by walking `left + i * scale`. That is the
whole reason it went in `view.py`: §19.1 is the record of what two derivations
of one coordinate cost, and a grid half a pixel off from the pixels it claims to
divide is worse than no grid, because you'd trust it. Same call the crop marquee
makes, `center=False`, so the two agree about where a pixel ends by construction
rather than by coincidence.

**Canvas items, not baked pixels.** Drawing the rules into the composed bitmap
would have kept the cache doing the work — but it would also have meant deriving
their positions a second time, from the crop rect and the resample placement.
And the thing baking would have bought was never at risk: `visible_source_rect`
already bounds the count by the viewport, so 32x on a 4000px image is the same
few dozen items as 32x on a 40px one. Checked in the smoke rather than assumed.

**Float dust I chose not to round away.** `image_to_display` computes
`ix / sw * fw`, and panned onto pixel 287 of a 400px source at 8x a boundary
comes back as -3.999999999999993 instead of -4.0. It is 1e-14 of a screen pixel
and Tk rounds it off. The tempting fix is to round in `grid_lines`, and that is
exactly how the grid would stop agreeing *exactly* with the mapping. So the
floats stay and the test asserts `abs(v - round(v)) < 0.01`, with the reasoning
written into the test — dust and a genuine half-pixel are different failures and
the assertion has to be able to tell them apart.

**Two tests were wrong before the code was.** The pan test read both grid
snapshots back through the *final* geometry, so it compared a value with itself
and passed for the wrong reason — the same shape of hole as the one §19.1 closed
(click points derived through the function they were meant to check). It now
samples the rules and their expected positions together, via a local helper that
makes doing it wrong awkward. And the smoke assumed fit meant "zoomed out": this
160x80 GIF fits at 552%, comfortably past the 4x threshold, so `Auto` correctly
drew a grid the check had declared impossible. Both were mine, both surfaced
before anything shipped, and the second is a real consequence worth knowing —
with `Auto`, a small GIF opens with the grid already on.

**The gesture funnel, again.** The grid moves nothing, so cancelling a stroke for
it reads as overkill. It isn't: `_draw` starts with `delete("all")`, which takes
the overlay with it while `_overlay_items` keeps holding the ids. A gesture that
survives a redraw is a gesture whose preview has vanished. Through `_apply_view`
it goes, like every other view change.

**A setting that does nothing says so.** Two of the three modes can be switched
on with no visible effect. The status line reports "Auto (from 400%) - not shown
at 200%". Written straight to the label rather than through the controller's
STATUS bus, because the grid never reaches the controller (§9) and borrowing the
bus for a frontend-only setting would be the first crack in that.

**Numbers:** 376 headless (was 360), Xvfb smoke 187 checks (was 170). Five
mutations confirmed to break the new checks: rules on pixel centres instead of
boundaries (3 headless + 1 smoke), `Always` losing its floor, the grid spanning
the viewport instead of the image, the toggle skipping `_apply_view` (2 smoke),
and the shortcut path not writing the menu variable. Screenshotted at 2013% on
`Claude_Mad.gif`.

**Handover**

- `GRID_COLOR` / `GRID_STIPPLE` at the top of `canvas.py` if the rules are too
  faint or too loud — over the orange body they read well, over the red they are
  subtle. That is a matter of taste and it is two constants.
- `GRID_AUTO_SCALE` (4.0) is the "beyond 4x" you asked for. If a small GIF
  opening with the grid already on annoys you, that is the constant.
- Ctrl+' cycles Off -> Auto -> Always. The View > Pixel Grid submenu sets it
  directly and reports the current mode.

## 2026-07-29 (later) — Fill, shapes, and the palette moving house

Matthew picked fill + shape tools as the next slice, then made three design
calls: a Fill checkbox rather than separate filled/outline tools, a tolerance
box on the bucket, and the palette moved into the view panel. The third was the
consequential one. Design in ARCHITECTURE §23.

**The §19 mask bet paid, and that is the headline.** "The brush is a mask" was
written to make a soft brush cheap later. It turned out to make two *different*
things cheap: fill and shapes cost one mask generator each and nothing else.
`_apply_stroke` became a thin caller of a new `_apply_mask`, so alpha
compositing, immutability, fresh uids, the decline convention and the playhead
rule are now stated once and inherited three times instead of being written out
three times and got wrong in one of them. Three ops, one commit path.

**Fill splits the question in two.** *Which pixels match* is colour, answered in
whole-image Pillow ops (C). *Which of those are reachable* is connectivity,
answered by `ImageDraw.floodfill` walking the match mask. The trick that makes
the second stage free: flood with 128, and the pixels still at 255 are exactly
the matching-but-unreachable ones. Which also means the global "replace every
matching pixel" variant, if ever wanted, is this function with the flood
deleted. Measured cost is ~1µs/pixel — instant at GIF sizes, 1.1s for a
whole-canvas fill at 1000x1000. Noted rather than optimised.

**Tolerance is Chebyshev on purpose.** Largest single-channel difference, so
"tolerance 8" means "no channel differs by more than 8" — a sentence you can
hold in your head, unlike a radius in RGBA space.

**Shapes are pixel-inclusive; crop is not.** A shape addresses the pixels it
covers, a crop box the boundaries between them. The conversion has to be undone
in exactly one place: the marquee draws through pixel *corners*, so a shape's
far edge is pushed out by one, or the box you drew is a pixel short of the box
you get. `ShapeTool.preview_box` is that adjustment, static so it is testable
without a gesture, and both the headless and the display test check it.

**The toolbar couldn't take nine tools, so it stopped being a toolbar.** §21
already recorded what that row does when it runs out of width — pack drops
widgets with no error, and the 480px minimum makes it permanent. The palette
moved into the strip beside the preview, which is now the side panel: tools and
settings always, view section conditional. The top row is gone, which hands the
preview back ~34px of height.

**And the failure came straight back on the other axis.** A column runs out of
*height*, and at the 480x400 minimum it does: the panel wanted 412px and had
238. Pack's answer was to keep the map and silently drop the zoom row. Half a
navigator, no error, exactly the shape of the original bug. `_view_section_fits`
makes it a decision — whole section or none of it, because the half that remains
looks like it works. I only found this because I went looking; the lesson from
§21 is that this window will not tell you.

**Two of my own tests were wrong again, in the same way as this morning.** The
`coords` test hardcoded its list of pixel tools, so adding four tools left it
passing while checking none of them — it derives from the palette now. And the
new smoke check for the committed rect drew it in the colour I had just flooded
the entire frame with, so it asserted nothing at all; it passed on a build where
the shape op did nothing. Both are the same failure mode: a test that still runs
but has quietly stopped covering its subject.

**A cheap win I did not expect.** The immutability suite's
`test_every_registered_op_is_covered_here` failed the moment the new ops
registered, before I had written a line of test for them. That guard has been
sitting there since M2 doing nothing visible; this is what it was for. Fill is a
sharper case for it than any previous op, too — it is the only one that *reads*
the frame it is about to paint.

**Numbers:** 426 headless (was 376), Xvfb smoke 211 checks (was 187). Six
mutations confirmed to break the new checks: fill losing its connectivity stage
(becoming a global replace), the shape box treated as exclusive, the marquee
forgetting its +1, the panel fit guard dropped, tolerance ignored, and the fill
tool committing on drag as well as press.

**Handover**

- Keys: F fill, L line, R rect, O ellipse, alongside the existing C/B/E/I. All
  yield to a focused text field, so typing in Size or Tol. is safe.
- The preview no longer widens when you return to fit — the palette lives there
  permanently now. Deliberate, but it is a visible change and you may dislike it.
- `PANEL_WIDTH` is 200 (was 168); the strip measures ~211px in practice because
  of the Colour/Fill row. If it feels wide, that row is where the width is.
- Fill on a photographic or dithered GIF will want a tolerance well above 0.
  Flat pixel art wants exactly 0.

## 2026-07-29 (later still) — Per-frame timing, made reachable

Matthew: "is there a way to set the individual frame time? It would make it
easier for editing instead of inserting duplicate frames." Right instinct —
duplicating to hold a pose bloats the file and multiplies the work of every
later edit. Design in ARCHITECTURE §24.

**No new op.** `timing.set_delay` has been there since M4; what was missing was
a way to reach it that wasn't a menu and a dialog. So this slice is a delay box
in the panel, the frame's delay in the status line, and every frame's delay
under its thumbnail — three surfaces onto one op.

**A correction worth making early:** the status line's `0.10s` was the *total*
animation duration, not the frame's. They coincide on a one-frame GIF, which is
how it went unnoticed. Nothing on screen reported a single frame's timing at
all, so Matthew was right that it was missing even though a number was sitting
right there.

**The scope rule took two goes and both halves matter.** The menu op reads "no
selection" as "all frames", which is fine behind a dialog and wrong for a box
that sits next to the frame counter reading "this frame". So: selection, or the
playhead frame, never everything.

Then the tests found the second half. Opening a file selects frame 0, and
`seek`/`step` deliberately leave the selection alone — so arrow to frame 3 and
frame 0 is still selected. A box keyed on the selection alone would have
reported and edited frame 0's delay while the preview *and* the status line both
showed frame 3. Now the selection counts only when the playhead is standing
inside it. I found that because a test failed for what looked like a boring
fixture reason (`delay_targets == (0,)`, expected `(2,)`) and was worth reading
properly instead of patching the test.

**A decline the timing ops never had.** Every other op family returns the same
document when nothing changed, so `run_op` says "nothing to do" instead of
pushing an identity snapshot onto undo. Both timing ops did `replace(doc, ...)`
unconditionally. Invisible for three milestones, because the only way in was a
dialog and nobody opens one to retype the value already in it — an inline box
asks on every focus-out. `_retimed` compares *results*, not requests, so "typed
103ms, already 100ms" is caught too.

**Two tests with no teeth, caught by mutation.** The obvious check — "an
unchanged commit doesn't change `undo_label`" — passed against a build with the
decline removed, because the previous edit was also a delay edit and the label
read the same either way. Making the labels differ *still* didn't help: with the
frontend guard and the op decline both present, no undo entry appears in any of
the three worlds, so the assertion couldn't distinguish them at all. The display
test now asserts the one thing it can actually see (reaching the op would print
"nothing to do"), and the decline is covered headlessly, which is its proper
layer. Verified by mutating each guard separately.

**The timeline labels are the surface that earns its keep.** A number in a box
tells you about one frame; a row of numbers tells you *which* frame is wrong.
On a 100/100/800/100/40/100 test GIF the 800 and the 40 jump straight out of the
strip. Drawn inside `_draw_slot`, so they inherit the virtualisation for free.

**Closed a long-deferred item while here:** `default_params` now seeds the Set
Delay dialog from the frames it would change, instead of a static 100ms. Mixed
selections seed from the shortest — retiming a mixed run is nearly always about
slowing part of it down, and the minimum is a delay some frame actually has
rather than a number invented for the box.

**Numbers:** 452 headless (was 426), Xvfb smoke 224 checks (was 211). Four
mutations confirmed to break the new checks — the scope ignoring the playhead,
the scope falling back to all frames, the timing ops losing their decline, and a
mixed selection showing one frame's value instead of blank — plus the two
teethless checks above, each re-verified after being rewritten.

**Handover**

- The box means "the selected frames, if you're standing in the selection;
  otherwise this frame". The label tells you which — it reads "Frame delay
  (4 frames)" when it would change four.
- Type 333 and you get 330; type 1 and you get 20. GIF quantises to 10ms with a
  20ms floor, and the box shows what landed rather than what you typed.
- Timeline labels show ms under a second and seconds above it.

## 2026-07-29 (evening) — Image-sequence IO, and the registry it forced

The one Matthew flagged as "very bespoke". Design in ARCHITECTURE §25.

**A folder is what broke the dict, and it isn't about there being more
formats.** `.webp` and `.apng` would have been two more keys. A folder has no
suffix, so `READERS[path.suffix]` has nothing to look up at all. The reader
*signature* survives untouched — a directory is still a `Path` — so the change
is narrower than "promote the dict to a registry" sounds: only dispatch moves,
from indexing by extension to asking each format whether it claims the path.

`available()` finally has a customer too, or nearly: it's still M5's video
import that needs it, but the guarantee is now tested with a format that reports
itself missing, rather than being a promise in a docstring.

**The trap I went looking for first.** `frame1, frame10, frame2` is what plain
sorting gives you, and it looks perfect on a nine-frame test folder. Natural
sort on the way in; zero-padded names on the way out — the padding isn't for us
(we natural-sort anyway) but for every file manager and shell that doesn't.
Both have tests that fail on a reverted sort.

**Padding, not scaling, for mismatched sizes.** Matthew picked union-and-pad.
Top-left rather than centred, which is the detail worth recording: a mismatched
sequence is nearly always one where something grew at the right or bottom, and
centring would shift *every* frame including the ones that were already correct.

**Import is not open; export is not save.** One field each. An opened file is a
document's home and Save writes back to it; an imported folder is a source, and
setting `_path` to it would aim Ctrl+S at writing a GIF over somebody's PNGs. So
import leaves the path None and Save falls through to Save As. Export leaves
path, dirty and history alone — writing a copy of your frames somewhere isn't
the claim "this document now lives here".

**The manifest is the deferred project format, unzipped.** §18 described
`.gifproj` as PNGs plus a JSON manifest in a container; an exported folder is
that minus the zip. Designed once, versioned from the first line of existing,
and refusing an unknown version rather than guessing. It also gives folders the
thing GIF can't: identical consecutive frames stay separate instead of being
merged into one longer hold.

**`ask_params` split into `ask_values`.** Operations had stopped being the only
things with parameters — a format's reader declares what the source can't tell
it. The `Param` schema was always general enough; only the function signature
wasn't.

**A bug I introduced and a correction to how I explained it.** Adding two File
menu entries broke `_refresh_file_menu`, which configured `(2, 3, 5)` by index.
I fixed it by label and wrote a confident comment saying the old code would have
failed *silently*. The mutation run disproved my own comment: index 3 had become
a separator, separators have no `-state`, and Tk raises `TclError` the moment the
File menu opens. Loud, not silent. The fix still stands and the comment now says
why — the loudness was luck, and one entry earlier it would have been exactly as
quiet as I claimed.

**Two smoke checks needed rewriting before they meant anything.** The first
asserted Export was disabled while a document was open (it wasn't, and menu state
is only recomputed by the postcommand anyway). The second checked one label, and
the old hardcoded indices happen to include the one Export now sits at — so it
passed against the broken build. Checking *all four* document-dependent entries
is what bites, because Save and Close are the ones those indices stopped
reaching.

**And one check I had to delete.** A failing import emits ERROR, `_on_error`
raises a modal messagebox, and a modal hangs a scripted run forever. It hung this
one until I removed it. Covered headlessly instead, which is where it belonged.

**Numbers:** 497 headless (was 452), Xvfb smoke 244 checks (was 224). Six
mutations confirmed: lexicographic sort, canvas from the first frame instead of
the union, unpadded export names, import setting a path, export clearing dirty,
and the menu indices.

**Handover**

- File > Import Frames… asks for a delay and a loop count, then loads the
  folder. File > Export Frames… writes `frame_0001.png` upward plus
  `giflite.json`, and warns if the folder already holds images.
- An imported document has no path on purpose — Ctrl+S will ask you where to
  put it rather than writing over your PNGs.
- A folder we exported re-imports with its exact timing; a folder from anywhere
  else uses the delay you type.

## 2026-07-29 (night) — "Empty" was not one colour

Matthew, using the editor in anger, asked for select/copy/paste and added "oh
and fill empty". I went to confirm that filling transparent areas already
worked — there was a passing test for it — and found it works right up until you
erase anything.

**The bug.** A GIF's transparent pixels carry the RGB of the transparent palette
index. `paint.erase` pulls alpha down and deliberately leaves RGB alone. So
after erasing, a frame holds two runs of pixels that are *identical on screen* —
both checkerboard — and numerically different: on `Claude_Glasses.gif`,
`(216,118,86,0)` where the file was transparent and `(69,73,77,0)` where I had
just erased. The four-channel colour match stopped dead at the join, and nothing
on screen could explain why, because on screen there is nothing there.

**"So I just didn't have the tolerance high enough?"** Technically yes — 147
would have crossed it in that case. But that is the wrong framing and worth
recording as such: the number needed is derived from the colour that *used to
be* under pixels the user has already erased. It is invisible, unguessable, and
different every time. Asking someone to tune a threshold against data they
cannot see is not a setting, it is a puzzle.

**The fix is one branch.** Seeded on a fully transparent pixel, the match reads
alpha only: every invisible pixel matches, whatever RGB sits under it. Alpha
zero means the other three channels describe a pixel you cannot see, so treating
them as significant was the bug and ignoring them is the fix. An opaque seed
behaves exactly as before — it is a branch, not a replacement.

Measured on the real file: one click now fills all 8871 empty pixels including
the erased hole, and bleeds into 0 visible pixels.

**Numbers:** 502 headless (was 497). Two mutations confirmed: reverting the
branch (3 failures) and making `_clear_mask` match everything rather than only
alpha zero (1 failure — the check that the fill still respects a wall of opaque
pixels with the *same* RGB as the transparent ones).

**Next session:** select / copy / paste, sliced. Matthew chose a floating
draggable paste, paste-into-every-selected-frame, and no clipping of the paint
tools. Slice 1 is select + copy + cut + paste-in-place; slice 2 makes the paste
floating. The wrinkle to design carefully: a region selection is the first piece
of UI state here that *persists* rather than being a gesture, so it belongs in
the controller beside the frame `Selection`, and the canvas gains its first
non-provisional overlay.

## 2026-07-29 (later) — Select, copy, cut, paste-in-place (slice 1)

Design in ARCHITECTURE §26. Three decisions were Matthew's, asked before any
code: cut clears only the frame you are on, paste reuses the delay box's scope
rule, and slice 2 (floating paste) is a separate session.

**The shape of it.** `Region` joined `Selection` in `core/model.py` — same
module, other axis: which pixels rather than which frames. Edge coordinates, so
it is literally the argument list `canvas.crop` takes, which bought the marquee
needing no `preview_box` correction and a test asserting that the same drag
through `SelectTool` and `CropTool` gives the same rectangle. Region and
clipboard live in the controller as session state; `paint.cut` and `paint.paste`
are two more mask generators through the existing commit path.

**Three things this slice found rather than built.**

*1. A premultiplication bug that had been correct by accident.* `_composite`
built its stroke with `Image.paste(colour, mask)`. `paste` with a mask blends
every channel — `dst*(1-m) + src*m` — so a mask of 128 produced
`(r/2, g/2, b/2, 128)`: premultiplied colour in a straight-alpha image, which
`alpha_composite` then multiplies by the alpha *again*. Black at half coverage
over white came out around 64 instead of 128.

It never mattered, because every mask in this codebase is hard: 0 or 255, and at
255 the blend is an exact copy. §19 promised that a soft or anti-aliased brush
would be "a feathered mask and nothing else changes", and this is the line that
would have made that false — the promise would have been broken by the first
person to implement the thing it was written to reassure them about. A pasted
sprite with a partly transparent edge is the first soft mask the project has
ever produced. Fixed by setting the stroke's alpha rather than blending it in;
all 502 existing tests unmoved, which is the evidence that hard masks are
genuinely unaffected.

*2. The playhead rule had been worked around, not satisfied.* `run_op` sends the
playhead to `result.selection.first`, and the painting ops have quietly returned
`Selection.single(index)` since M4 to stop it moving — throwing away the user's
frame selection to do it. Invisible for a one-frame op; fatal for paste, where
collapsing the selection means the *second* paste hits one frame instead of
twenty-one. `OpResult` gained an optional `index`. Small change, and it turns a
workaround that was never written down into something an op can just say.

*3. Ctrl+C belongs to whatever is being typed in.* `_bind_bare_key` guarded
unmodified keys on the theory that only those collide with text entry. They are
not: `bind_all` fires after the widget's class binding, so Ctrl+C in the Size
spinbox would have copied the number *and* replaced the image clipboard with a
rectangle of canvas — with nothing on screen reporting what the clipboard holds,
so it would only surface as a wrong paste some minutes later. Renamed
`_bind_guarded_key`; the real rule is "does the focused widget have a better
claim on this keystroke", and the modifier was never the test. Smoke check added
in both directions (in the box: no; on the canvas: yes).

**Esc is now four stages** — gesture, tool, region, frames — ordered by how
recent each commitment is. Region before frames because the region is the thing
you can see on the canvas you are looking at.

**Deliberately not done:** `Delete` still deletes frames with a region selected,
rather than clearing the region's pixels. Arguably a footgun; changing what an
existing destructive shortcut does is not a change to make in passing. Noted in
TODO.

**Found while screenshotting, pre-existing:** at the 480x400 minimum window the
Frame delay section is silently amputated by `pack` — §23.5 happening to a
section that has no `_view_section_fits`-style guard. Measured against the
palette at both nine and ten tools and it is identical, so this slice did not
cause it and did not worsen it (nine and ten tools both fill five rows of two).
In TODO.

**Numbers:** 579 headless (was 502), 269 smoke checks (was 232). Eight
production mutations confirmed to break the new checks: the premultiply revert,
`run_op` ignoring `OpResult.index`, `Region.clamped` as a no-op,
`_apply_mask_frames` rewriting unchanged frames, the region clamp dropped from
`_emit_doc_changed`, `SelectTool` asking for pixel coordinates, cut spreading to
every target frame, and paste ignoring `frame_targets`.

One test wrote itself wrong and is worth remembering: the first `region_items()`
in the smoke test filtered marquee rectangles on "has a dash", which finds one
of the two ants rectangles (the other is the solid dark one underneath) and
would have passed while asserting half of what it named. Filtering by outline
colour fixed it. Same failure mode as the `coords` test that stopped covering
four tools without going red.

## 2026-07-29 (later still) — Erase mode

Matthew, using the editor: *"what colour do i fill with to get transparent? or
what do i do with the eraser tool to make it flood fill?"*

Neither works, and the first cannot: painting alpha-composites, so a fully
transparent colour contributes nothing and the op declines. Alpha is removed by
subtraction, which is the `"erase"` branch `_composite` has had since M4 behind
exactly one tool. The answer is not a colour, it is the other branch — so the
feature is an **Erase checkbox** (his call, from three options) applying to
every painting tool at once, rather than a second bucket in the palette.

Design in ARCHITECTURE §27. It reaches the tools two ways and that is not sloppy:
strokes *swap the op*, because Pencil and Eraser have been two ops since M4, and
fill and shapes take a `mode` param because they have one op each. The mask
generators are untouched — "which pixels" and "what happens to them" were
already separate questions, and this is the first thing to answer the second one
differently. Erase-shapes came along free, which is the short way to clear a
rectangle.

**The subtle one:** `StrokeTool._erasing` is `self.erase or ctx.erase_mode`.
Reading the flag alone turns the *Eraser* into a pencil whenever the box is off
— an inversion that reads as a double-negative bug long before it reads as a
design error. Mutation-tested.

**The undo menu had to be told.** "Undo Fill" after removing pixels describes the
implementation and misdescribes the action, in the one place a user looks to
find out what they just did. `OpResult` could not carry it — the label is wanted
whether the op applies *or declines* — so the op names the run through an
optional `label_for(**params)` hook, resolved by a new `op_label`. Same shape as
the `default_params` hook that already existed.

**Two mutations were not caught, and they failed differently.**

A `_mode()` helper normalised anything that was not `"erase"` to `"paint"`.
Replacing its body with `return mode` broke nothing, because `_composite`
compares `mode == "erase"` and had already decided. Defence in front of a wall.
Deleted; the reasoning moved to the comparison that makes the choice, and
mutating *that* is caught.

The second was a real hole: every label test called `op_label` directly, so
`run_op` reverting to `op.label` passed everything. "`op_label` is correct" and
"`run_op` uses it" are two claims and only one was tested. `TestUndoLabels` is
the second. Worth remembering as the pattern — a helper tested in isolation says
nothing about its caller.

**Layout, measured before trusting it** (§21/§23.5 twice bitten): Fill and Erase
went on a row of their own rather than as a fourth widget beside the swatch.
Panel height 406 → 427 with 505 available at the default size, and width
unchanged at 211 because the two-column tools grid sets it. Height is guarded by
`_view_section_fits`; width is not guarded at all and comes straight off the
preview, so height is the cheaper axis to spend. Screenshotted.

**And a thing the swatch could not say.** Disabling it while erasing does
nothing visible — a `tk.Button` whose background *is* the colour looks identical
disabled, because the explicit `bg` beats the disabled style. Greying the
"Colour" label beside it is the half you can see. Same "two claims" shape as the
label bug above: "we disabled it" is not "it looks disabled".

**Numbers:** 607 headless (was 579), 281 smoke (was 269). Ten mutations
confirmed after the two misses were fixed.

**Next session:** slice 2 — the floating paste. Drag to place, commit or cancel.
The pieces it needs already exist: `paint.paste` takes an arbitrary `(x, y)`, the
canvas can draw a persistent overlay from state, and `Tool.is_gesturing` /
`on_cancel` already give a mode something to hang from. The new thing is that a
floating paste is a state the *document* is not yet in — pixels shown but not
committed — which is the first thing here that isn't either committed or a
gesture.
