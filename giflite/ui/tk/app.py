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
from typing import Callable

from giflite.app import events as ev
from giflite.app.cache import ThumbnailCache
from giflite.app.controller import AppController
from giflite.core.io import format_for, open_filter, save_filter
from giflite.core.model import MIN_DURATION_MS, Region, Selection
from giflite.core.ops import get_op, menu_groups, op_params
from giflite.ui.base import Frontend
from giflite.ui.tk.canvas import PreviewCanvas
from giflite.ui.tk.dialogs import ask_params, ask_values
from giflite.ui.tk.minimap import MiniMap
from giflite.ui.tk.timeline import Timeline
from giflite.ui.tk.tools import default_tools
from giflite.ui.tk.view import (
    GRID_ALWAYS,
    GRID_AUTO,
    GRID_AUTO_SCALE,
    GRID_OFF,
    PAN_STEP,
)

APP_NAME = "GIF Editor Lite"
EMPTY_TEXT = "No animation open\n\nCtrl+O to open a GIF"

# The side panel: wide enough for two columns of tool radios and a readable map
# with a "Fit (3200%)" readout, narrow enough to leave a usable preview at the
# window's 480px minimum width.
PANEL_WIDTH = 200
MINIMAP_HEIGHT = 120

# The side panel's sections, most important first. `_relayout_panel` walks this
# order and stands a section down *whole* when the panel runs out of height --
# see `_build_side_panel` for why the order is this one, and 23.6 for why the
# priority list exists at all rather than a `_fits` check per section.
PANEL_SECTIONS = ("tools", "paint", "delay", "view")

# Vertical space a packed section costs beyond its own requested height: the
# panel's 6px top and bottom padding, plus an 8px gap above every section but
# the first. Both are pack options set below, not guesses -- and getting them
# wrong is the whole of §23.6, so they are named here rather than added as a
# magic number inside the check.
PANEL_PADDING_V = 12
SECTION_GAP = 8

# The palette's no-tool selection: plain viewing, no gesture armed.
CURSOR_TOOL = "cursor"

# Palette order, filling the two-column grid left-to-right. Cursor first because
# it is the way *out* of a tool, then Select and Crop -- the two that address a
# rectangle rather than a mark -- then marks (pencil, eraser, fill), shapes
# (line, rect, ellipse), and the one that doesn't paint at all.
TOOL_BUTTONS = (
    ("cursor", "Cursor"), ("select", "Select"),
    ("move", "Move"), ("crop", "Crop"),
    ("pencil", "Pencil"), ("eraser", "Eraser"),
    ("fill", "Fill"), ("line", "Line"),
    ("rect", "Rect"), ("ellipse", "Ellipse"),
    ("eyedropper", "Picker"),
)

# Bare-key shortcuts for the palette. B/E/I/C are the inherited ones (b for
# brush, i for the picker -- both borrowed from every other editor); F/L/R/O/S
# are the new ones and were checked against the existing bare keys, which are
# space, the arrows, Home/End and Delete/BackSpace. All of them yield to a
# focused text field via `_bind_guarded_key`, or typing "4" then "e" in the Size
# box would quietly swap your tool.
TOOL_KEYS = (
    ("s", "select"), ("m", "move"), ("c", "crop"), ("b", "pencil"), ("e", "eraser"),
    ("f", "fill"), ("l", "line"), ("r", "rect"), ("o", "ellipse"),
    ("i", "eyedropper"),
)

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

# Pixel-grid modes as the menu presents them. Auto names its own threshold in
# the label: a menu item whose effect depends on a number the user cannot see is
# an item they will toggle twice and then distrust.
GRID_CHOICES = (
    (GRID_OFF, "Off"),
    (GRID_AUTO, f"Auto (from {int(GRID_AUTO_SCALE * 100)}%)"),
    (GRID_ALWAYS, "Always"),
)


def _format_bytes(nbytes: int) -> str:
    """Adaptive units, because '~0 MB' for a small GIF just looks broken."""
    if nbytes < 1024:
        return f"{nbytes} B"
    if nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.0f} KB"
    return f"{nbytes / (1024 * 1024):.1f} MB"


