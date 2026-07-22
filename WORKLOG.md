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

**Next**

- Risk 2 still open, but confirmed not blocking: it's an export concern that first bites at M3.
- M1 next: `PlaybackClock` (with `set_durations` from the outset), timeline strip, scrubbing.
- Note for M1: the `/tmp/tkroot` tkinter extraction that let me run the real window under Xvfb won't survive a reboot — it's four commands to redo (`apt-get download python3-tk tk8.6-blt2.5 blt libtk8.6`, `dpkg-deb -x` each into `/tmp/tkroot`, point `PYTHONPATH` at `usr/lib/python3.10` and `lib-dynload`, `LD_LIBRARY_PATH` at `usr/lib` and `usr/lib/x86_64-linux-gnu`), and Xvfb must start in the *same* bash call as the thing using it.
