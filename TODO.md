# TODO

## Blocked on Matthew

- [x] `git init` — done
- [x] M0 committed (`95cf542`), line endings pinned to LF (`92fd44e`)
- [ ] Sweep `tmp_obj_*` cruft from `.git\objects` after a reboot frees the handles (harmless if left)
- [ ] Commit M1: `git add -A; git commit -F COMMIT_MSG.txt`
- [ ] Decide risk #2: identical-frame merging on export (accept / disable optimiser / project file) — **not urgent, first bites at M3**
- [ ] Run `python -m giflite` on Windows and confirm the window looks right
- [ ] Confirm Python version on the Windows box (targeting 3.10+)

## M0 — skeleton ✅

- [x] `giflite` package scaffold, `__main__.py` (no `--ui` switch until there's a second UI)
- [x] `core/model.py` — `Frame`, `Document`, `Selection`, `validate()`, image uid
- [x] `core/io/gif_read.py` — coalesce to RGBA, quantise to 10 ms with 20 ms floor
- [x] `app/controller.py` — doc + selection + index as session state, clamped centrally
- [x] `app/events.py` — pub/sub; single `doc_changed(doc, selection, index, reason)` per mutation
- [x] `ui/base.py` — Frontend ABC only
- [x] `ui/tk/app.py` — window showing frame 0, plus a real empty state for `doc is None`
- [x] Load-time memory estimate + warning above ~250 MB
- [x] 51 headless tests + 12-check Tk smoke test, both green

## M1 — viewing ✅

- [x] `core/playback.py` — `PlaybackClock`, forward + loop + speed, `set_durations()`, dt cap
- [x] Controller owns the clock; rebuilds durations and clamps index on every `doc_changed`
- [x] Continuous timer, `tick()` no-op when paused; new `PLAYBACK_STATE` event
- [x] `ui/tk/canvas.py` — preview surface, owns zoom/pan, scaled-frame cache for smooth playback
- [x] `ui/tk/timeline.py` — single Canvas, virtualised thumbnails, `PhotoImage` strong refs, click-to-seek, auto-scroll
- [x] `app/cache.py` — PIL-level thumbnails only, keyed by frame uid, plain dict, `retain()` pruning
- [x] Transport bar: play/pause, frame counter, speed dropdown; spacebar + arrow keys + Home/End
- [x] 42 new tests (clock, controller playback, cache); Tk smoke now 28 checks, all green on Xvfb

## M2 — editing (v1 complete)

- [ ] `core/ops/registry.py` — `@register_op`, menu grouping by id prefix, `accel` per op
- [ ] `core/ops/frames.py` — delete, duplicate, move, reverse, trim; each returns `OpResult`
- [ ] `core/history.py` — snapshot `(doc, selection, index, label)`, limit 64, saved-marker for dirty
- [ ] `can_undo` / `undo_label` / `can_run(op_id)` on the controller so menus don't re-derive state
- [ ] `ui/tk/dialogs.py` — hardcoded duplicate-count dialog (no param schema yet)
- [ ] Selection UI: click, shift-range, ctrl-toggle
- [ ] Drag-to-reorder following the gesture rule: local preview, one op on release
- [ ] `tests/fake_frontend.py` + event-ordering assertions
- [ ] Immutability test: source images byte-identical after every op
- [ ] Boundary grep (incl. `ImageTk`) wired into pre-commit or pytest (§11.4)

## Later

- [ ] M3 `gif_write` + Save/Save As; introduce `Param` schema **here**, where options are plural
- [ ] M4 image-sequence IO, promote format dict → registry, canvas ops, timing ops, ping-pong
- [ ] M5 video import (`imageio-ffmpeg`, try/except registration), WebP/APNG export
- [ ] Second frontend to actually prove the seam (Qt or Dear PyGui)