def _image_count(folder: Path) -> int:
    """How many images a folder already holds, for the export warning. Zero for
    a folder that doesn't exist yet, which is the tidy case."""
    from giflite.core.io.sequence import SEQUENCE_SUFFIXES
    try:
        return sum(1 for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in SEQUENCE_SUFFIXES)
    except (OSError, FileNotFoundError):
        return 0


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
        # 720 rather than the original 680 because the side panel outgrew it:
        # four sections at their natural heights want 516px of panel and 680
        # gives 505. That was true before this was measured -- the old fit check
        # under-counted padding by ~45px, so it showed the view section anyway
        # and `pack` clipped the Fit/1:1 row to 9px of the 28 it wants (§23.6).
        # Growing the default costs nothing and leaves ~29px of slack, which
        # matters because Windows' font metrics are not X11's.
        root.geometry("900x720")
        root.minsize(480, 400)

        # Tool state (frontend-owned; ARCHITECTURE.md 19). The active tool --
        # crop, pencil, eraser or eyedropper -- plus the settings tools read
        # through the ToolContext (implemented by this window).
        self._tools = default_tools()
        self._active_tool = None
        self._fg_color = (0, 0, 0, 255)
        self._brush_size = 4
        # Set before _build_body: the delay box's <FocusOut> can fire while the
        # window is still being assembled, and _commit_delay reads this first.
        self._refreshing_delay = False

        self._build_menu()
        self._build_body()
        self._subscribe()
        # A resize changes the fit scale with no command behind it, so the panel
        # has to follow the canvas rather than only the menu. The *status line*
        # too, and pointing this at the narrower `_refresh_view_controls` was a
        # real bug: showing or hiding the panel resizes the canvas, which re-fits
        # it, so the status line kept a percentage the readout had already moved
        # past. Both are derived from the same state, so both refresh together.
        self.canvas.on_view_change = self._update_status
        self._grid_var.set(self.canvas.view.grid_mode)

        self._render()
        self._set_title(controller.path, controller.dirty, controller.source_label)
        self._update_transport()

        self._last_tick = time.perf_counter()
        self.root.after(TIMER_MS, self._on_timer)

    # ---- construction ----------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        self.file_menu = tk.Menu(menubar, tearoff=False, postcommand=self._refresh_file_menu)
        self.file_menu.add_command(label="Open...", accelerator="Ctrl+O", command=self.open_file)
        # Import/Export sit apart from Open/Save on purpose: an imported folder
        # is a source, not a home, so the document it makes has no path to save
        # back to (ARCHITECTURE.md 25.3). Folding them into Open would make
        # Ctrl+S mean "write a GIF over those PNGs".
        self.file_menu.add_command(label="Import Frames...", command=self.import_frames)
        self.file_menu.add_command(label="Export Frames...", command=self.export_frames)
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
        # Region editing. The menu is where these are discoverable -- the
        # shortcuts are the ones everybody already knows, but "cut what?" has a
        # non-obvious answer here (a rectangle of canvas, not frames), and a
        # greyed-out item that says why is the cheapest way to teach it.
        self.edit_menu.add_command(label="Cut Area", accelerator="Ctrl+X",
                                   command=self.cut_region)
        self.edit_menu.add_command(label="Copy Area", accelerator="Ctrl+C",
                                   command=self.copy_region)
        self.edit_menu.add_command(label="Paste", accelerator="Ctrl+V",
                                   command=self.paste_region)
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
        view_menu.add_separator()
        # Radiobuttons in a submenu rather than a checkbox, because the mode is
        # genuinely three-valued and the variable makes the menu report the
        # current state for free -- no postcommand refresh to forget.
        # Left empty here and seeded from the transform in __init__ once the
        # canvas exists: the default grid mode lives in view.py, and a second
        # copy of it in the menu is how the two drift apart.
        self._grid_var = tk.StringVar()
        grid_menu = tk.Menu(view_menu, tearoff=False)
        for mode, label in GRID_CHOICES:
            grid_menu.add_radiobutton(
                label=label, value=mode, variable=self._grid_var,
                command=lambda m=mode: self.set_grid_mode(m),
            )
        view_menu.add_cascade(label="Pixel Grid", menu=grid_menu)
        view_menu.add_command(label="Cycle Pixel Grid", accelerator="Ctrl+'",
                              command=self.cycle_grid_mode)
        menubar.add_cascade(label="View", menu=view_menu)

        # One menu per op group, built entirely from the registry. Adding an op
        # (even a whole new group) needs no change here beyond OP_MENUS.
        self.op_menus: dict[str, tuple[tk.Menu, list]] = {}
        for group_key, title in OP_MENUS:
            menu, entries = self._build_op_menu(menubar, group_key, title)
            if group_key == "canvas":
                # Crop is a canvas op but gesture-driven (in_menu=False), so it
                # isn't in menu_groups(). The menu item just selects the crop
                # tool; it rides the group's existing enable/disable refresh by
                # bringing its own predicate.
                menu.add_separator()
                menu.add_command(label="Crop", accelerator="C",
                                 command=lambda: self._select_tool("crop"))
                entries.append((menu.index("end"),
                                lambda: self.controller.can_run("canvas.crop")))
                # The other way to reach the same op: a marquee is already the
                # four numbers it wants (§26), so this needs no gesture at all.
                # Two questions rather than a new controller predicate -- "can
                # this op run" and "is there a region" are both already askable,
                # and a `can_crop_to_region` would be `can_copy` under a second
                # name, which is the kind of duplication no test can catch.
                menu.add_command(label="Crop to Selection",
                                 command=self.crop_to_region)
                entries.append((menu.index("end"),
                                lambda: self.controller.can_run("canvas.crop")
                                and self.controller.region is not None))

        self.root.config(menu=menubar)

        # bind_all so shortcuts work regardless of which widget has focus --
        # except that a text field has a better claim on a bare key than we do,
        # so those go through _unless_typing.
        self.root.bind_all("<Control-o>", lambda _e: self.open_file())
        self.root.bind_all("<Control-s>", lambda _e: self.save_file())
        self.root.bind_all("<Control-Shift-S>", lambda _e: self.save_file_as())
        self.root.bind_all("<Control-w>", lambda _e: self.controller.close())
        bind_key = self._bind_guarded_key
        bind_key("<space>", self._on_space)
        # While something is floating the arrows place it, because stepping a
        # frame would settle the float first and the keys would then be doing
        # two very different things one keypress apart.
        bind_key("<Left>", lambda _e: self._arrow(-1, 0))
        bind_key("<Right>", lambda _e: self._arrow(1, 0))
        bind_key("<Up>", lambda _e: self._arrow(0, -1))
        bind_key("<Down>", lambda _e: self._arrow(0, 1))
        bind_key("<Home>", lambda _e: self.controller.seek(0))
        bind_key("<End>", lambda _e: self.controller.seek(self.controller.frame_count - 1))
        # Cut / copy / paste. **Guarded, despite carrying a modifier.** The rule
        # in §19.3 was written as "bare keys yield to text fields" and the real
        # rule is "a keystroke something else has a better claim on yields to
        # it" -- which Ctrl+C in the Size spinbox absolutely does. `bind_all`
        # fires after the widget's class binding, so without the guard copying
        # a number out of a text box would *also* copy a rectangle of canvas
        # into the image clipboard, silently replacing whatever was there.
        bind_key("<Control-x>", lambda _e: self.cut_region())
        bind_key("<Control-c>", lambda _e: self.copy_region())
        bind_key("<Control-v>", lambda _e: self.paste_region())
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
        for key, tool_id in TOOL_KEYS:
            bind_key(f"<{key}>", lambda _e, t=tool_id: self._select_tool(t))
        bind_key("<Return>", lambda _e: self._commit_float())
        bind_key("<KP_Enter>", lambda _e: self._commit_float())
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
        # Ctrl-modified, so it needs no `_bind_bare_key` guard: an apostrophe
        # typed into the Size box arrives without Control.
        self.root.bind_all("<Control-apostrophe>", lambda _e: self.cycle_grid_mode())

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

    def _bind_guarded_key(self, sequence: str, action) -> None:
        """Bind a shortcut that yields to whatever is being typed in.

        Was `_bind_bare_key`, on the theory that only unmodified keys collide
        with typing. Cut/copy/paste disproved it: Ctrl+C is a text-editing
        keystroke in every entry widget in this window, and `bind_all` fires
        *after* the widget's class binding, so an unguarded binding would copy
        the number and then also overwrite the image clipboard. The test is not
        "does it have a modifier", it is "does the focused widget have a better
        claim on this keystroke".

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

        # The side panel is packed before the canvas so the canvas takes what's
        # left. Unlike the old view-only strip it is *always* shown, because it
        # now carries the tool palette; only its view section comes and goes
        # (see `_refresh_view_controls`).
        self._build_side_panel()
        self.side_panel.pack(side="right", fill="y")

        self.canvas = PreviewCanvas(self.root)
        self.canvas.pack(side="top", fill="both", expand=True)

        # The sections themselves are packed by `_relayout_panel` and nowhere
        # else, which is why this call has to exist: built is not shown. It runs
        # after the canvas because deciding whether the view section is wanted
        # asks the canvas what the zoom is.
        self._relayout_panel()

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

    def _build_side_panel(self) -> None:
        """Everything that isn't the preview, the timeline or the transport.

        Four sections in one strip beside the canvas: the tool palette, the
        settings those tools read, the frame delay, and the view controls.

        **Why it isn't a toolbar any more.** The palette used to be a row across
        the top, and §21 records what that row did when it ran out of width: at
        1087px wanted against 900 available, `pack` dropped three widgets off the
        end with no error, and only a screenshot caught it. Adding fill and three
        shape tools would have pushed a five-tool row to nine. A vertical strip
        turns "runs out of width" -- which the window's 480px minimum makes
        permanent -- into "runs out of height", which is bounded and orderable.

        **The tools are a two-column grid, not a stack.** Nine stacked radios are
        ~190px of panel; two columns are ~105px, which is what keeps the view
        section fitting underneath at the default window size.

        **Every section is one frame, and that is load-bearing** (§23.6). A
        section built as loose siblings can only be amputated -- `pack` drops
        whichever child it reaches with no room left, silently, leaving the rest
        looking fine. One frame per section is what makes "show it whole or not
        at all" expressible, and `_relayout_panel` is what decides which.
        """
        self.side_panel = ttk.Frame(self.root, padding=(6, 6))
        # Built once, in PANEL_SECTIONS order; packed and unpacked from then on
        # by `_relayout_panel` alone. Nothing else in the frontend may pack a
        # direct child of the panel, or the height arithmetic stops describing
        # the panel it is measuring.
        self._sections: dict[str, ttk.Frame] = {
            name: ttk.Frame(self.side_panel) for name in PANEL_SECTIONS
        }
        self._sections_shown: set[str] = set()

        # ---- tools ----
        tools_section = self._sections["tools"]
        ttk.Label(tools_section, text="Tools").pack(side="top", anchor="w")
        tools = ttk.Frame(tools_section)
        tools.pack(side="top", fill="x", pady=(2, 0))
        self._tool_var = tk.StringVar(value=CURSOR_TOOL)
        # "cursor" is the no-tool default (plain viewing); the rest map to entries
        # in self._tools. Radiobuttons give a free single-selection UI, and
        # exactly one tool being active is the point -- that's what having folded
        # crop in here buys (it used to be a mode running alongside them).
        # Kept as an attribute so the smoke test can assert every one of them is
        # actually mapped; a radio that pack silently dropped is a tool the user
        # cannot reach, and that is the §21 failure exactly.
        self._tool_buttons: dict[str, ttk.Radiobutton] = {}
        for i, (tid, text) in enumerate(TOOL_BUTTONS):
            button = ttk.Radiobutton(
                tools, text=text, value=tid, variable=self._tool_var,
                command=lambda t=tid: self._select_tool(t),
            )
            button.grid(row=i // 2, column=i % 2, sticky="w")
            self._tool_buttons[tid] = button

        # ---- settings the tools read ----
        # The separator lives *inside* the section it heads rather than beside
        # it, so a section that stands down takes its rule with it. A dangling
        # divider under the last visible section is the same class of bug as the
        # amputation itself: the layout telling you about something that isn't
        # there.
        paint_section = self._sections["paint"]
        ttk.Separator(paint_section, orient="horizontal").pack(
            side="top", fill="x", pady=(0, 8))

        # Two balanced rows rather than one long one. Colour+Size on a single row
        # measured 216px, which pushed the whole panel to 228 and took that width
        # off the preview for no reason -- the minimap only needs 188.
        colour_row = ttk.Frame(paint_section)
        colour_row.pack(side="top", fill="x")
        # Kept as an attribute so erase mode can grey it: a `tk.Button` whose
        # background *is* the colour does not visibly change when disabled --
        # the explicit bg wins -- so the swatch alone cannot show that the
        # colour has stopped being used. The label greying is the visible half.
        self._colour_label = ttk.Label(colour_row, text="Colour")
        self._colour_label.pack(side="left")
        # Classic tk.Button so the swatch can carry the colour as its background.
        self._swatch = tk.Button(colour_row, width=3, bg=_rgb_hex(self._fg_color),
                                 relief="sunken", command=self._choose_color)
        self._swatch.pack(side="left", padx=(4, 0))

        # The two mode toggles, on a row of their own rather than tucked beside
        # the swatch. Both answer "what does a mark do" rather than "what does it
        # look like", so they belong together -- and putting a fourth widget on
        # the colour row would widen the panel, which comes straight off the
        # preview and is the one axis with no guard on it (§21). Height is
        # guarded; width is not, so height is the cheaper thing to spend.
        mode_row = ttk.Frame(paint_section)
        mode_row.pack(side="top", fill="x", pady=(6, 0))
        self._fill_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(mode_row, text="Fill", variable=self._fill_var).pack(
            side="left")
        self._erase_var = tk.BooleanVar(value=False)
        # Erase is the answer to "what colour do I fill with to get transparent".
        # None: painting composites *over*, so a transparent colour changes
        # nothing. Removing alpha is a different operation, and this is it --
        # for every tool at once, rather than as a second bucket in the palette.
        ttk.Checkbutton(mode_row, text="Erase", variable=self._erase_var,
                        command=self._on_erase_toggle).pack(side="right")

        size_row = ttk.Frame(paint_section)
        size_row.pack(side="top", fill="x", pady=(6, 0))
        ttk.Label(size_row, text="Size").pack(side="left", padx=(0, 4))
        self._size_var = tk.StringVar(value=str(self._brush_size))
        self._size_var.trace_add("write", lambda *_: self._on_size_change())
        # Kept as an attribute so the smoke test can put focus in it: it's one of
        # the window's two text fields, and therefore a thing bare-key shortcuts
        # have to yield to.
        self._size_box = ttk.Spinbox(size_row, from_=1, to=64, width=4,
                                     textvariable=self._size_var)
        self._size_box.pack(side="left")
        ttk.Label(size_row, text="Tol.").pack(side="left", padx=(10, 4))
        self._tolerance_var = tk.StringVar(value="0")
        self._tolerance_box = ttk.Spinbox(size_row, from_=0, to=255, width=4,
                                          textvariable=self._tolerance_var)
        self._tolerance_box.pack(side="left")

        # ---- per-frame timing ----
        # The fast path to `timing.set_delay`, which until now was a menu item
        # and a dialog. Retiming a frame is the correct way to hold a pose;
        # duplicating frames to do it bloats the file and multiplies the work of
        # every later edit, so the control for it should be at least as reachable
        # as the duplicate button.
        delay_section = self._sections["delay"]
        ttk.Separator(delay_section, orient="horizontal").pack(
            side="top", fill="x", pady=(0, 8))
        self._delay_label = ttk.Label(delay_section, text="Frame delay")
        self._delay_label.pack(side="top", anchor="w")
        delay_row = ttk.Frame(delay_section)
        delay_row.pack(side="top", fill="x", pady=(2, 0))
        self._delay_var = tk.StringVar(value="")
        self._delay_box = ttk.Spinbox(delay_row, from_=MIN_DURATION_MS, to=60000,
                                      increment=10, width=7,
                                      textvariable=self._delay_var)
        self._delay_box.pack(side="left")
        ttk.Label(delay_row, text="ms").pack(side="left", padx=(4, 0))
        # Commit on Enter or on leaving the box -- never per keystroke, which
        # would push "1", "10", "100" onto the undo stack as three edits. The
        # spinbox arrows commit too, via the same handler.
        self._delay_box.configure(command=self._commit_delay)
        self._delay_box.bind("<Return>", lambda _e: self._commit_delay())
        self._delay_box.bind("<FocusOut>", lambda _e: self._commit_delay())

        self._build_view_section()

    def _build_view_section(self) -> None:
        """The navigator and the zoom controls, at the bottom of the panel.

        Last in PANEL_SECTIONS on purpose, which now means "first to stand down"
        rather than "first to be starved". It is the section that is already
        optional -- at fit it says nothing anyone needs -- and it is by far the
        tallest, so a panel short of room buys the most by dropping it. The
        tools above it are not optional at all.
        """
        self.view_panel = self._sections["view"]

        self.minimap = MiniMap(self.view_panel, on_center=self._center_view_on,
                               height=MINIMAP_HEIGHT, width=PANEL_WIDTH - 12)
        self.minimap.pack(side="top", fill="x")

        zoom_row = ttk.Frame(self.view_panel)
        zoom_row.pack(side="top", fill="x", pady=(6, 0))
        self._zoom_out_button = ttk.Button(zoom_row, text="−", width=2,
                                           command=self.zoom_out)
        self._zoom_out_button.pack(side="left")
        # Fixed width, centred: the text runs from "50%" to "Fit (3200%)", and a
        # label that resizes with its content shoves the buttons around it.
        self._zoom_label = ttk.Label(zoom_row, width=11, anchor="center")
        self._zoom_label.pack(side="left", fill="x", expand=True)
        self._zoom_in_button = ttk.Button(zoom_row, text="+", width=2,
                                          command=self.zoom_in)
        self._zoom_in_button.pack(side="left")

        button_row = ttk.Frame(self.view_panel)
        button_row.pack(side="top", fill="x", pady=(4, 0))
        self._fit_button = ttk.Button(button_row, text="Fit", command=self.zoom_fit)
        self._fit_button.pack(side="left", fill="x", expand=True)
        self._actual_button = ttk.Button(button_row, text="1:1",
                                         command=self.zoom_actual)
        self._actual_button.pack(side="left", fill="x", expand=True)

    def _subscribe(self) -> None:
        bus = self.controller.events
        bus.on(ev.DOC_CHANGED, self._on_doc_changed)
        bus.on(ev.SELECTION_CHANGED, self._on_selection_changed)
        bus.on(ev.REGION_CHANGED, self._on_region_changed)
        bus.on(ev.FLOAT_CHANGED, self._on_float_changed)
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

    def import_frames(self) -> None:
        """Pick a folder of stills, ask the reader's questions, import it.

        The options dialog is generated from `Format.read_params` -- the format
        declares what the source cannot tell it, and this knows nothing about
        what those questions are. A video importer's fps arrives here for free.
        """
        folder = filedialog.askdirectory(title="Import frames from folder")
        if not folder:
            return
        path = Path(folder)
        fmt = format_for(path, readable=True)
        if fmt is None or fmt.read is None:
            messagebox.showerror("Import frames",
                                 f"Nothing here can read {path.name}.",
                                 parent=self.root)
            return
        values = ask_values(self.root, f"Import {path.name}", fmt.read_params)
        if values is None:
            return  # cancelled
        self._with_busy_cursor(lambda: self.controller.import_frames(path, **values))

    def export_frames(self) -> None:
        """Write every frame into a chosen folder as numbered PNGs.

        Warns before writing into a folder that already holds images. Export
        does not merely add files -- same-named frames are overwritten -- and
        the folder someone picks in a hurry is often one with something in it.
        Consistent with the Save-safety rule (§19.2): the *frontend* owns the
        warning, the controller owns the fact.
        """
        if self.controller.doc is None:
            return
        folder = filedialog.askdirectory(title="Export frames to folder")
        if not folder:
            return
        path = Path(folder)
        existing = _image_count(path)
        if existing and not messagebox.askokcancel(
            "Export frames",
            f"{path.name} already contains {existing} image file"
            f"{'s' if existing != 1 else ''}.",
            detail="Frames with the same names will be overwritten.",
            parent=self.root,
        ):
            return
        self._with_busy_cursor(lambda: self.controller.export_frames(path))

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
        """Esc, and the Deselect menu item: drop the region first, then frames.

        Esc is a five-stage ladder now, ordered by how recent and how transient
        each thing is: abandon the gesture, put back a floating move or paste,
        put the tool away (those three are the canvas's, since it owns Esc while
        a tool is active), clear the region, clear the frame selection. Each
        press undoes the most recent commitment, which is the only ordering
        nobody has to memorise.

        The float stage is repeated here because a paste can float with no tool
        active at all -- Ctrl+V selects Move, but the user can put it away
        again -- and then Esc never reaches the canvas.

        The region goes before the frames deliberately. It is the thing you can
        see on the canvas you are looking at, so it is what Esc appears to be
        aimed at; a frame selection that quietly vanished first would look like
        Esc had done nothing.
        """
        if self.controller.cancel_float():
            return
        if self.controller.region is not None:
            self.controller.set_region(None)
            return
        self.controller.set_selection(Selection.empty())

    # ---- region editing --------------------------------------------------
    #
    # Thin, like the zoom commands: the controller owns the region, the
    # clipboard and the scope rule, and these exist to say what happened when
    # nothing did. A shortcut that silently does nothing is indistinguishable
    # from one that isn't bound.

    def cut_region(self) -> None:
        if not self.controller.can_copy:
            self.status.configure(text="Select an area first (S, then drag)")
            return
        self.controller.cut_region()

    def copy_region(self) -> None:
        if not self.controller.can_copy:
            self.status.configure(text="Select an area first (S, then drag)")
            return
        self.controller.copy_region()

    def paste_region(self) -> None:
        """Ctrl+V floats the clipboard rather than landing it.

        One keystroke more for a paste in place -- Ctrl+V, Enter -- and it buys
        the thing paste-in-place could not do at all. The Move tool comes with
        it, so the very next drag places it; arriving in a state you cannot
        manipulate without first hunting for the right tool would be a worse
        trade than the extra keystroke.
        """
        if not self.controller.can_paste:
            self.status.configure(text="Nothing copied yet")
            return
        if self.controller.begin_paste():
            self._select_tool("move")

    def crop_to_region(self) -> None:
        """Image -> Crop to Selection: the marquee becomes the whole canvas.

        Menu-only for now. Every editor spells this differently and none of the
        obvious keys are free here -- C is the crop tool and S is Select -- so
        the shortcut is a decision to take on purpose rather than one to smuggle
        in with the feature.
        """
        if self.controller.region is None:
            self.status.configure(text="Select an area first (S, then drag)")
            return
        self.controller.crop_to_region()

    def set_region(self, region) -> None:
        """ToolContext hook: SelectTool finished a drag (or a click)."""
        self.controller.set_region(None if region is None else Region(*region))

    # ---- the floating edit -----------------------------------------------

    @property
    def floating(self) -> bool:
        return self.controller.floating is not None

    @property
    def float_offset(self) -> tuple[int, int]:
        return self.controller.float_offset

    def begin_move(self) -> bool:
        if self.controller.begin_move():
            return True
        self.status.configure(text="Select an area first (S, then drag)")
        return False

    def move_float(self, dx: int, dy: int) -> None:
        self.controller.move_float(dx, dy)

    def _commit_float(self) -> None:
        """Enter: land it. Silent when there is nothing floating, because Enter
        is also just a key people press."""
        self.controller.commit_float()

    def _arrow(self, dx: int, dy: int) -> None:
        """Arrows nudge a float, or step frames when nothing is floating.

        One binding doing two things, which is usually a smell -- but stepping a
        frame settles the float first (`_settle_float`), so without this the
        same key would place pixels and then, one press later, commit them and
        jump to another frame. Nudging is the only reading that stays put.
        """
        if self.floating:
            self.controller.nudge_float(dx, dy)
        elif dx:
            self.controller.step(dx)

    def _on_float_changed(self, floating=None, **_) -> None:
        """Redraw the preview from the uncommitted result.

        `_render` refreshes the status line through `_summary`, which is where
        the float's own message lives -- a float looks exactly like a committed
        edit, and "nothing has actually happened yet" is invisible unless
        something says so.
        """
        self._render()

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

    def pan(self, dx: float, dy: float) -> None:
        """One pan step, in units of `PAN_STEP` viewport-fractions. No UI drives
        this now that the navigator does the panning -- it stays because the
        canvas API is the seam a second frontend would use, and it is what the
        minimap's drag reduces to."""
        self.canvas.pan(dx * PAN_STEP, dy * PAN_STEP)
        self._update_status()

    def _center_view_on(self, ix: int, iy: int) -> None:
        """The navigator pointed at an image coordinate."""
        if self.canvas.center_view_on(ix, iy):
            self._update_status()

    def set_grid_mode(self, mode: str) -> None:
        self.canvas.set_grid_mode(mode)
        self._after_grid_change()

    def cycle_grid_mode(self) -> None:
        self.canvas.cycle_grid_mode()
        self._after_grid_change()

    def _after_grid_change(self) -> None:
        """Keep the menu in step and say what just happened.

        The announcement is not decoration. Two of the three modes can be
        switched on and change nothing you can see -- Auto below 400%, Always
        below 200% -- and a setting that silently does nothing is the failure
        this project has now hit from three directions (a toolbar row that
        dropped its widgets, a stale zoom readout, a Save that re-encoded for no
        gain). Saying "on, but not at this zoom" costs one line.

        Written straight to the label rather than through the controller's
        STATUS event, because the grid never reaches the controller (§9) and
        borrowing its bus to announce a frontend-only setting would be the first
        crack in that. The next view change overwrites it, exactly as it does
        for the controller's own messages.
        """
        view = self.canvas.view
        self._grid_var.set(view.grid_mode)
        label = dict(GRID_CHOICES)[view.grid_mode]
        if view.grid_suppressed:
            self.status.configure(
                text=f"Pixel grid: {label} - not shown at {view.label}")
        else:
            self.status.configure(text=f"Pixel grid: {label}")

    def _update_status(self) -> None:
        self.status.configure(text=self._summary())
        self._refresh_view_controls()

    def _refresh_view_controls(self) -> None:
        """Point the panel at what the transform can currently do.

        Driven from the canvas's own redraw as well as from the commands,
        because the fit scale changes on a window resize without anyone pressing
        anything -- and a `+` button that stays lit at 3200% is a lie about the
        ladder having more rungs.
        """
        view = self.canvas.view
        has_doc = self.controller.doc is not None
        self._zoom_label.configure(text=view.label)
        for button, enabled in (
            (self._zoom_in_button, view.can_zoom_in),
            (self._zoom_out_button, view.can_zoom_out),
            (self._fit_button, not view.is_fit),
            (self._actual_button, abs(view.scale - 1.0) > 1e-9),
        ):
            button.configure(state="normal" if (has_doc and enabled) else "disabled")
        self._relayout_panel()
        if self._panel_shown:
            self._update_minimap()

    @property
    def _panel_shown(self) -> bool:
        """Whether the view section is up. Kept because the minimap costs work.

        Derived rather than stored: `_relayout_panel` is the single writer of
        panel visibility now, and a second copy of "is the view section
        showing" would be a thing to forget to update.
        """
        return "view" in self._sections_shown

    def _section_wanted(self, name: str) -> bool:
        """Whether a section has anything to say, before height is considered.

        Only the view section has an answer other than yes: at fit the map's
        rectangle covers the whole image, which is to say it tells you nothing,
        so the section is pure cost.
        """
        if name == "view":
            view = self.canvas.view
            return self.controller.doc is not None and not view.is_fit
        return True

    def _relayout_panel(self) -> None:
        """Show as many sections as fit, in priority order, whole ones only.

        **Why this is a panel-level rule and not a check per section** (§23.6).
        It used to be one guard, `_view_section_fits`, standing in front of the
        one section anybody had watched `pack` amputate. Then the frame-delay
        section arrived with no guard of its own and was silently dropped at the
        480x400 minimum -- the same failure, third time, on a section added
        after both of the previous fixes. A per-section check only ever protects
        the sections someone remembered to check, and the section nobody thought
        about is exactly the one that breaks. So the panel decides for all of
        them, and a new section joins by being named in PANEL_SECTIONS.

        **Stop at the first section that doesn't fit**, rather than skipping it
        and trying the next. Sections are in priority order, so a later one
        appearing where an earlier one couldn't would say the panel is short of
        room *and* rank the missing one below what replaced it -- and because
        the later sections here are the taller ones, it would also flicker: drop
        the delay box, gain room, show the map, which needs more room than the
        box did. Greedy and monotonic is both simpler and steadier.

        Re-entrancy: packing changes the panel's width, which resizes the canvas,
        which fires `<Configure>` -> redraw -> back in here. Nothing below
        depends on the *width*, so the second pass computes the same answer and
        the no-op guard on each section stops the bouncing there.
        """
        available = self.side_panel.winfo_height()
        # Before the first layout Tk reports 1. Assume everything fits rather
        # than hiding it all on the way up; the next <Configure> asks again with
        # a real number.
        room = available - PANEL_PADDING_V if available > 1 else None

        for i, name in enumerate(PANEL_SECTIONS):
            section = self._sections[name]
            show = self._section_wanted(name)
            # The top section is never stood down. If the panel is too short
            # even for that, an empty strip helps nobody, and the window's
            # 480x400 minimum leaves 225px against the palette's 159 -- so this
            # is a floor under the arithmetic, not a case anyone should reach.
            if show and room is not None and i > 0:
                needed = section.winfo_reqheight() + SECTION_GAP
                if needed <= room:
                    room -= needed
                else:
                    # This section didn't fit, so nothing below it is offered
                    # the leftovers either -- see the docstring.
                    show = False
                    room = 0
            elif show and room is not None:
                room -= section.winfo_reqheight()
            self._show_section(name, show)

    def _show_section(self, name: str, wanted: bool) -> None:
        """Pack or unpack one section, whole. No-op when already in that state.

        Whole section or none of it: half a navigator is worse than no
        navigator, because the half that remains looks like it works.
        """
        if wanted == (name in self._sections_shown):
            return
        section = self._sections[name]
        if wanted:
            # A returning section re-packs at the *bottom*, and that is correct
            # here without a `pack(before=...)`, which is what this first said.
            # Sections stand down monotonically from the end of PANEL_SECTIONS,
            # so a later section is never up while an earlier one is down --
            # which means a section coming back is always the last one showing,
            # and `before` had nothing to point at. A guard in front of
            # something that already decides (§27.4, §28.3, and now this).
            # The claim is pinned by the smoke check on `pack_slaves` order
            # rather than by the guard, because a guard cannot be seen to work.
            #
            # The gap goes *above* each section rather than below, so the last
            # one visible doesn't leave 8px of dead panel under it -- which the
            # arithmetic would then have to charge for.
            gap = 0 if name == PANEL_SECTIONS[0] else SECTION_GAP
            section.pack(side="top", fill="x", pady=(gap, 0))
            self._sections_shown.add(name)
        else:
            section.pack_forget()
            self._sections_shown.discard(name)

    def _update_minimap(self) -> None:
        image = self.controller.frame_image()
        doc = self.controller.doc
        if image is None or doc is None:
            self.minimap.clear()
            return
        self.minimap.show(image, doc[self.controller.index].image_uid,
                          self.canvas.view.visible_source_rect())

    def _select_tool(self, tool_id: str) -> None:
        """Activate a tool -- crop, pencil, eraser, eyedropper -- or 'cursor' to
        put tools away. One entry point for the palette, the shortcuts and the
        Image > Crop menu item alike."""
        if tool_id != CURSOR_TOOL and self.controller.doc is None:
            return  # nothing to work on; leave the palette on Cursor
        # Reaching for another tool settles a float first -- except Move, which
        # is the tool for manipulating one. Committing rather than discarding
        # for the usual reason: an unwanted commit is one Ctrl+Z away.
        if tool_id != "move":
            self.controller.commit_float()
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
        self.status.configure(text=self._tool_status(tool))

    def _tool_status(self, tool) -> str:
        """The status line for an active tool, erase mode included.

        "(erasing)" because the checkbox lives elsewhere on screen and a tool
        quietly doing the opposite of its name is worth one word. Not shown for
        the tools erase mode does not reach -- select, crop and the eyedropper
        edit no pixels -- nor for the Eraser, which would be saying it twice.
        """
        untouched = ("select", "crop", "eyedropper", "eraser")
        erasing = self.erase_mode and tool.id not in untouched
        return f"{tool.label}{' (erasing)' if erasing else ''}: {tool.hint}"

    def end_tool(self) -> None:
        """ToolContext hook: the canvas's Esc, once no gesture is outstanding.

        Really "escape from whatever is current", which is why it checks the
        float first: putting the tool away while a move sat uncommitted would
        commit it (see `_select_tool`), and Esc must never be the key that
        applies something.
        """
        if self.controller.cancel_float():
            return
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

    def _on_erase_toggle(self) -> None:
        """Erase changed: grey the colour out, and say what the tool now does.

        The swatch is disabled rather than left live because while erasing the
        colour is not used by anything, and a control that looks active and
        does nothing is the failure this project keeps meeting from new angles
        (§23.5, and the whole of `_after_grid_change`). The status line matters
        for the same reason: this is a *mode*, so it changes what the tool you
        already have selected will do, with nothing else on screen moving.
        """
        erasing = self.erase_mode
        self._swatch.configure(state="disabled" if erasing else "normal")
        self._colour_label.state(["disabled"] if erasing else ["!disabled"])
        tool = self._active_tool
        if tool is None:
            self.status.configure(
                text="Erase: pencil, fill and shapes now remove pixels"
                if erasing else self._summary())
            return
        self.status.configure(text=self._tool_status(tool))

    # ---- per-frame delay -------------------------------------------------

    def _commit_delay(self) -> None:
        """Apply what's in the box, if it is a number and it differs.

        Guarded three ways, because this fires on every focus-out:

        - garbage or an empty box is ignored rather than treated as zero;
        - a value equal to what the targets already hold is skipped here, so a
          click through the box never touches history. The op declines too, but
          declining still costs a status message saying "nothing to do", which
          would be noise for a box the user merely tabbed past;
        - `_refreshing_delay` stops the refresh below from re-entering this.
        """
        if self._refreshing_delay or self.controller.doc is None:
            return
        try:
            wanted = int(float(self._delay_var.get()))
        except (TypeError, ValueError):
            self._refresh_delay_box()   # put the real value back
            return
        if wanted == self.controller.target_delay_ms:
            return
        self.controller.set_frame_delay(wanted)
        # The op quantises to 10ms and floors at MIN_DURATION_MS, so what landed
        # may not be what was typed. Showing the result rather than the request
        # is the only honest thing to put in the box.
        self._refresh_delay_box()

    def _refresh_delay_box(self) -> None:
        """Point the box and its label at the current targets.

        Empty when the selected frames disagree: a single number would be wrong
        for most of them, and blank is the one display that isn't a lie. The
        label carries the count, so "Frame delay (4 frames)" tells you what
        typing here would do *before* you type it -- which is the whole reason
        the inline path is scoped to the selection rather than to everything.
        """
        self._refreshing_delay = True
        try:
            targets = self.controller.frame_targets
            shared = self.controller.target_delay_ms
            self._delay_var.set("" if shared is None else str(shared))
            suffix = f" ({len(targets)} frames)" if len(targets) > 1 else ""
            self._delay_label.configure(text=f"Frame delay{suffix}")
            self._delay_box.configure(
                state="normal" if self.controller.doc is not None else "disabled")
        finally:
            self._refreshing_delay = False

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

    @property
    def fill_shapes(self) -> bool:
        return bool(self._fill_var.get())

    @property
    def erase_mode(self) -> bool:
        """Whether a mark removes pixels instead of adding them.

        One flag for every painting tool rather than an erase variant of each,
        because "erase" is not a colour and never could be -- painting
        composites *over*, so no colour, however transparent, subtracts alpha.
        It is the other branch of the same operation, and a single toggle says
        that far better than a second bucket and a second rectangle in the
        palette would.
        """
        return bool(self._erase_var.get())

    @property
    def tolerance(self) -> int:
        """How near a colour has to be for the fill bucket to cross it.

        Read live off the spinbox and clamped here rather than mirrored into an
        attribute on every keystroke, because mid-edit the box legitimately holds
        "" or "-" and neither is a tolerance. The brush size predates this and
        does mirror; that one has a `trace_add` swallowing the same garbage, so
        both are safe, but this is the shorter way to be safe.
        """
        try:
            return max(0, min(int(float(self._tolerance_var.get())), 255))
        except (TypeError, ValueError):
            return 0

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
        """One menu for one op group. Kept in `self.op_menus` under its group key.

        Held rather than dropped because a menu you cannot name is a menu no
        test can open: `entrycget` reports whatever was last configured, so
        checking that an entry greys out means running its postcommand first,
        and that means having the menu. `file_menu` has been an attribute for
        the same reason since M3.
        """
        menu = tk.Menu(menubar, tearoff=False)
        # (index, "should this be enabled?"). A predicate rather than an op id
        # because the appended non-registry items are not all "can this op run":
        # Crop to Selection also wants a region, and encoding that as a special
        # case in the refresh would put frontend knowledge in the generic half.
        entries: list[tuple[int, Callable[[], bool]]] = []
        for op in menu_groups().get(group_key, []):
            # "..." signals a dialog; it's a UI convention, so it lives here
            # rather than in the op's label (which feeds "Undo <label>").
            label = op.label + ("..." if op_params(op) else "")
            menu.add_command(label=label, accelerator=op.accel,
                             command=lambda oid=op.id: self._invoke_op(oid))
            entries.append((menu.index("end"),
                            lambda oid=op.id: self.controller.can_run(oid)))
        self.op_menus[group_key] = (menu, entries)
        menu.configure(postcommand=lambda k=group_key: self.refresh_op_menu(k))
        menubar.add_cascade(label=title, menu=menu)
        # Returned so callers can append non-registry items (e.g. gesture-driven
        # Crop) that still share the group's enable/disable refresh.
        return menu, entries

    def refresh_op_menu(self, group_key: str) -> None:
        """Ask each entry's own predicate whether it should be live."""
        menu, entries = self.op_menus[group_key]
        for index, enabled in entries:
            menu.entryconfigure(index, state="normal" if enabled() else "disabled")

    def _refresh_file_menu(self) -> None:
        """Everything here except Open and Import needs a document.

        By *label*, not by index. This used to say `for entry in (2, 3, 5)` with
        a comment naming Save, Save As and Close; inserting Import and Export
        after Open repointed those numbers at Export, a *separator* and Save As.

        I assumed that would fail silently -- wrong three items greyed out, no
        error -- and wrote as much here until a mutation run disproved it: a
        separator has no `-state`, so Tk raises `TclError` the moment the File
        menu opens. Loud, then, not silent. Still worth fixing by label, because
        the loudness was luck: had the insertion landed one entry earlier, all
        three indices would have hit real entries and it *would* have been
        silent. Tk accepts a label wherever it accepts an index, so not being
        able to make the mistake costs nothing either way.
        """
        state = "normal" if self.controller.doc is not None else "disabled"
        for label in ("Export Frames...", "Save", "Save As...", "Close"):
            self.file_menu.entryconfigure(label, state=state)

    def _refresh_edit_menu(self) -> None:
        """Everything in Edit, by label except the two whose labels move.

        Undo and Redo stay indices 0 and 1: their text is rewritten right here,
        so there is no stable label to address them by, and being the first two
        entries is a property of the menu rather than a count anyone maintains.
        Everything else goes by label, which is the lesson `_refresh_file_menu`
        records -- inserting Cut/Copy/Paste is exactly the edit that repointed
        those numbers last time, and it would have done so again.
        """
        c = self.controller
        undo_text = f"Undo {c.undo_label}" if c.undo_label else "Undo"
        redo_text = f"Redo {c.redo_label}" if c.redo_label else "Redo"
        self.edit_menu.entryconfigure(0, label=undo_text, state="normal" if c.can_undo else "disabled")
        self.edit_menu.entryconfigure(1, label=redo_text, state="normal" if c.can_redo else "disabled")
        has_doc = c.doc is not None
        for label, enabled in (
            ("Cut Area", c.can_copy),
            ("Copy Area", c.can_copy),
            ("Paste", c.can_paste),
            ("Select All", has_doc),
            ("Deselect", bool(c.selection) or c.region is not None),
        ):
            self.edit_menu.entryconfigure(
                label, state="normal" if enabled else "disabled")

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
        # The delay box is scoped to the selection, so its value and its
        # "(N frames)" label both change when the selection does.
        self._refresh_delay_box()

    def _on_region_changed(self, region=None, **_) -> None:
        self._show_region(region, self.controller.floating)

    def _show_region(self, region, floating=None) -> None:
        """Point the canvas at the controller's region.

        One place, called from the event and from `_render`, because the canvas
        forgets nothing but a *new document* replaces what it is showing and the
        two have to agree at that moment as well.

        While something is floating the marquee tracks it rather than staying
        where the pixels came from: a move shows a hole at the source, and a
        marquee still drawn round that hole would be pointing at the one place
        the pixels are not.
        """
        if region is None:
            self.canvas.set_region(None)
            return
        dx, dy = (floating.dx, floating.dy) if floating is not None else (0, 0)
        self.canvas.set_region(
            (region.x + dx, region.y + dy, region.width, region.height))

    def _on_playhead_moved(self, index: int = 0, **_) -> None:
        self.timeline.set_index(index)
        self._render()
        self._update_transport()

    def _on_playback_state(self, playing: bool = False, **_) -> None:
        self.play_button.configure(text="Pause" if playing else "Play")

    def _on_title_changed(self, path: Path | None = None, dirty: bool = False,
                          name: str | None = None, **_) -> None:
        self._set_title(path, dirty, name)

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
            self._refresh_view_controls()
            self._refresh_delay_box()
            return
        doc = self.controller.doc
        key = doc[self.controller.index].image_uid if doc is not None else None
        floating = self.controller.floating
        if floating is not None:
            # The uncommitted result: the op run and thrown away. Keyed by the
            # offset so the bitmap cache still works during a drag, and distinct
            # from the frame's own uid so a committed frame is never served
            # these pixels (ARCHITECTURE.md 5, the stale-cache rule).
            image = self.controller.float_preview()
            key = ("float", key, floating.dx, floating.dy)
        self.canvas.show(image, key=key)
        self._show_region(self.controller.region, floating)
        self._update_status()

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
        self._refresh_delay_box()

    def _summary(self) -> str:
        """Derived from state, not from a remembered event, so it can't drift
        out of sync by missing one."""
        doc = self.controller.doc
        if doc is None:
            return "Ready"
        # A float takes the whole line. It has to live *here* rather than being
        # written once when the float changes: every view change refreshes the
        # status from this method, so a message set anywhere else survives until
        # the first zoom and then silently vanishes -- and "nothing has actually
        # happened yet" is the one thing on screen that nothing else says.
        floating = self.controller.floating
        if floating is not None:
            dx, dy = floating.dx, floating.dy
            placed = f"{dx:+d}, {dy:+d}" if (dx or dy) else "not moved yet"
            count = len(self.controller.frame_targets)
            frames = f" on {count} frames" if count > 1 else ""
            return (f"{floating.kind.title()} in progress ({placed}){frames}"
                    f"   |   Enter to drop it, Esc to put it back")
        # The frame's own delay *and* the total. Only the total was here
        # before, which reads as the frame's on a one-frame GIF and is silently
        # a different number on any other.
        delay = self.controller.current_delay_ms
        return (
            f"{doc.size[0]}x{doc.size[1]}   |   "
            f"frame {delay} ms   |   "
            f"total {doc.total_duration_ms / 1000:.2f}s   |   "
            f"{_format_bytes(doc.nbytes_estimate)}   |   "
            # Fit reports its percentage too: "Fit" alone leaves you unable to
            # tell a 40% view from a 400% one, which matters most on exactly the
            # small pixel-art GIFs that get blown up to fill the window.
            f"{self.canvas.view.label}"
        )

    def _set_title(self, path: Path | None, dirty: bool,
                   name: str | None = None) -> None:
        """`name` covers the pathless case -- an imported folder still has a
        name worth showing, and falling straight back to the bare app title
        throws away the only context the user has about what is loaded."""
        label = path.name if path is not None else name
        if label is None:
            self.root.title(APP_NAME)
            return
        mark = "*" if dirty else ""
        self.root.title(f"{mark}{label} - {APP_NAME}")

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
