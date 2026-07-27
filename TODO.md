# TODO

## Blocked on Matthew

- [x] `git init` — done
- [x] M0 committed (`95cf542`), line endings pinned to LF (`92fd44e`)
- [ ] Sweep `tmp_obj_*` cruft from `.git\objects` after a reboot frees the handles (harmless if left)
- [ ] Commit the crop feature: `git add -A; git commit -F COMMIT_MSG.txt` (M0–M4 already committed through `8b19fe4`)
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
- [x] Preview composites over a checkerboard + border so canvas bounds and transparency are visible
- [x] 42 new tests (clock, controller playback, cache); Tk smoke now 29 checks, all green on Xvfb

## M2 — editing (v1 complete)

**Slice 1 — editing core (done, committed as its own checkpoint):**

- [x] `core/ops/registry.py` — `@register_op`, `menu_groups()` by id prefix, `accel` per op
- [x] `core/ops/frames.py` — delete, duplicate, move, reverse, trim; each returns `OpResult` + post-op selection
- [x] `core/history.py` — snapshot `(doc, selection, index, label)`, limit 64, saved-marker for dirty, `amend_current` for pre-op selection
- [x] `run_op` / `undo` / `redo` / `can_undo` / `undo_label` / `can_run(op_id)` / `dirty` on the controller
- [x] Immutability test: source images byte-identical after every op (guards risk 3)
- [x] Event-ordering: one `doc_changed` per op/undo/redo, verified via fake frontend
- [x] 50 core tests + 14 controller-editing tests, boundary grep still clean

**Slice 2 — editing UI (done):**

- [x] `ui/tk/dialogs.py` — hardcoded duplicate-count dialog (no param schema yet)
- [x] Selection UI: click, shift-range, ctrl-toggle, select-all, deselect
- [x] Drag-to-reorder following the gesture rule: local insertion marker, one `move` op on release
- [x] Edit + Frames menus built from the registry, live enable/disable via postcommand, keyboard shortcuts
- [x] Timeline preserves scroll on edits, resets only on open/close
- [x] Tk smoke now 50 checks incl. the real drag gesture path, screenshot under Xvfb

**M2 is complete — v1 lite edits.**

## M3 — save ✅

- [x] `core/io/gif_write.py` — adaptive palette + Floyd–Steinberg dither, per-frame transparency, disposal=2, durations + loop preserved
- [x] `count_merges` so the UI can mention identical-frame folding on save
- [x] Controller `save` / `save_as` / `has_path`; `mark_saved` clears dirty; merge count reported
- [x] File menu Save (Ctrl+S) / Save As (Ctrl+Shift+S); Save falls back to Save As with no path; menu enable/disable
- [x] 18 new tests (writer round-trip incl. transparency + merge, controller saving + dirty), smoke +5 checks. 182 total, all green
- [x] Verified end-to-end on the real transparent GIFs: reverse + trim + save + reopen is pixel-perfect on visible content and transparency
- [x] Decision recorded: accept merge, defer project file (ARCHITECTURE §18)
- [ ] **`Param` schema deferred *again*** — "just save" needed no options dialog. It now lands with the *next* thing that has plural options (M4 timing/canvas ops, or WebP/APNG export at M5), whichever comes first

## M4 — canvas & timing ops ✅

- [x] **Fixed a real bug first:** `quantise_duration` jumped sub-20ms values to 100ms, so "speed up" could slow a frame down. Split: quantiser floors to 20 (monotonic), reader keeps the browser-clamp (tiny → ~100)
- [x] `core/params.py` — the long-deferred `Param` schema: Int/Float/Bool/Choice with `coerce`, bounds, `default_params` hook for doc-seeded dialogs
- [x] Timing ops: `timing.set_delay`, `timing.scale_speed` (pure, selection-or-all)
- [x] Canvas ops: `canvas.resize` (keep-aspect), `canvas.rotate` (dirs verified vs Pillow), `canvas.flip` — first pixel-allocating ops, fresh uids
- [x] Migrated `duplicate` to a `Param`; retired the hand-written duplicate dialog
- [x] `ui/tk/dialogs.py` — generic `ParamDialog` built from any op's params; `ask_params`
- [x] Menus built generically per op-group (Frames / Timing / Image); "..." decoration for param ops lives in the UI
- [x] Ping-pong playback (clock bounce mode + transport toggle)
- [x] 243 tests (was 182); Tk smoke 61 checks incl. dialog-seeding + resize + ping-pong, screenshot under Xvfb

## Crop — rubber-band gesture ✅

Post-M4 additive slice. Crop wanted a canvas gesture, not a typed-coords dialog (deferred at M4 on purpose). Sliced core-then-UI, same as M2.

**Slice 1 — core op (headless):**

