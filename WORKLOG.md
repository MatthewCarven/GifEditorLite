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
- Reminder: the `/tmp/tkroot` tkinter extraction and Xvfb don't survive a reboot — re-extract with `apt-get download python3-tk tk8.6-blt2.5 blt libtk8.6`, `dpkg-deb -x` into `/tmp/tkroot`, point `PYTHONPATH`/`LD_LIBRARY_PATH` at it, and start Xvfb in the *same* bash call as the smoke test.
