# TODO

## Blocked on Matthew

- [x] `git init` — done
- [x] M0 committed (`95cf542`), line endings pinned to LF (`92fd44e`)
- [ ] Sweep `tmp_obj_*` cruft from `.git\objects` after a reboot frees the handles (harmless if left)
- [x] Crop and both painting slices are committed (HEAD `01feb8a`) — that old "commit the crop feature" item was stale
- [x] Batch files pinned to CRLF in the working tree (`2b1d115`)
- [x] Image-sequence IO committed and pushed (`9c7792f`)
- [x] Pixel grid committed (`328ccd7`) — a stale `.git/index.lock` had to be deleted first, as usual
- [x] Fill + shapes + the palette move committed and pushed (`c0f18a4`)
- [ ] Eyeball the new side panel on Windows — the preview no longer widens when
      you return to fit, which is deliberate but visible
- [ ] Check the overwrite-warning wording reads right to you (`_confirm_overwrite_source` in `ui/tk/app.py`) — it's the one dialog standing between you and a lost original
- [ ] Decide risk #2: identical-frame merging on export (accept / disable optimiser / project file) — **not urgent, first bites at M3**
- [x] Run `python -m giflite` on Windows and confirm the window looks right
      — done 2026-07-29, screenshotted on `Claude.gif` at 2039% with the
      pixel grid on. The `gray50` stipple renders as a dotted rule on
      Windows rather than the smoother wash Tk gives under X11; still
      reads as a guide, but that is the platform difference to know if
      `GRID_COLOR` ever gets retuned
- [ ] Confirm Python version on the Windows box. **This stopped being
      cosmetic on 2026-08-01**: the package did not import *at all* on 3.11,
      3.12 or 3.13 until that day — `Document.meta`'s mappingproxy default
      became illegal in 3.11 (ARCHITECTURE §31). Fixed, and guarded by a test
      that fails on 3.10 too, but worth knowing which one you are on

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

## Crop folded into the tool system ✅

Post-painting tidy-up, and the cheapest kind of win: two mechanisms doing one
job became one. Design in ARCHITECTURE §19.1.

