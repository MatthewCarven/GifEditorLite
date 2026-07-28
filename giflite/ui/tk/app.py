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
from tkinter import colorchooser, filedialog, messagebox, ttk

from giflite.app import events as ev
from giflite.app.cache import ThumbnailCache
from giflite.app.controller import AppController
from giflite.core.io import open_filter, save_filter
from giflite.core.model import Selection
from giflite.core.ops import get_op, menu_groups, op_params
from giflite.ui.base import Frontend
from giflite.ui.tk.canvas import PreviewCanvas
from giflite.ui.tk.dialogs import ask_params
from giflite.ui.tk.timeline import Timeline
from giflite.ui.tk.tools import default_tools

APP_NAME = "GIF Editor Lite"
EMPTY_TEXT = "No animation open\n\nCtrl+O to open a GIF"

# The palette's no-tool selection: plain viewing, no gesture armed.
CURSOR_TOOL = "cursor"

# Widget classes that own their keystrokes. Every bare-key shortcut below is
# also an editing key inside a text field -- Left/Right/Home/End move the caret,
# BackSpace and Delete remove a character, space types one, and b/e/i/c are
# letters someone is trying to type. `bind_all` fires after the widget's own
# class binding, so without this guard typing "12" then BackSpace in the brush
# Size box edits the number *and* deletes a frame.
TEXT_ENTRY_CLASSES = frozenset(
    {"Entry", "TEntry", "Spinbox", "TSpinbox", "Text", "TCombobox", "Combobox"}
)

# Op id prefix -> menu title. Order here is menu order in the bar.
OP_MENUS = (("frames", "Frames"), ("timing", "Timing"), ("canvas", "Image"))

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


def _rgb_hex(color) -> str:
    """(r, g, b, a) -> '#rrggbb' for Tk; alpha is ignored (Tk has no alpha)."""
    r, g, b = int(color[0]), int(color[1]), int(color[2])
    return f"#{r:02x}{g:02x}{b:02x}"


