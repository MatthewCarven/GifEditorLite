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

**Next**

- Await review of the doc, specifically risk 2 and the milestone ordering, before any code.