- [x] `CropTool` in `ui/tk/tools.py`; `canvas.crop` op untouched (still `in_menu=False`)
- [x] Canvas down to one mouse path (`_dispatch`) and one coordinate mapping — deleted `begin_crop`, `_end_crop`, `_clamp_to_image`, the four `_crop_*` handlers and the mode flag
- [x] `Tool.is_gesturing` + `Tool.on_cancel(ctx)`: the hooks that make the resize-cancel guard and two-stage Esc generic rather than crop-specific
- [x] **Fixed a real bug in painting:** a resize mid-stroke would have committed against stale geometry (crop had the guard, strokes didn't); sharing the dispatch fixed it
- [x] Two-stage Esc: abandon the gesture, then put the tool away; still defers to the global deselect with no tool active
- [x] Overlays generalised — `show_stroke_overlay` + `show_rect_overlay` (box in image pixels, so a future rect-select/shape tool is free)
- [x] `Tool.hint` drives the status line, retiring the per-tool if-chain in the frontend
- [x] Crop joins the palette (Cursor / Crop / Pencil / Eraser / Eyedropper); sticky like the others, C still selects it
- [x] `tests/test_tools.py` — 25 headless tests against a fake `ToolContext`; Xvfb smoke 110 checks (was 83). 299 headless total

## Drawing offset — fixed ✅

Matthew spotted the pencil drawing up-left of the cursor on `Claude.gif`. Two
half-pixel errors compounding; invisible at 1:1, ~15 screen px at 30x zoom.
Design in ARCHITECTURE §19.1.1.

- [x] `_display_to_image` floors for pixel tools (a pixel spans `[i, i+1)`, so `round` sent pixel centres to the neighbour) and no longer clamps — the paint ops clip, and clamping smeared strokes along the edge
- [x] `_display_to_image(snap="edge")` keeps round+clamp for crop, whose coordinates are boundaries between pixels, not pixels — `Tool.coords` declares which a tool wants
- [x] `_image_to_display(center=True)` puts the stroke preview on the pixel instead of its top-left corner
- [x] Pinned the canvas `scrollregion` + dispatch through `canvasx`/`canvasy`: a scrollregion-less `tk.Canvas` scrolls over its items' bbox, after which widget != canvas coords and every gesture is offset. Not the live bug, but one stray binding away from being one
- [x] Closed the test hole that hid it: click points were derived via `_image_to_display` and asserted through `_display_to_image`, so both halves being wrong together passed. Now hand-computed coords, `_image_geom` checked against `canvas.bbox()`, pixel centres + 2%/50%/98% sweeps, and the overlay's drawn bbox compared to centre-vs-corner. All verified to fail on a reverted tree

## Save safety ✅

Ctrl+S on a freshly-opened file re-encoded the user's original in place. Design
in ARCHITECTURE §19.2.

- [x] Controller reports the fact: `overwrites_source` (path is still the file we read) and `suggested_save_name` (`<stem>_edited.gif`, idempotent — no `a_edited_edited.gif`)
- [x] Frontend owns the policy: overwrite / save-elsewhere / cancel, with the *safe* button as the default
- [x] Warns once per opened file — the flag clears on the first write to any path
- [x] Save As defaults to the non-destructive name
- [x] Routing covered in the smoke test with a stubbed answer; the dialog's real option set (`-detail`, `-default no`) proved to construct under Xvfb separately, since a modal would hang a scripted run
- [x] A non-dirty Save re-encoded for no gain — now skipped ("No changes to save"), done 2026-07-27
- [x] Single-key shortcuts fired while the Size spinbox had focus — now yield to text fields, done 2026-07-27

## Keyboard & save polish ✅

Both were listed as "possible follow-ups" under Save safety. The second turned
out to be worse than advertised. Design in ARCHITECTURE §19.2–19.3.

- [x] `save_would_change_nothing` on the controller; `save()` acts on it rather
      than only reporting it — protection, not policy, so a second frontend
      can't lose an original by forgetting. `save_as` still always writes
- [x] A skipped save leaves `overwrites_source` set: an original we didn't touch
      is still an original, so the warn-once flag must not be spent
- [x] `_bind_bare_key` — every unmodified shortcut stands down while a text
      field has focus; returns `None`, not `"break"`, so a dialog's own Escape
      still fires. Ctrl-combinations stay raw
- [x] **It wasn't just tool letters.** With the guard reverted, BackSpace and
      Delete typed into the brush Size box deleted *two frames* (8 → 6), space
      toggled playback, and Home/End moved the playhead. The reported symptom
      was the mildest one
- [x] Tests: 312 headless (was 302) incl. a monkeypatched writer spy, since
      comparing bytes alone passes against a broken build on the shared fixture
      — our writer reproduces `make_gif` art exactly. Xvfb smoke 132 checks
      (was 110); all new checks verified to fail on a reverted tree

**Later (additive):**

- [x] Fill bucket; line / rect / ellipse shape tools — done 2026-07-29, see below
- [ ] Soft / anti-aliased brushes (a new mask generator; op + tool unchanged)
- [x] Zoom / pan — done 2026-07-28, see below
- [ ] **Memory-aware undo** — track the bytes `History` holds and warn + report when it climbs past ~128 MB, rather than silently trimming. The plain 64-snapshot cap is fine initially; this is the bespoke follow-up Matthew flagged (concrete form of risk 1's `FrameStore` note)

## Later

- [x] **Image-sequence IO** — done 2026-07-29, see below
- [ ] **Project / sidecar format** (`.gifproj`?) — lossless zip of PNG frames + JSON manifest, so authored frames/timing survive a round-trip GIF can't represent. One `read_x`/`write_x` pair; see ARCHITECTURE §18. Matthew wants this eventually
- [ ] M5 video import (`imageio-ffmpeg`, try/except registration). **WebP/APNG
      export is no longer planned here** — Matthew's call 2026-07-29: if those
      land it's a fork, *APNG Editor Lite*, so the two tools can promise
      different things. See the eraser-opacity note below for why the split is
      about purpose rather than code
- [ ] Second frontend to actually prove the seam (Qt or Dear PyGui)
- [x] Polish: warn before overwriting the *original* source on Ctrl+S (GIF re-save is lossy); default Save As to `<name>_edited.gif` — done 2026-07-27
- [x] Polish: `default_params` seeds Set-Delay from the frames it would change — done 2026-07-29 (mixed selections seed from the shortest)
- [ ] Polish: crop is apply-on-release; a draggable/adjustable marquee with an explicit confirm (and maybe a keep-aspect modifier) would be nicer

## Zoom & pan ✅ (slice 1)

Buttons-and-keyboard only, by choice — see ARCHITECTURE §20.5. Design in §20.

- [x] `ui/tk/view.py` — `ViewTransform`, no toolkit import, so the arithmetic is
      headless (`tests/test_view.py`, 41 checks). Scale is `None` for fit rather
      than a baked float; pan is the image point held at the viewport centre
- [x] `geometry()` returns exactly the `_image_geom` tuple the canvas already
      published, so **`tools.py` needed no changes** — crop and the brushes work
      at 32x without knowing zoom exists
- [x] Rendering is crop-then-scale: the composed bitmap is viewport-bounded, not
      image-bounded (32x on this GIF would otherwise be 5120px wide — asserted
      in the smoke against the real bitmap)
- [x] Sub-pixel remainder carried by *placement*, not by the resample; checker
      board carries a phase offset so it stays locked to the image across a pan
- [x] Every view change funnels through `_apply_view`, which cancels a pending
      gesture — the same staleness `<Configure>` has guarded since crop, and
      reachable because Ctrl+- fires mid-stroke
- [x] View menu + Ctrl+= / Ctrl+- / Ctrl+0 / Ctrl+1; zoom in the status line
- [x] An edit keeps your magnification; only open/close resets to fit
- [x] 353 headless (was 312), Xvfb smoke 151 checks (was 132). Four mutations of
      the production code confirmed to break the new checks

## View panel & navigator ✅ (slice 2)

Started as the planned toolbar cluster; it didn't fit, and Matthew's counter-
proposal of a minimap turned out to be the better control anyway. Design in
ARCHITECTURE §21.

- [x] **The toolbar cluster failed silently** — that row needs 1087px and gets
      900, so `pack` dropped three widgets with no error. Only a screenshot
      caught it. Anything added to that toolbar needs the same check
- [x] `ui/tk/minimap.py` — fitted thumbnail, dimmed outside the visible region,
      viewport rectangle; press or drag to centre the preview there. Absolute,
      not relative: the position you press is the position you get
- [x] It keeps the preview's mouse entirely the tools' — a drag in the map is
      not a gesture on the canvas, so no view input can land inside a stroke
- [x] `image_to_display` / `display_to_image` **moved onto `ViewTransform`**;
      the canvas delegates and the map reuses them. Also fixed a weak test:
      `test_view.py` had been testing its own *copies* of those functions
- [x] `center_on(ix, iy)` (clamped) and a configurable `fit_pad`
- [x] **The `_axis_origin` clamp stopped being hypothetical.** §20 kept it for
      "the next pan input that sets a centre from raw deltas"; the navigator is
      that input. Dropping `center_on`'s clamp now leaves rendering correct
      while the stored centre is wrong — the invisible-trap case. Both are
      checked, at both levels
- [x] Panel shown only when zoomed in, packed `before=canvas`, re-entrancy
      guarded; the status line refreshes with it (pointing `on_view_change` at
      the controls-only refresh left a stale percentage — a real bug)
- [x] 360 headless (was 353), Xvfb smoke 170 checks (was 132 before zoom). Four
      mutations confirmed to break the new checks

**Later (additive):**

- [ ] `can_pan_x` / `can_pan_y` have no production caller now the pan buttons
      are gone — kept as the question any other pan input must ask. Delete if
      nothing claims them
- [ ] Possible follow-up: `_boards` and `_composed` are keyed more finely now
      (rect + out-size + phase). Bounded, but worth a look at real cache hit
      rates during playback while zoomed
- [ ] Possible follow-up: zoom-to-cursor, keyboard panning (`nudge` and the
      direction helpers are already there), or a resizable panel

## Pixel grid ✅

Asked for as "gridlines beyond 4x". Three states rather than a checkbox, at
Matthew's choice. Design in ARCHITECTURE §22.

- [x] `ViewTransform`: `grid_mode` (off / auto / always), `set_grid_mode`,
      `cycle_grid_mode`, `grid_visible`, `grid_suppressed`, `grid_lines()`
- [x] Rules generated by calling `image_to_display` — the same mapping the tools
      and the crop marquee read through, not a second copy of the arithmetic
      (the §19.1 trap). Spans from `visible_source_rect`, so they stop at the
      artwork and the count is viewport-bounded, not image-bounded
- [x] `Always` keeps a floor at 2x: below it the rules touch and the "grid" is a
      flat fill costing one canvas item per source pixel, per redraw, at 60fps
- [x] Canvas items, not baked into the composed bitmap — the frame cache stays
      pure and there is no second derivation of a rule's position
- [x] Stippled `gray50`, so one colour reads over both dark and light artwork
- [x] Goes through `_apply_view`: `_draw` deletes the overlay whether or not the
      image moved, so a gesture surviving a grid toggle is a gesture whose
      preview has silently vanished
- [x] View > Pixel Grid submenu (radiobuttons, so the menu reports the mode for
      free) + Ctrl+' to cycle; status line names a mode that is on but invisible
- [x] **Two of my own tests were wrong before the code was** — the pan test read
      both snapshots through the final geometry and compared a value with
      itself; the smoke assumed fit meant "zoomed out" (this GIF fits at 552%)
- [x] 376 headless (was 360), Xvfb smoke 187 checks (was 170). Five mutations
      confirmed to break the new checks

**Later (additive):**

- [ ] A major/minor grid — a stronger rule every 8 or 16 pixels for sprite-sheet
      work. `grid_lines` already knows the step; it would be one more field and
      one more colour
- [ ] Decide whether `Auto` opening a small GIF with the grid already on is
      right. It is literally the rule asked for (552% *is* beyond 4x), but
      `GRID_AUTO_SCALE` is the one constant if it grates

## Fill, shapes, and the palette moving house ✅

Matthew's three calls: a Fill checkbox rather than separate filled/outline
tools, a tolerance box on the bucket, palette into the view panel. Design in
ARCHITECTURE §23.

- [x] `_apply_mask` — one commit path for every painting op; `_apply_stroke` is
      now a thin caller. Alpha compositing, immutability, fresh uids, the
      decline convention and the playhead rule are stated once, inherited thrice
- [x] `paint.fill` — `_match_mask` (Chebyshev tolerance, whole-image C ops) then
      `ImageDraw.floodfill` for connectivity. Flood with 128 so the pixels left
      at 255 are the matching-but-unreachable ones
- [x] `paint.shape` — one op with a `kind` (line/rect/ellipse), outline at brush
      width or filled. Unknown kind declines rather than defaulting
- [x] `FillTool` (commits on press — nothing to preview), `ShapeTool` +
      Line/Rect/Ellipse. `preview_box` undoes the pixel-inclusive/edge mismatch
- [x] Toolbar retired; side panel carries tools + settings always, view section
      conditionally. Two-column tool grid; preview gains ~34px of height
- [x] `_view_section_fits` — at 480x400 the panel wanted 412px of 238 and pack
      silently dropped the zoom row. Whole section or none of it
- [x] Keys F/L/R/O added to C/B/E/I, all yielding to focused text fields
- [x] **Two of my tests were wrong again**: the `coords` test hardcoded its tool
      list (now derived from the palette), and the new smoke rect check drew in
      the colour it had just flooded the frame with, so it asserted nothing
- [x] 426 headless (was 376), Xvfb smoke 211 checks (was 187). Six mutations
      confirmed to break the new checks

**Later (additive):**

- [ ] Shift-constrain: square/circle from a shape drag, 45° lines. Needs the
      canvas to pass modifier state through `_dispatch`, which is an API change
      to `Tool.on_press` — worth doing once, for all tools
- [ ] Global fill (replace every matching pixel, not just the contiguous run) —
      `_fill_mask` without the flood stage; a modifier or a fourth setting
- [ ] Fill is ~1µs/pixel (PIL's Python flood walk): 1.1s for a whole-canvas fill
      at 1000x1000, instant at GIF sizes. Only worth attacking if it bites
- [ ] The panel measures ~211px against `PANEL_WIDTH` 200 — the Colour/Fill row
      is what exceeds it. Cosmetic

## Per-frame timing, made reachable ✅

Matthew asked for a way to set individual frame time rather than duplicating
frames to hold a pose. No new op — `timing.set_delay` is M4; this is the fast
path to it. Design in ARCHITECTURE §24.

- [x] `delay_targets` / `target_delay_ms` / `current_delay_ms` /
      `set_frame_delay` on the controller — scope policy and state derivation,
      so a second frontend gets the same answers
- [x] **Scope: the selection, but only one the playhead is standing in.** Never
      "all frames" (that's the menu op's rule, right behind a dialog and wrong
      for an inline box), and never a selection you have arrowed away from —
      opening selects frame 0 and `seek` leaves it there, so the box would have
      edited frame 0 while the preview showed frame 3
- [x] Delay box in the side panel, ms, committing on Enter / focus-out / arrows.
      Shows what *landed* after quantisation, not what was typed. Blank when the
      targets disagree; the label carries the count
- [x] Status line reports the frame's own delay — it only ever showed the
      *total*, which coincides on a one-frame GIF and is a different number on
      any other
- [x] Timeline: each frame's delay under its thumbnail, inside `_draw_slot` so
      it inherits the virtualisation
- [x] **Fixed a real bug the box exposed:** both timing ops returned a fresh
      Document unconditionally, so setting the delay already there pushed an
      identity edit onto undo. Every other op family declines; these now do too,
      comparing results rather than requests (103ms quantises to 100ms)
- [x] **Two tests had no teeth** and mutation caught it — see ARCHITECTURE §24.4
- [x] 452 headless (was 426), Xvfb smoke 224 checks (was 211). Four mutations
      confirmed, plus the two rewritten checks re-verified

**Later (additive):**

- [ ] A delay edit moves the playhead to `selection.first` (run_op's rule for
      every op). Retiming frames 3-6 while looking at 5 jumps you to 3. Harmless
      but slightly rude; would need run_op to distinguish reordering ops from
      in-place ones
- [ ] Nudging delay from the keyboard (up/down on the selected frame) without
      focusing the box

## Image-sequence IO ✅

The bespoke one. Import a folder of stills; export frames back out. Design in
ARCHITECTURE §25.

- [x] `core/io/sequence.py` — natural-sorted import, union canvas with top-left
      padding (never scaling), zero-padded export
- [x] `core/io/manifest.py` — versioned schema, **shared with the deferred
      `.gifproj`** (§18): an exported folder is the project format minus the zip
- [x] IO dict promoted to a `Format` registry: `matches()` dispatch, `is_folder`
      as a real field, `available()` tested with an unavailable format,
      `read_params` so a frontend can generate an options dialog for a format it
      knows nothing about
- [x] `import_frames` / `export_frames` on the controller. **Import is not open**
      (no path, so Ctrl+S goes to Save As rather than writing a GIF over the
      PNGs); **export is not save** (path, dirty and history untouched)
- [x] `_source_label` so the title shows the imported folder's name
- [x] `ask_values` split out of `ask_params` — ops are no longer the only things
      with parameters
- [x] **Fixed a bug I introduced:** two new File menu entries broke
      `_refresh_file_menu`'s hardcoded indices; now by label. The comment
      explaining it was also wrong, and the mutation run said so — see §25.5
- [x] 497 headless (was 452), Xvfb smoke 244 checks (was 224). Six mutations
      confirmed

**Later (additive):**

- [ ] `.gifproj` is now mostly a zip of what `write_sequence` already produces —
      one reader/writer pair over the existing manifest (§18, §25.4)
- [ ] Import currently ignores subfolders by design. A "recurse" option would be
      easy if a real folder layout ever wants it
- [ ] Export always writes PNG. A format choice (or reusing the writable formats
      list) would be the natural extension

## Fill: "empty" was not one colour ✅

Found while checking whether "fill empty" already worked. It did, until you
erased anything.

- [x] Transparent pixels carry the RGB that was under them, and `paint.erase`
      leaves RGB alone — so originally-transparent and just-erased pixels look
      identical and compare as different colours, and the fill stopped at the
      join with nothing on screen to explain why
- [x] `_clear_mask`: seeded on a fully transparent pixel, match alpha only.
      An opaque seed is unchanged — a branch, not a replacement
- [x] Tolerance could technically have crossed it (147 in the real case) but the
      value depends on invisible data, so it was unguessable rather than a
      setting anyone could have got right
- [x] 502 headless (was 497); two mutations confirmed

## Select / copy / paste

Matthew's calls: floating draggable paste, paste into every selected frame, no
clipping of the paint tools. Then, asked before slice 1: cut clears only the
playhead frame, and paste reuses the delay box's scope rule.

### Slice 1 — done 2026-07-29 (ARCHITECTURE §26)

- [x] `Region` in `core/model.py` beside `Selection` — edge coordinates, so it
      is the argument list `canvas.crop` already takes; `from_corners`,
      `clamped`, `box`
- [x] Region + clipboard as controller session state: not undoable, not saved,
      re-clamped in `_emit_doc_changed` when the *canvas* changes shape, and the
      clipboard deliberately survives `open`
- [x] `REGION_CHANGED`, emitted after `DOC_CHANGED` so no listener draws a
      marquee against a canvas that has gone
- [x] `paint.cut` and `paint.paste` — two more mask generators; `_apply_mask`
      became the single-frame caller of a multi-frame `_apply_mask_frames`
- [x] `OpResult.index` — an op may say where the playhead belongs, because the
      `Selection.single(index)` workaround cannot survive a multi-frame op
- [x] `SelectTool`, the first tool whose result outlives the gesture; the canvas
      gains its first overlay redrawn from state rather than cleared with the
      gesture
- [x] Edit menu Cut/Copy/Paste + Ctrl+X/C/V, guarded against text fields;
      `_bind_bare_key` → `_bind_guarded_key`
- [x] Esc ladder: gesture → tool → region → frames
- [x] Fixed a premultiplication bug in `_composite` that would have broken §19's
      "a soft brush is just a feathered mask" promise for whoever implemented it
- [x] 579 headless (was 502), 269 smoke (was 232), 8 mutations confirmed

### Slice 2 — the floating edit — done 2026-07-30 (ARCHITECTURE §28)

Asked for as "can I move a selection?", which is the same feature from the other
side. One floating layer, two producers: a move lifts pixels off the frame, a
paste brings them from the clipboard.

- [x] `FloatingEdit` in the controller — the third state, neither committed nor
      a gesture. Survives a resize, alone among in-flight state, because its
      offset is in image pixels
- [x] `paint.move` as **one** op, so one Ctrl+Z undoes a move rather than
      handing back the hole while you still hold the sprite. Each frame shifts
      its *own* pixels, unlike paste
- [x] The commit loop became `_apply_frames`, taking any `image -> image` — a
      move is an erase *and* a composite, which no single mask can express
- [x] `float_preview` is the op run and thrown away: two lines, because ops are
      pure, and the preview is the *same call* as the commit rather than a
      second implementation of it
- [x] Move tool (**M**), Enter commits, Esc cancels, arrows nudge; anything else
      you do commits the float rather than discarding it
- [x] Ctrl+V floats and selects Move; Ctrl+V then Enter is the old paste-in-place
- [x] 659 headless (was 607), 307 smoke (was 281), 14 mutations confirmed

Follow-ups, none load-bearing:

- [ ] A float is bounded to the frame you started it on — scrubbing commits it.
      Carrying one across frames would mean placing pixels you cannot see, but
      "float it here, then step and drop it there" is a plausible want
- [ ] No handles on the marquee, so no scale or rotate of a selection. That is a
      transform mode rather than a placement one and would want its own slice
- [x] `test_controller_region.py`'s `painted()` — fixed 2026-08-01, and worse
      than recorded: the substituted document is 20x20 and the opened GIF
      40x20, so undo was handing back a *differently sized* document and every
      "undo put it back" assertion in the file was sampling the wrong one.
      Pinned by a test asserting on the size, which is the one property no
      flat-colour pixel comparison can satisfy by accident

### Known, deliberately deferred

- [ ] **`Delete` with a region selected still deletes frames**, not the region's
      pixels. Arguably a footgun — but changing what an existing destructive
      shortcut does is not a change to make while adding a feature. Cut is the
      supported way to clear a region
- [x] **Crop to Selection** — done 2026-08-01 (ARCHITECTURE §30).
      `controller.crop_to_region()`, reachable at Image -> Crop to Selection.
      The marquee is dropped afterwards, but only if the crop actually
      happened: a full-canvas region is the identity, the op declines, and
      taking the selection away as a consolation prize would change the one
      thing you didn't ask it to
- [ ] It has no keyboard shortcut. C is the crop tool and S is Select, so a
      binding is a decision to take on purpose rather than smuggle in with the
      feature — available if you want one
- [x] **System clipboard** — done 2026-08-01 (ARCHITECTURE §32). The format
      decision was *both*: a registered "PNG" format for the readers that
      understand alpha, and CF_DIB for everything else. Copy Area mirrors out
      too, so the last thing you copied is what another program gets

## Erase mode — done 2026-07-29 (ARCHITECTURE §27)

From Matthew's question, which had a wrong premise worth recording: *what colour
do I fill with to get transparent?* None, and none could — painting
alpha-composites, so a transparent colour contributes nothing and the op
declines. Removing alpha is the other branch of the same operation.

- [x] An **Erase checkbox** beside Fill, applying to every painting tool: pencil,
      bucket, line, rect, ellipse. His pick from three options (the others were a
      separate erase-bucket tool and a modifier-click)
- [x] Strokes swap the op (`paint.erase` has existed since M4); fill and shapes
      take a `mode` param. Mask generators untouched
- [x] `StrokeTool._erasing` is `self.erase or ctx.erase_mode` — the flag alone
      would turn the Eraser into a pencil whenever the box was off
- [x] Optional `label_for(**params)` hook + `op_label`, so the undo entry says
      "Erase Fill" rather than "Fill". Same shape as the `default_params` hook
- [x] Colour swatch *and* the "Colour" label grey out while erasing — the swatch
      alone shows nothing, since its explicit `bg` survives being disabled
- [x] 607 headless (was 579), 281 smoke (was 269), 10 mutations confirmed

Small things noticed and deliberately left alone:

- [ ] **"Fill" now means two things in the panel**: the bucket tool, and the
      checkbox that makes shapes solid. They sit two sections apart and the
      ambiguity predates this, but erase mode put them closer together.
      "Solid" would fix it in one word — not done, because renaming a control
      nobody asked to have renamed is its own small surprise
- [ ] Erase mode has no keyboard shortcut. Every bare letter that suggests
      itself (`x`, `t`, `d`) is unclaimed, so this is available if it turns out
      to be worth one

## The panel decides which sections fit — done 2026-08-01 ✅

Found while screenshotting slice 1; **pre-existing, and not caused by it** —
measured at both nine and ten palette tools and identical, since nine and ten
both fill five rows of two.

At 480x400 the side panel has 225px and its children want 406. `_view_section_fits`
stands the view section down deliberately (§23.5), but nothing does the same for
the Frame delay section, so `pack` simply drops it: no error, a control silently
gone. Exactly §21/§23.5 again, on a section that was added after both.

- [x] **Done 2026-08-01** (ARCHITECTURE §29). `PANEL_SECTIONS` in priority
      order, one frame per section, `_relayout_panel` the single writer of what
      is packed. A new section joins the rule by being named in the tuple;
      there is no longer a way to add one *without* it
- [x] The smoke test names every section now — and the measuring found a
      *fourth* instance before the fix landed: at the 900x680 default the four
      sections wanted 516px of a 505px panel, `_view_section_fits`
      under-counted padding by ~45px, and the Fit/1:1 row had been clipped to
      9px of its 28 for the whole life of the guard meant to protect it
- [x] Default window 900x680 -> 900x720 (Matthew's call, asked with the
      measurements in hand): ~29px of slack, and Windows font metrics are not
      X11's, so an exact fit here is a coin toss there
- [ ] At the 480x400 minimum only the tool palette fits, so the colour swatch,
      brush size and delay box all stand down. That is the rule applied
      honestly rather than a bug — but the answer *if* it ever matters is a
      scrolling panel, not a fourth special case
- [ ] The padding constants are only tested by the height sweep (400-780 in the
      smoke). Three mutations of them survived every fixed-size check: a
      constant 24px light only changes the outcome inside a 24px band

## Copy Frame / Paste Frame — done 2026-08-01 (ARCHITECTURE §32) ✅

Asked for as "copy frame to clipboard ... and Paste frame from Clipboard (if
its the same size else complain)". His calls, all asked before any code: the
**Windows clipboard both ways**, paste **replaces** the current frame, **one
clipboard** shared with Copy Area, and a mismatch **refuses and names both
sizes**.

- [x] `giflite/app/sysclip.py` — in `app/`, not `ui/tk/`: it is platform I/O,
      not toolkit I/O, so a second frontend inherits it. Pure DIB encoder +
      inverse + decision rules; one small impure `put_image`
- [x] `paint.replace_frame` — replaces rather than composites, keeps the
      frame's own duration, declines a size mismatch rather than resizing
- [x] Edit menu: Copy Frame (Ctrl+Shift+C), Paste Frame (Ctrl+Shift+V), both
      guarded against firing inside a text field
- [x] 17 mutations confirmed, including all three silent-failure modes of the
      DIB format (row order, channel order, stray file header)

**Only Matthew can verify the Windows half.** Specifically: does a copied frame
arrive in Paint/Discord the right way up and the right colour, does
transparency survive into a PNG-aware target, and does Paste Frame take a
screenshot in.

- [ ] Paste Frame is live whenever a document is open, because knowing whether
      the OS clipboard holds an image means opening and decoding it — every
      time the Edit menu opens. The command reports "Nothing on the clipboard"
      when pressed instead. Revisit only if that reads wrong in use
- [ ] Copying *out* is Windows-only. macOS (`pbcopy`/NSPasteboard) and Linux
      (`xclip`) are each a small shim behind the same `can_copy()` seam;
      reading already works on all three via Pillow
- [ ] A file on the clipboard (copied in Explorer) is reported, not opened.
      Importing it as a frame — or as a whole animation — is a plausible
      want and a separate decision

## Python 3.11+ could not import the package — fixed 2026-08-01 ✅

Found the hard way: the sandbox moved to 3.11 and `import giflite` raised before
a single test ran. `Document.meta` defaulted to `MappingProxyType({})`, chosen
*because* it is read-only and therefore safe to share between frozen instances.
3.11 replaced dataclasses' "is it a list, dict or set" check with "is its type
unhashable", and a mappingproxy is unhashable. Illegal on 3.11, 3.12 and 3.13;
`pyproject.toml` says `>=3.10`. See ARCHITECTURE §31.

- [x] `field(default_factory=lambda: EMPTY_MAP)` — same singleton, new spelling
- [x] `test_boundaries.py` restates 3.11's rule over every dataclass in the
      package, so it fails on 3.10 too. Importing the package would catch this
      on a new interpreter and stay silent on an old one, which is backwards:
      the person who needs telling is the one who can't hit it
- [x] Verified by reintroducing the bad default under 3.10 and watching the new
      check fail there; suite green on 3.10, 3.11 and 3.12
- [ ] Nothing else in the package was 3.11-sensitive, but nothing checks that
      either — running the suite on more than one interpreter is currently a
      thing someone remembers to do, not a thing CI does (there is no CI)

## Eraser opacity — investigated, declined ❌

Matthew asked for a 0-255 eraser opacity to feather sprite edges so they don't
look wrong against different backgrounds. Then asked the right question before
I built it: does a GIF palette carry more than 1 bit of alpha?

**It does not, and there are no deviations.** Verified against the repo's own
files: the palette is RGB triples (768 bytes for 256 entries, 3 per entry, no
fourth byte). Transparency is a Graphic Control Extension flag plus a single
byte naming *one* fully-invisible palette index; `Claude_Glasses.gif` reports it
as the int `255`. The index may differ per frame (`claude_blinky.gif` has frames
with one and frames with none) but it is always exactly one, always binary.

Measured consequence: an authored alpha ramp of `255,223,191,159,127,95,63,31`
comes back from a GIF save/reload as `255,255,255,255,0,0,0,0` — `gif_write`'s
`_ALPHA_CUTOFF` at 128 is not a choice we could make differently. The same ramp
survives a PNG-sequence round trip untouched.

So the feature would work in the editor, survive PNG export, and silently
evaporate on every GIF save. Declined — Matthew's call, and the right one.

**Revisit only if one of these becomes true:**

- [ ] WebP/APNG export lands. Both carry real 8-bit alpha, at which point partial
      erase means something on the way out — **but that is now a fork**, not a
      feature here. The registry would happily hold an APNG writer beside the GIF
      one; the reason to split is that an editor promising soft edges and one
      promising GIF fidelity want different defaults, different warnings and a
      different answer to "why did my feathering vanish". Purpose, not capability
- [ ] A PNG-sequence or `.gifproj` workflow becomes the primary output rather
      than GIF — partial alpha is already real in `Document` and already
      round-trips through `write_sequence`
- [ ] The actual goal (edges that don't look wrong on a different backdrop) is
      wanted badly enough to solve the GIF-native way: **matting** — blend edge
      pixels toward the background colour they'll sit on, staying fully opaque.
      Needs no partial alpha and nothing is lost on save. Not requested; noted
      so the underlying want isn't forgotten along with the rejected mechanism