class MainWindow:
    def __init__(self, root: tk.Tk, controller: AppController) -> None:
        self.root = root
        self.controller = controller
        self.thumbnails = ThumbnailCache()

        root.title(APP_NAME)
        root.geometry("900x680")
        root.minsize(480, 400)

        # Tool state (frontend-owned; ARCHITECTURE.md 19). The active tool --
        # crop, pencil, eraser or eyedropper -- plus the settings tools read
        # through the ToolContext (implemented by this window).
        self._tools = default_tools()
        self._active_tool = None
        self._fg_color = (0, 0, 0, 255)
        self._brush_size = 4

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

        self.file_menu = tk.Menu(menubar, tearoff=False, postcommand=self._refresh_file_menu)
        self.file_menu.add_command(label="Open...", accelerator="Ctrl+O", command=self.open_file)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save_file)
        self.file_menu.add_command(label="Save As...", accelerator="Ctrl+Shift+S", command=self.save_file_as)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Close", accelerator="Ctrl+W", command=self.controller.close)
        self.file_menu.add_command(label="Exit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=self.file_menu)

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

        # View is pure frontend: none of it reaches the controller, because zoom
        # and pan are the frontend's entirely (ARCHITECTURE.md 9). It is also
        # the only menu here with no enable/disable refresh -- zooming an empty
        # window is harmless and the transform simply has nothing to scale.
        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="Zoom In", accelerator="Ctrl++",
                              command=self.zoom_in)
        view_menu.add_command(label="Zoom Out", accelerator="Ctrl+-",
                              command=self.zoom_out)
        view_menu.add_separator()
        view_menu.add_command(label="Fit to Window", accelerator="Ctrl+0",
                              command=self.zoom_fit)
        view_menu.add_command(label="Actual Size", accelerator="Ctrl+1",
                              command=self.zoom_actual)
        menubar.add_cascade(label="View", menu=view_menu)

        # One menu per op group, built entirely from the registry. Adding an op
        # (even a whole new group) needs no change here beyond OP_MENUS.
        for group_key, title in OP_MENUS:
            menu, entries = self._build_op_menu(menubar, group_key, title)
            if group_key == "canvas":
                # Crop is a canvas op but gesture-driven (in_menu=False), so it
                # isn't in menu_groups(). The menu item just selects the crop
                # tool; it rides the group's existing enable/disable refresh via
                # can_run("canvas.crop").
                menu.add_separator()
                menu.add_command(label="Crop", accelerator="C",
                                 command=lambda: self._select_tool("crop"))
                entries.append((menu.index("end"), "canvas.crop"))

        self.root.config(menu=menubar)

        # bind_all so shortcuts work regardless of which widget has focus --
        # except that a text field has a better claim on a bare key than we do,
        # so those go through _unless_typing.
        self.root.bind_all("<Control-o>", lambda _e: self.open_file())
        self.root.bind_all("<Control-s>", lambda _e: self.save_file())
        self.root.bind_all("<Control-Shift-S>", lambda _e: self.save_file_as())
        self.root.bind_all("<Control-w>", lambda _e: self.controller.close())
        bind_key = self._bind_bare_key
        bind_key("<space>", self._on_space)
        bind_key("<Left>", lambda _e: self.controller.step(-1))
        bind_key("<Right>", lambda _e: self.controller.step(1))
        bind_key("<Home>", lambda _e: self.controller.seek(0))
        bind_key("<End>", lambda _e: self.controller.seek(self.controller.frame_count - 1))
        # editing shortcuts -- the controller no-ops these when they can't apply
        self.root.bind_all("<Control-z>", lambda _e: self.controller.undo())
        self.root.bind_all("<Control-Shift-Z>", lambda _e: self.controller.redo())
        self.root.bind_all("<Control-y>", lambda _e: self.controller.redo())
        self.root.bind_all("<Control-a>", lambda _e: self._select_all())
        # Ctrl+D is the fast path: duplicate once, no dialog. The menu item
        # "Duplicate Frames..." opens the count dialog instead.
        self.root.bind_all("<Control-d>", lambda _e: self.controller.run_op("frames.duplicate"))
        bind_key("<Delete>", lambda _e: self.controller.run_op("frames.delete"))
        bind_key("<BackSpace>", lambda _e: self.controller.run_op("frames.delete"))
        # Tool shortcuts. Each selects a tool on the preview; the canvas owns Esc
        # while one is active, so the global Esc below still deselects the rest
        # of the time.
        bind_key("<c>", lambda _e: self._select_tool("crop"))
        bind_key("<b>", lambda _e: self._select_tool("pencil"))
        bind_key("<e>", lambda _e: self._select_tool("eraser"))
        bind_key("<i>", lambda _e: self._select_tool("eyedropper"))
        bind_key("<Escape>", lambda _e: self._clear_selection())
        # Zoom. Ctrl-combinations, so no _bind_bare_key guard is needed -- they
        # don't collide with typing. Both <Control-plus> and <Control-equal> are
        # bound because "+" is the shifted key on most layouts and nobody
        # reaches for Shift to zoom in.
        self.root.bind_all("<Control-plus>", lambda _e: self.zoom_in())
        self.root.bind_all("<Control-equal>", lambda _e: self.zoom_in())
        self.root.bind_all("<Control-minus>", lambda _e: self.zoom_out())
        self.root.bind_all("<Control-Key-0>", lambda _e: self.zoom_fit())
        self.root.bind_all("<Control-Key-1>", lambda _e: self.zoom_actual())

    # ---- keyboard routing ------------------------------------------------

    def focus_is_text_field(self) -> bool:
        """Whether the keyboard currently belongs to something being typed in."""
        try:
            widget = self.root.focus_get()
        except (KeyError, tk.TclError):
            # focus_get raises for a window Tk doesn't own -- not ours, not typing.
            return False
        if widget is None:
            return False
        try:
            return widget.winfo_class() in TEXT_ENTRY_CLASSES
        except tk.TclError:  # widget destroyed mid-event
            return False

    def _bind_bare_key(self, sequence: str, action) -> None:
        """Bind a shortcut that has no modifier, yielding to any text field.

        Returns None rather than "break" when it yields: the field's own class
        binding has already run by the time `bind_all` fires, and other listeners
        (a dialog's Escape, say) still deserve their turn.
        """

        def handler(event):
            if self.focus_is_text_field():
                return None
            return action(event)

        self.root.bind_all(sequence, handler)

    def _build_body(self) -> None:
        # Tool palette across the top; the preview canvas takes the slack below.
        self._build_toolbar()

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

        self.pingpong_var = tk.BooleanVar(value=False)
        self.pingpong_check = ttk.Checkbutton(
            bar, text="Ping-pong", variable=self.pingpong_var, command=self._on_pingpong
        )
        self.pingpong_check.pack(side="left", padx=(16, 0))

        ttk.Label(bar, text="Speed").pack(side="right", padx=(0, 4))
        self.speed = ttk.Combobox(
            bar, width=6, state="readonly", values=[label for label, _ in SPEEDS]
        )
        self.speed.set("1x")
        self.speed.bind("<<ComboboxSelected>>", self._on_speed)
        self.speed.pack(side="right")

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(8, 4))
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text="Tools").pack(side="left", padx=(0, 6))
        self._tool_var = tk.StringVar(value=CURSOR_TOOL)
        # "cursor" is the no-tool default (plain viewing); the rest map to entries
        # in self._tools. Radiobuttons give a free single-selection UI, and
        # exactly one tool being active is the point -- that's what having folded
        # crop in here buys (it used to be a mode running alongside them).
        for tid, text in (("cursor", "Cursor"), ("crop", "Crop"), ("pencil", "Pencil"),
                          ("eraser", "Eraser"), ("eyedropper", "Eyedropper")):
            ttk.Radiobutton(
                bar, text=text, value=tid, variable=self._tool_var,
                command=lambda t=tid: self._select_tool(t),
            ).pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Label(bar, text="Colour").pack(side="left", padx=(0, 4))
        # Classic tk.Button so the swatch can carry the colour as its background.
        self._swatch = tk.Button(bar, width=3, bg=_rgb_hex(self._fg_color),
                                 relief="sunken", command=self._choose_color)
        self._swatch.pack(side="left")

        ttk.Label(bar, text="Size").pack(side="left", padx=(10, 4))
        self._size_var = tk.StringVar(value=str(self._brush_size))
        self._size_var.trace_add("write", lambda *_: self._on_size_change())
        # Kept as an attribute so the smoke test can put focus in it: it's the
        # window's one text field, and therefore the thing bare-key shortcuts
        # have to yield to.
        self._size_box = ttk.Spinbox(bar, from_=1, to=64, width=4,
                                     textvariable=self._size_var)
        self._size_box.pack(side="left")

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

    def save_file(self) -> None:
        # Save writes to the current path; with none yet, fall back to Save As.
        if not self.controller.has_path:
            self.save_file_as()
            return
        # Nothing to write: say so and stop. Checked before the overwrite warning,
        # because warning about destroying an original we aren't going to touch
        # trains people to click through the one dialog that matters.
        if self.controller.save_would_change_nothing:
            self.controller.save()  # emits the status line; writes nothing
            return
        if self.controller.overwrites_source and not self._confirm_overwrite_source():
            return
        self._with_busy_cursor(self.controller.save)

    def _confirm_overwrite_source(self) -> bool:
        """Ask before Ctrl+S re-encodes the file the user opened.

        Saving is not a round trip -- the palette is rebuilt and identical
        consecutive frames are merged -- so an absent-minded Ctrl+S silently
        degrades someone's source file with no way back. Asking once (the
        controller clears the flag after any write) is cheap; a lost original
        isn't. Returns True to go ahead with the in-place save.
        """
        answer = messagebox.askyesnocancel(
            APP_NAME,
            f"Overwrite the original {self.controller.path.name}?",
            detail=(
                "Saving re-encodes the animation: the palette is rebuilt and "
                "identical consecutive frames are merged into longer holds. The "
                "file you opened cannot be recovered afterwards.\n\n"
                "Yes  -  overwrite it\n"
                "No  -  save to a new file instead"
            ),
            icon=messagebox.WARNING,
            default=messagebox.NO,  # the safe button is the one Enter picks
            parent=self.root,
        )
        if answer is None:
            return False  # Cancel: don't save, don't open a dialog either
        if answer:
            return True
        self.save_file_as()
        return False

    def save_file_as(self) -> None:
        if self.controller.doc is None:
            return
        path = filedialog.asksaveasfilename(
            title="Save animation",
            defaultextension=".gif",
            filetypes=save_filter(),
            # Naming policy lives in the controller so a second frontend inherits
            # it: "<name>_edited.gif" while the current path is still the original.
            initialfile=self.controller.suggested_save_name,
        )
        if path:
            self._with_busy_cursor(lambda: self.controller.save_as(Path(path)))

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

    def _invoke_op(self, op_id: str) -> None:
        """Run an op from a menu: collect its params via a generated dialog
        first if it has any, otherwise run it straight."""
        op = get_op(op_id)
        if op is None or self.controller.doc is None:
            return
        if not op_params(op):
            self.controller.run_op(op_id)
            return
        values = ask_params(self.root, op, self.controller.doc, self.controller.selection)
        if values is not None:  # None == cancelled
            self.controller.run_op(op_id, **values)

    # ---- tools (the canvas tools call the ToolContext below) -------------

    # ---- view ------------------------------------------------------------
    #
    # Thin on purpose: the canvas owns the transform and the gesture-cancelling
    # that a view change implies, so these exist only to refresh the readout
    # afterwards. Nothing here touches the controller.

    def zoom_in(self) -> None:
        self.canvas.zoom_in()
        self._update_status()

    def zoom_out(self) -> None:
        self.canvas.zoom_out()
        self._update_status()

    def zoom_fit(self) -> None:
        self.canvas.zoom_fit()
        self._update_status()

    def zoom_actual(self) -> None:
        self.canvas.zoom_actual()
        self._update_status()

    def _update_status(self) -> None:
        self.status.configure(text=self._summary())

    def _select_tool(self, tool_id: str) -> None:
        """Activate a tool -- crop, pencil, eraser, eyedropper -- or 'cursor' to
        put tools away. One entry point for the palette, the shortcuts and the
        Image > Crop menu item alike."""
        if tool_id != CURSOR_TOOL and self.controller.doc is None:
            return  # nothing to work on; leave the palette on Cursor
        self._tool_var.set(tool_id)
        tool = self._tools.get(tool_id)
        self._active_tool = tool
        if tool is None:
            self.canvas.clear_tool()
            self.status.configure(text=self._summary())
            return
        # Every tool is an editing mode, not a viewing one: a running preview
        # would repaint over the live overlay on the next tick.
        self.controller.pause()
        self.canvas.set_tool(tool, self)
        self.status.configure(text=f"{tool.label}: {tool.hint}")

    def end_tool(self) -> None:
        """ToolContext hook: put tools away (the canvas calls this on Esc)."""
        self._select_tool(CURSOR_TOOL)

    def _choose_color(self) -> None:
        rgb, _hex = colorchooser.askcolor(
            color=_rgb_hex(self._fg_color), parent=self.root, title="Foreground colour")
        if rgb is not None:
            self._set_fg_color((int(rgb[0]), int(rgb[1]), int(rgb[2]), 255))

    def _set_fg_color(self, rgba) -> None:
        self._fg_color = (int(rgba[0]), int(rgba[1]), int(rgba[2]),
                          int(rgba[3]) if len(rgba) > 3 else 255)
        self._swatch.configure(bg=_rgb_hex(self._fg_color))

    def _on_size_change(self) -> None:
        try:
            self._brush_size = max(1, min(int(float(self._size_var.get())), 256))
        except (TypeError, ValueError):
            pass  # mid-edit garbage in the spinbox; keep the last good size

    # ToolContext protocol -- see ui/tk/tools.py -------------------------------

    @property
    def frame_index(self) -> int:
        return self.controller.index

    @property
    def brush_size(self) -> int:
        return self._brush_size

    @property
    def fg_color(self):
        return self._fg_color

    def commit(self, op_id: str, **params) -> None:
        self.controller.run_op(op_id, **params)

    def pick_color(self, x: int, y: int) -> None:
        image = self.controller.frame_image()
        if image is None:
            return
        w, h = image.size
        px = max(0, min(int(x), w - 1))
        py = max(0, min(int(y), h - 1))
        r, g, b, _a = image.getpixel((px, py))
        self._set_fg_color((r, g, b, 255))  # adopt it opaque, so it always paints

    def preview_stroke(self, points, erase: bool = False) -> None:
        self.canvas.show_stroke_overlay(points, _rgb_hex(self._fg_color),
                                        self._brush_size, erase)

    def preview_rect(self, box) -> None:
        self.canvas.show_rect_overlay(box)

    def clear_preview(self) -> None:
        self.canvas.clear_overlay()

    # ---- menu construction / state ---------------------------------------

    def _build_op_menu(self, menubar: tk.Menu, group_key: str, title: str):
        menu = tk.Menu(menubar, tearoff=False)
        entries: list[tuple[int, str]] = []
        for op in menu_groups().get(group_key, []):
            # "..." signals a dialog; it's a UI convention, so it lives here
            # rather than in the op's label (which feeds "Undo <label>").
            label = op.label + ("..." if op_params(op) else "")
            menu.add_command(label=label, accelerator=op.accel,
                             command=lambda oid=op.id: self._invoke_op(oid))
            entries.append((menu.index("end"), op.id))
        menu.configure(postcommand=lambda m=menu, e=entries: self._refresh_op_menu(m, e))
        menubar.add_cascade(label=title, menu=menu)
        # Returned so callers can append non-registry items (e.g. gesture-driven
        # Crop) that still share the group's enable/disable refresh.
        return menu, entries

    def _refresh_op_menu(self, menu: tk.Menu, entries: list[tuple[int, str]]) -> None:
        for index, op_id in entries:
            menu.entryconfigure(
                index, state="normal" if self.controller.can_run(op_id) else "disabled"
            )

    def _refresh_file_menu(self) -> None:
        has_doc = self.controller.doc is not None
        state = "normal" if has_doc else "disabled"
        # Save (index 2), Save As (3), Close (5) all need a document.
        for entry in (2, 3, 5):
            self.file_menu.entryconfigure(entry, state=state)

    def _refresh_edit_menu(self) -> None:
        c = self.controller
        undo_text = f"Undo {c.undo_label}" if c.undo_label else "Undo"
        redo_text = f"Redo {c.redo_label}" if c.redo_label else "Redo"
        self.edit_menu.entryconfigure(0, label=undo_text, state="normal" if c.can_undo else "disabled")
        self.edit_menu.entryconfigure(1, label=redo_text, state="normal" if c.can_redo else "disabled")
        has_doc = c.doc is not None
        self.edit_menu.entryconfigure(3, state="normal" if has_doc else "disabled")
        self.edit_menu.entryconfigure(4, state="normal" if c.selection else "disabled")

    def _on_space(self, _event: tk.Event) -> str:
        self.controller.toggle_play()
        return "break"  # keep space from also 'clicking' a focused button

    def _on_speed(self, _event: tk.Event) -> None:
        label = self.speed.get()
        for text, factor in SPEEDS:
            if text == label:
                self.controller.set_speed(factor)
                break

    def _on_pingpong(self) -> None:
        self.controller.set_pingpong(self.pingpong_var.get())

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
            self._select_tool(CURSOR_TOOL)  # no document -> put tools away
        # Keep the timeline scrolled where it was during an edit; only jump back
        # to the start for a genuinely new document. Zoom follows the same rule
        # for the same reason: an edit -- crop especially -- should leave you
        # looking at the same magnification, and only a new file earns a reset.
        if reason in ("open", "close"):
            self.canvas.reset_view()
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
            self.pingpong_check.configure(state="disabled")
            return
        self.play_button.configure(
            text="Pause" if self.controller.playing else "Play",
            state="normal" if self.controller.can_play else "disabled",
        )
        self.speed.configure(state="readonly")
        self.pingpong_check.configure(state="normal" if self.controller.can_play else "disabled")
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
            f"{_format_bytes(doc.nbytes_estimate)}   |   "
            # Fit reports its percentage too: "Fit" alone leaves you unable to
            # tell a 40% view from a 400% one, which matters most on exactly the
            # small pixel-art GIFs that get blown up to fill the window.
            f"{self.canvas.view.label}"
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
