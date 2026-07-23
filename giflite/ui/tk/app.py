"""Tkinter frontend: window, menu, timeline, transport, wiring.

Subscribes to the events in ARCHITECTURE.md 9 and calls controller methods.
Owns no application state -- the playhead, selection, document and play/pause
state all live behind the controller, which is what stops a second frontend
from having to re-derive them. The one thing the frontend genuinely owns is
the timer: it ticks a clock the controller reads.
"""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from giflite.app import events as ev
from giflite.app.cache import ThumbnailCache
from giflite.app.controller import AppController
from giflite.core.io import open_filter
from giflite.core.model import Selection
from giflite.core.ops import menu_groups
from giflite.ui.base import Frontend
from giflite.ui.tk.canvas import PreviewCanvas
from giflite.ui.tk.dialogs import ask_duplicate_count
from giflite.ui.tk.timeline import Timeline

APP_NAME = "GIF Editor Lite"
EMPTY_TEXT = "No animation open\n\nCtrl+O to open a GIF"

# ~60fps timer. It runs continuously; controller.tick() is a no-op while
# paused, so there is no timer to start or stop and no start/stop race.
TIMER_MS = 16

SPEEDS = (("0.25x", 0.25), ("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("4x", 4.0))


def _format_bytes(nbytes: int) -> str:
    """Adaptive units, because '~0 MB' for a small GIF just looks broken."""
    if nbytes < 1024:
        return f"{nbytes} B"
    if nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.0f} KB"
    return f"{nbytes / (1024 * 1024):.1f} MB"


class MainWindow:
    def __init__(self, root: tk.Tk, controller: AppController) -> None:
        self.root = root
        self.controller = controller
        self.thumbnails = ThumbnailCache()

        root.title(APP_NAME)
        root.geometry("900x680")
        root.minsize(480, 400)

        self._build_menu()
        self._build_body()
        self._subscribe()

        self._render()
        self._set_title(controller.path, controller.dirty)
        self._update_transport()

        self._last_tick = time.perf_counter()
        self.root.after(TIMER_MS, self._on_timer)

    # ---- construction ----------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Open...", accelerator="Ctrl+O", command=self.open_file)
        file_menu.add_command(label="Close", accelerator="Ctrl+W", command=self.controller.close)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        # Edit and Frames refresh their own enable/disable state each time they
        # open (postcommand), so the frontend never tracks it per event -- it
        # just asks the controller at the moment the menu appears.
        self.edit_menu = tk.Menu(menubar, tearoff=False, postcommand=self._refresh_edit_menu)
        self.edit_menu.add_command(label="Undo", accelerator="Ctrl+Z", command=self.controller.undo)
        self.edit_menu.add_command(label="Redo", accelerator="Ctrl+Shift+Z", command=self.controller.redo)
        self.edit_menu.add_separator()
        self.edit_menu.add_command(label="Select All", accelerator="Ctrl+A", command=self._select_all)
        self.edit_menu.add_command(label="Deselect", accelerator="Esc", command=self._clear_selection)
        menubar.add_cascade(label="Edit", menu=self.edit_menu)

        self.frames_menu = tk.Menu(menubar, tearoff=False, postcommand=self._refresh_frames_menu)
        self._frame_menu_entries: list[tuple[int, str]] = []  # (entry index, op id)
        for op in menu_groups().get("frames", []):
            self.frames_menu.add_command(
                label=op.label,
                accelerator=op.accel,
                command=lambda oid=op.id: self.controller.run_op(oid),
            )
            self._frame_menu_entries.append((self.frames_menu.index("end"), op.id))
        self.frames_menu.add_separator()
        self.frames_menu.add_command(label="Duplicate N times...", command=self._duplicate_n)
        self._duplicate_n_entry = self.frames_menu.index("end")
        menubar.add_cascade(label="Frames", menu=self.frames_menu)

        self.root.config(menu=menubar)

        # bind_all so shortcuts work regardless of which widget has focus
        self.root.bind_all("<Control-o>", lambda _e: self.open_file())
        self.root.bind_all("<Control-w>", lambda _e: self.controller.close())
        self.root.bind_all("<space>", self._on_space)
        self.root.bind_all("<Left>", lambda _e: self.controller.step(-1))
        self.root.bind_all("<Right>", lambda _e: self.controller.step(1))
        self.root.bind_all("<Home>", lambda _e: self.controller.seek(0))
        self.root.bind_all("<End>", lambda _e: self.controller.seek(self.controller.frame_count - 1))
        # editing shortcuts -- the controller no-ops these when they can't apply
        self.root.bind_all("<Control-z>", lambda _e: self.controller.undo())
        self.root.bind_all("<Control-Shift-Z>", lambda _e: self.controller.redo())
        self.root.bind_all("<Control-y>", lambda _e: self.controller.redo())
        self.root.bind_all("<Control-a>", lambda _e: self._select_all())
        self.root.bind_all("<Control-d>", lambda _e: self.controller.run_op("frames.duplicate"))
        self.root.bind_all("<Delete>", lambda _e: self.controller.run_op("frames.delete"))
        self.root.bind_all("<BackSpace>", lambda _e: self.controller.run_op("frames.delete"))
        self.root.bind_all("<Escape>", lambda _e: self._clear_selection())

    def _build_body(self) -> None:
        # Packed bottom-up so the preview canvas takes all the slack.
        self.status = ttk.Label(self.root, text="Ready", anchor="w", padding=(8, 3))
        self.status.pack(side="bottom", fill="x")

        self.timeline = Timeline(
            self.root,
            self.thumbnails,
            on_pick=self._pick,
            on_extend=self._extend,
            on_toggle=self._toggle,
            on_reorder=self._reorder,
        )
        self.timeline.pack(side="bottom", fill="x")

        self._build_transport()

        self.canvas = PreviewCanvas(self.root)
        self.canvas.pack(side="top", fill="both", expand=True)

    def _build_transport(self) -> None:
        bar = ttk.Frame(self.root, padding=(8, 4))
        bar.pack(side="bottom", fill="x")

        self.play_button = ttk.Button(bar, text="Play", width=8, command=self.controller.toggle_play)
        self.play_button.pack(side="left")

        self.counter = ttk.Label(bar, text="", width=16, anchor="w")
        self.counter.pack(side="left", padx=(10, 0))

        ttk.Label(bar, text="Speed").pack(side="right", padx=(0, 4))
        self.speed = ttk.Combobox(
            bar, width=6, state="readonly", values=[label for label, _ in SPEEDS]
        )
        self.speed.set("1x")
        self.speed.bind("<<ComboboxSelected>>", self._on_speed)
        self.speed.pack(side="right")

    def _subscribe(self) -> None:
        bus = self.controller.events
        bus.on(ev.DOC_CHANGED, self._on_doc_changed)
        bus.on(ev.SELECTION_CHANGED, self._on_selection_changed)
        bus.on(ev.PLAYHEAD_MOVED, self._on_playhead_moved)
        bus.on(ev.PLAYBACK_STATE, self._on_playback_state)
        bus.on(ev.TITLE_CHANGED, self._on_title_changed)
        bus.on(ev.STATUS, self._on_status)
        bus.on(ev.ERROR, self._on_error)

    # ---- commands --------------------------------------------------------

    def open_file(self) -> None:
        # File pickers are frontend policy: the controller takes a path that
        # has already been chosen and knows nothing about dialogs.
        path = filedialog.askopenfilename(title="Open animation", filetypes=open_filter())
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> None:
        self._with_busy_cursor(lambda: self.controller.open(path))

    # ---- selection gestures (called by the timeline) ---------------------

    def _pick(self, index: int) -> None:
        """Plain click: select just this frame and move the playhead to it."""
        self.controller.seek(index)
        self.controller.set_selection(Selection.single(index))

    def _extend(self, index: int) -> None:
        """Shift-click: extend the range from the existing anchor."""
        sel = self.controller.selection
        anchor = sel.anchor if sel.anchor is not None else self.controller.index
        self.controller.set_selection(Selection.span(anchor, index, anchor))
        self.controller.seek(index)

    def _toggle(self, index: int) -> None:
        """Ctrl-click: add or remove this frame from the selection."""
        self.controller.set_selection(self.controller.selection.toggled(index))
        self.controller.seek(index)

    def _reorder(self, to: int) -> None:
        """Drag release: move the current selection to the drop point."""
        self.controller.run_op("frames.move", to=to)

    def _select_all(self) -> None:
        if self.controller.frame_count:
            self.controller.set_selection(
                Selection(frozenset(range(self.controller.frame_count)))
            )

    def _clear_selection(self) -> None:
        self.controller.set_selection(Selection.empty())

    def _duplicate_n(self) -> None:
        count = ask_duplicate_count(self.root)
        if count:
            self.controller.run_op("frames.duplicate", copies=count)

    # ---- menu state ------------------------------------------------------

    def _refresh_edit_menu(self) -> None:
        c = self.controller
        undo_text = f"Undo {c.undo_label}" if c.undo_label else "Undo"
        redo_text = f"Redo {c.redo_label}" if c.redo_label else "Redo"
        self.edit_menu.entryconfigure(0, label=undo_text, state="normal" if c.can_undo else "disabled")
        self.edit_menu.entryconfigure(1, label=redo_text, state="normal" if c.can_redo else "disabled")
        has_doc = c.doc is not None
        self.edit_menu.entryconfigure(3, state="normal" if has_doc else "disabled")
        self.edit_menu.entryconfigure(4, state="normal" if c.selection else "disabled")

    def _refresh_frames_menu(self) -> None:
        for entry, op_id in self._frame_menu_entries:
            self.frames_menu.entryconfigure(
                entry, state="normal" if self.controller.can_run(op_id) else "disabled"
            )
        self.frames_menu.entryconfigure(
            self._duplicate_n_entry,
            state="normal" if self.controller.can_run("frames.duplicate") else "disabled",
        )

    def _on_space(self, _event: tk.Event) -> str:
        self.controller.toggle_play()
        return "break"  # keep space from also 'clicking' a focused button

    def _on_speed(self, _event: tk.Event) -> None:
        label = self.speed.get()
        for text, factor in SPEEDS:
            if text == label:
                self.controller.set_speed(factor)
                break

    # ---- the timer -------------------------------------------------------

    def _on_timer(self) -> None:
        now = time.perf_counter()
        dt_ms = (now - self._last_tick) * 1000.0
        self._last_tick = now
        self.controller.tick(dt_ms)
        self.root.after(TIMER_MS, self._on_timer)

    # ---- event handlers (payloads are keyword args; **_ tolerates growth) -

    def _on_doc_changed(self, doc=None, selection=None, index=0, reason="", **_) -> None:
        self.canvas.invalidate()
        if doc is not None:
            self.thumbnails.retain({f.image_uid for f in doc})
        else:
            self.thumbnails.clear()
        # Keep the timeline scrolled where it was during an edit; only jump back
        # to the start for a genuinely new document.
        self.timeline.set_document(doc, reset_view=reason in ("open", "close"))
        if selection is not None:
            self.timeline.set_selection(selection)
        self.timeline.set_index(index)
        self._render()
        self._update_transport()

    def _on_selection_changed(self, selection=None, **_) -> None:
        if selection is not None:
            self.timeline.set_selection(selection)

    def _on_playhead_moved(self, index: int = 0, **_) -> None:
        self.timeline.set_index(index)
        self._render()
        self._update_transport()

    def _on_playback_state(self, playing: bool = False, **_) -> None:
        self.play_button.configure(text="Pause" if playing else "Play")

    def _on_title_changed(self, path: Path | None = None, dirty: bool = False, **_) -> None:
        self._set_title(path, dirty)

    def _on_status(self, message: str = "", **_) -> None:
        self.status.configure(text=message)
        self.root.update_idletasks()

    def _on_error(self, exception: BaseException, context: str = "", **_) -> None:
        self.status.configure(text="Ready")
        detail = f"{exception}\n\n{context}" if context else str(exception)
        messagebox.showerror(APP_NAME, detail, parent=self.root)

    # ---- rendering -------------------------------------------------------

    def _render(self) -> None:
        image = self.controller.frame_image()
        if image is None:
            self.canvas.show_placeholder(EMPTY_TEXT)
            self.status.configure(text="Ready")
            return
        doc = self.controller.doc
        key = doc[self.controller.index].image_uid if doc is not None else None
        self.canvas.show(image, key=key)
        self.status.configure(text=self._summary())

    def _update_transport(self) -> None:
        doc = self.controller.doc
        if doc is None:
            self.counter.configure(text="")
            self.play_button.configure(text="Play", state="disabled")
            self.speed.configure(state="disabled")
            return
        self.play_button.configure(
            text="Pause" if self.controller.playing else "Play",
            state="normal" if self.controller.can_play else "disabled",
        )
        self.speed.configure(state="readonly")
        self.counter.configure(text=f"Frame {self.controller.index + 1} of {len(doc)}")

    def _summary(self) -> str:
        """Derived from state, not from a remembered event, so it can't drift
        out of sync by missing one."""
        doc = self.controller.doc
        if doc is None:
            return "Ready"
        return (
            f"{doc.size[0]}x{doc.size[1]}   |   "
            f"{doc.total_duration_ms / 1000:.2f}s   |   "
            f"{_format_bytes(doc.nbytes_estimate)}"
        )

    def _set_title(self, path: Path | None, dirty: bool) -> None:
        if path is None:
            self.root.title(APP_NAME)
            return
        mark = "*" if dirty else ""
        self.root.title(f"{mark}{path.name} - {APP_NAME}")

    def _with_busy_cursor(self, action) -> None:
        """Reads block the mainloop, so at least say so and look busy.

        Deliberately not root.update() inside the read: that would re-enter the
        event loop and let the user click Open again mid-load. Real progress
        reporting waits for threading at M5 (ARCHITECTURE.md risk 7).
        """
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            action()
        finally:
            self.root.configure(cursor="")


class TkFrontend(Frontend):
    def run(self, controller: AppController, initial_path: Path | None = None) -> None:
        root = tk.Tk()
        window = MainWindow(root, controller)
        if initial_path is not None:
            # after() so the window is mapped and subscribed first: errors and
            # status from this load then have somewhere to go.
            root.after(50, lambda: window.open_path(initial_path))
        root.mainloop()
