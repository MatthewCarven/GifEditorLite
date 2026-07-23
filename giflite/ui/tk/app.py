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
from giflite.ui.base import Frontend
from giflite.ui.tk.canvas import PreviewCanvas
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
        self.root.config(menu=menubar)

        # bind_all so shortcuts work regardless of which widget has focus
        self.root.bind_all("<Control-o>", lambda _e: self.open_file())
        self.root.bind_all("<Control-w>", lambda _e: self.controller.close())
        self.root.bind_all("<space>", self._on_space)
        self.root.bind_all("<Left>", lambda _e: self.controller.step(-1))
        self.root.bind_all("<Right>", lambda _e: self.controller.step(1))
        self.root.bind_all("<Home>", lambda _e: self.controller.seek(0))
        self.root.bind_all("<End>", lambda _e: self.controller.seek(self.controller.frame_count - 1))

    def _build_body(self) -> None:
        # Packed bottom-up so the preview canvas takes all the slack.
        self.status = ttk.Label(self.root, text="Ready", anchor="w", padding=(8, 3))
        self.status.pack(side="bottom", fill="x")

        self.timeline = Timeline(self.root, self.thumbnails, on_pick=self._on_pick)
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

    def _on_pick(self, index: int) -> None:
        from giflite.core.model import Selection

        self.controller.seek(index)
        self.controller.set_selection(Selection.single(index))

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

    def _on_doc_changed(self, doc=None, selection=None, index=0, **_) -> None:
        self.canvas.invalidate()
        if doc is not None:
            self.thumbnails.retain({f.image_uid for f in doc})
        else:
            self.thumbnails.clear()
        self.timeline.set_document(doc)
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