- [x] `canvas.crop` in `core/ops/canvas.py` — pure, image-space box (x/y/w/h), clamped to the canvas, fresh uids per frame, new `doc.size`, selection passed through untouched
- [x] `in_menu = False` — gesture-driven like `frames.move`; a full-canvas or empty box returns the same doc so no no-op lands on the undo stack
- [x] `default_params` seeds the whole canvas (identity box) for any future typed-coords fallback
- [x] 8 op tests + immutability coverage (the `test_every_registered_op_is_covered_here` guard forced it) — 253 tests total

**Slice 2 — the gesture UI:**

- [x] `PreviewCanvas` records where the fitted image sits (`_image_geom`) and maps widget coords → image pixels
- [x] Rubber-band: press/drag draws a dashed marquee + a live pixel-size label; release commits one `canvas.crop`; Esc cancels; a window resize mid-crop cancels (geometry would be stale)
- [x] Image → Crop menu item + **C** shortcut; rides the canvas group's enable/disable refresh via `can_run("canvas.crop")`
- [x] Tk smoke 74 checks (was 61) incl. the mapped drag + undo + Esc-cancel; screenshot under Xvfb

## Painting — the tool layer (in progress)

Design in ARCHITECTURE §19. Tools live in the frontend and commit pure core ops; the brush is a coverage **mask**, so soft/AA brushes are a future drop-in (a new mask generator, op + tool unchanged). v1 set (Matthew's call): **Pencil, Eraser, Eyedropper** — hard-edged, current frame only, at fit scale (zoom later).

**Slice 1 — painting core (headless): ✅**

- [x] `core/ops/paint.py` — `paint.stroke` (paint) + `paint.erase`, pure, mask-based; target a frame `index`; fresh uid on the one edited frame; decline no-ops (empty / off-canvas / bad index); registered via `core/ops/__init__.py`
- [x] `_brush_mask` = the pluggable stamp (hard 0/255 now; soft feathered later changes only this)
- [x] Tests: paint paints, erase clears alpha, disc size, target-index, declines, fresh uid; + immutability CASES (the coverage guard forced it). 268 tests (was 253); boundary rule still clean

**Slice 2 — the tool system + tools (UI): ✅**

- [x] `ui/tk/tools.py` — minimal `Tool` base + Pencil / Eraser (a shared `StrokeTool`) / Eyedropper; toolkit-neutral, talks to a duck-typed `ToolContext`
- [x] Canvas: one mouse-dispatch to crop-mode-or-active-tool, `_image_to_display` inverse map, provisional stroke overlay (scaled polyline; neutral dashed for erase)
- [x] Tool palette: Cursor/Pencil/Eraser/Eyedropper radios + colour swatch (Tk `colorchooser`) + size spinbox; active-tool state; B / E / I keys
- [x] Eyedropper sets the FG colour (no op — the Tool≠Operation case)
- [x] Caught a real bug: paint must return `Selection.single(index)` or `run_op` moves the playhead off the frame just painted
- [x] Xvfb smoke 83 checks (was 74) incl. mapped pencil stroke + eyedropper + erase; screenshot

**Later (additive):**

- [ ] Fold crop into the tool system — one interaction mechanism, not two
- [ ] Fill bucket; line / rect / ellipse shape tools
- [ ] Soft / anti-aliased brushes (a new mask generator; op + tool unchanged)
- [ ] Zoom / pan (the fit calc in `canvas.py` is already factored for it) — precise pixel work
- [ ] **Memory-aware undo** — track the bytes `History` holds and warn + report when it climbs past ~128 MB, rather than silently trimming. The plain 64-snapshot cap is fine initially; this is the bespoke follow-up Matthew flagged (concrete form of risk 1's `FrameStore` note)

## Later

- [ ] **Image-sequence IO** — import a folder of PNGs as frames / export frames out. Promotes the IO dict to a real registry (folder-based source is a new shape)
- [ ] **Project / sidecar format** (`.gifproj`?) — lossless zip of PNG frames + JSON manifest, so authored frames/timing survive a round-trip GIF can't represent. One `read_x`/`write_x` pair; see ARCHITECTURE §18. Matthew wants this eventually
- [ ] M5 video import (`imageio-ffmpeg`, try/except registration), WebP/APNG export
- [ ] Second frontend to actually prove the seam (Qt or Dear PyGui)
- [ ] Polish: warn before overwriting the *original* source on Ctrl+S (GIF re-save is lossy); default Save As to `<name>_edited.gif`?
- [ ] Polish: `default_params` could seed Set-Delay from the current frame's delay
- [ ] Polish: crop is apply-on-release; a draggable/adjustable marquee with an explicit confirm (and maybe a keep-aspect modifier) would be nicer
