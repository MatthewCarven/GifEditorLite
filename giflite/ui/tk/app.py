"""Tkinter frontend: window, menu, wiring.

Subscribes to the events in ARCHITECTURE.md 9 and calls controller methods.
Owns no application state -- the playhead, selection and document all live
behind the controller, which is what stops a second frontend from having to
re-derive them.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from giflite.app import events as ev
from giflite.app.controller import AppController
from giflite.core.io import open_filter
from giflite.ui.base import Frontend
from giflite.ui.tk.canvas import PreviewCanvas

APP_NAME = "GIF Editor Lite"
EMPTY_TEXT = "No animation open\n\nCtrl+O to open a GIF"


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

        root.title(APP_NAME)
        root.geometry("900x620")
        root.minsize(420, 320)

        self._build_menu()
        self._build_body()
        self._subscribe()

        # The controller may already hold a document opened from argv, so
        # render current state rather than assuming we start empty.
        self._render()
        self._set_title(controller.path, controller.dirty)

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
        # bind_all so the accelerator works regardless of which widget has focus
        self.root.bind_all("<Control-o>", lambda _e: self.open_file())
        self.root.bind_all("<Control-w>", lambda _e: self.controller.close())

    def _build_body(self) -> None:
        self.canvas = PreviewCanvas(self.root)
        self.canvas.pack(side="top", fill="both", expand=True)

        self.status = ttk.Label(self.root, text="Ready", anchor="w", padding=(8, 3))
        self.status.pack(side="bottom", fill="x")

    def _subscribe(self) -> None:
        bus = self.controller.events
        bus.on(ev.DOC_CHANGED, self._on_doc_changed)
        bus.on(ev.PLAYHEAD_MOVED, self._on_playhead_moved)
        bus.on(ev.TITLE_CHANGED, self._on_title_changed)
        bus.on(ev.STATUS, self._on_status)
        bus.on(ev.ERROR, self._on_error)

    # ---- commands --------------------------------------------------------

    def open_file(self) -> None:
        # File pickers are frontend policy: the controller takes a path that
        # has already been chosen, and knows nothing about dialogs.
        path = filedialog.askopenfilename(
            title="Open animation",
            filetypes=open_filter(),
        )
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> None:
        self._with_busy_cursor(lambda: self.controller.open(path))

    # ---- event handlers (payloads are keyword args; **_ tolerates growth) -

    def _on_doc_changed(self, **_: object) -> None:
        self._render()

    def _on_playhead_moved(self, **_: object) -> None:
        self._render()

    def _on_title_changed(self, path: Path | None = None, dirty: bool = False, **_) -> None:
        self._set_title(path, dirty)

    def _on_status(self, message: str = "", **_: object) -> None:
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
        self.canvas.show(image)
        self.status.configure(text=self._summary())

    def _summary(self) -> str:
        """Derived from state, not from a remembered event.

        The controller emits transient messages ("Loading...", size warnings);
        anything that is really a view of current state gets computed here, so
        it can't drift out of sync by missing an event.
        """
        doc = self.controller.doc
        if doc is None:
            return "Ready"
        return (
            f"Frame {self.controller.index + 1} of {len(doc)}   |   "
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

        Deliberately not `root.update()` inside the read: that would re-enter
        the event loop and let the user click Open again mid-load. Real
        progress reporting waits for threading at M5 (ARCHITECTURE.md risk 7).
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
