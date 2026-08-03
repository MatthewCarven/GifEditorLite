"""The operating system's clipboard, as far as images are concerned.

Lives in `app/` rather than in `ui/tk/` on purpose. It is platform I/O, not
toolkit I/O: nothing here imports tkinter, so a second frontend gets clipboard
support without reimplementing it (ARCHITECTURE.md 32). That is the opposite
call from the one `ui/tk/tools.py` makes, and for the opposite reason -- a Tool
is about mouse gestures, which every toolkit spells differently, while
"put these pixels on the Windows clipboard" is the same three Win32 calls no
matter what drew the window.

**Reading is easy and writing is not**, which is the whole shape of this file.
Pillow ships `ImageGrab.grabclipboard()` and it works on Windows, macOS and
X11/Wayland with a helper installed. Pillow ships nothing for the other
direction, so writing is a `ctypes` shim onto `user32`/`kernel32` and is
Windows-only here. `can_copy()` says so honestly rather than failing at the
moment someone presses the key.

**Everything that can be tested without a clipboard is a pure function.** The
DIB encoder, its inverse and the decision rules all take arguments and return
values; the part that cannot be tested in CI -- open, empty, allocate, set,
close -- is one small function with no branching worth speaking of. That split
is deliberate: the untestable code should be the code with nothing in it.
"""

from __future__ import annotations

import io
import struct
import sys
from typing import Callable

from PIL import Image, ImageGrab

# Clipboard format numbers from WinUser.h. Named rather than inlined because a
# wrong integer here fails silently -- Windows would accept the handle and no
# application would ever ask for that format.
CF_DIB = 8
CF_DIBV5 = 17

# BITMAPINFOHEADER: 40 bytes, and the only header CF_DIB is allowed to carry.
BITMAPINFOHEADER_SIZE = 40
BI_RGB = 0

GMEM_MOVEABLE = 0x0002


class ClipboardError(RuntimeError):
    """The OS refused. Carries what it refused at, for the status line."""


# ---- pure: the DIB wire format ------------------------------------------


def dib_bytes(image: Image.Image) -> bytes:
    """Encode an image as a CF_DIB payload: 40-byte header, then BGRA rows.

    Three details, each of which is the kind of thing that silently produces a
    picture that is upside down, blue, or both:

    * **Rows run bottom-up.** A DIB with a positive height starts at the
      *bottom* row. (A negative height means top-down and most things accept
      it, but not everything does, so this writes the boring one.)
    * **Channel order is BGRA**, not RGBA.
    * **32bpp BI_RGB carries the alpha byte** but the format does not promise
      anyone will read it. That is why `png_bytes` goes on the clipboard too:
      Windows apps that understand transparency look for the PNG format first,
      and the DIB is the fallback for everything else.
    """
    rgba = image.convert("RGBA")
    width, height = rgba.size
    header = struct.pack(
        "<IiiHHIIiiII",
        BITMAPINFOHEADER_SIZE,
        width,
        height,          # positive: bottom-up
        1,               # planes
        32,              # bits per pixel
        BI_RGB,
        width * height * 4,
        0, 0, 0, 0,      # pixels-per-metre and palette counts: all irrelevant here
    )
    # `raw` with a mode of "BGRA" does the channel swap in C; the row reversal
    # is ours, because Pillow has no bottom-up writer.
    body = rgba.tobytes("raw", "BGRA")
    stride = width * 4
    rows = [body[i * stride:(i + 1) * stride] for i in range(height)]
    return header + b"".join(reversed(rows))


def image_from_dib(data: bytes) -> Image.Image:
    """Decode a CF_DIB payload. The inverse of `dib_bytes`, and its test.

    Deliberately narrow: it understands the 32bpp BI_RGB bitmaps this module
    writes, which is enough to prove the encoder round-trips. Reading the
    clipboard in anger goes through Pillow (`grab_image`), which handles the
    whole zoo of formats other applications actually produce.
    """
    if len(data) < BITMAPINFOHEADER_SIZE:
        raise ValueError("not a DIB: too short for a BITMAPINFOHEADER")
    size, width, height, _planes, bpp, compression = struct.unpack(
        "<IiiHHI", data[:20])
    if size != BITMAPINFOHEADER_SIZE or bpp != 32 or compression != BI_RGB:
        raise ValueError(
            f"unsupported DIB: header={size} bpp={bpp} compression={compression}")
    top_down = height < 0
    height = abs(height)
    stride = width * 4
    body = data[size:size + stride * height]
    rows = [body[i * stride:(i + 1) * stride] for i in range(height)]
    if not top_down:
        rows.reverse()
    return Image.frombytes("RGBA", (width, height), b"".join(rows), "raw", "BGRA")


def png_bytes(image: Image.Image) -> bytes:
    """The same picture as a PNG, for the clipboard format that keeps alpha."""
    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, format="PNG")
    return buffer.getvalue()


# ---- pure: what to do with whatever was on the clipboard -----------------


def interpret_grab(grabbed: object) -> tuple[Image.Image | None, str]:
    """Turn `ImageGrab.grabclipboard()`'s three return shapes into two.

    It returns an `Image`, or a *list of file paths* (which is what Windows
    puts there when you copy a file in Explorer, and what catches everyone
    out), or None. A caller that assumes "image or nothing" gets an
    AttributeError on the good day and a confusing no-op on the bad one.

    The list case gets its own message rather than being folded into "nothing
    on the clipboard", because those two situations want completely different
    things from the user, and a GIF copied in Explorer is a *file* -- opening
    it as one frame would be the wrong answer to a reasonable action.
    """
    if isinstance(grabbed, Image.Image):
        return grabbed.convert("RGBA"), ""
    if isinstance(grabbed, list):
        if not grabbed:
            return None, "Nothing on the clipboard"
        return None, ("That's a file on the clipboard, not an image "
                      "-- use File > Open or Import Frames")
    return None, "Nothing on the clipboard"


def size_complaint(clip_size: tuple[int, int],
                   canvas_size: tuple[int, int]) -> str:
    """The refusal text, or "" when the sizes agree.

    Matthew's call: refuse and name both sizes. Nothing happens, nothing lands
    on the undo stack, and the numbers are what tell you what to do about it --
    which a bare "wrong size" would not.
    """
    if clip_size == canvas_size:
        return ""
    cw, ch = clip_size
    ww, wh = canvas_size
    return f"Clipboard image is {cw}x{ch}, canvas is {ww}x{wh}"


# ---- impure: the actual clipboard ----------------------------------------


def can_copy() -> bool:
    """Whether *writing* an image to the system clipboard works on this box.

    Only Windows so far. Said out loud so the frontend can grey the menu item
    instead of offering something that fails when pressed -- an item that does
    nothing is indistinguishable from a broken one.
    """
    return sys.platform == "win32"


def grab_image(grab: Callable[[], object] | None = None) -> tuple[Image.Image | None, str]:
    """Read an image off the system clipboard. (image, "") or (None, why).

    `grab` is injectable so the decision rules above can be tested without a
    clipboard, a display, or an operating system that has either.
    """
    grabber = grab or ImageGrab.grabclipboard
    try:
        grabbed = grabber()
    except Exception as exc:  # noqa: BLE001 -- platform-specific and varied
        # Pillow raises for a missing xclip/wl-paste on Linux, and OSError for
        # a clipboard another process is holding open on Windows. Neither is
        # this program's fault and neither should reach a traceback.
        return None, f"Could not read the clipboard: {exc}"
    return interpret_grab(grabbed)


def put_image(image: Image.Image) -> None:
    """Put an image on the Windows clipboard as PNG *and* CF_DIB.

    Raises ClipboardError on any refusal. Both formats, because they serve
    different readers: modern applications ask for "PNG" and get the alpha,
    everything else takes the DIB and composites it however it likes.

    **This function is the one thing here that CI cannot run.** It is written
    to be boring for that reason: no branching on the data, no cleverness, one
    ordered sequence of Win32 calls with the close in a `finally`. Everything
    with a decision in it lives above, in a pure function with tests.
    """
    if not can_copy():
        raise ClipboardError(
            f"Copying an image to the clipboard isn't supported on {sys.platform}")

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Declared rather than left to ctypes' defaults: a handle truncated to 32
    # bits on 64-bit Windows is the classic way this fails, and it fails by
    # corrupting the clipboard rather than by raising.
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]

    def moveable(payload: bytes):
        """Copy bytes into a GMEM_MOVEABLE block the clipboard will own."""
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not handle:
            raise ClipboardError("GlobalAlloc failed")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            raise ClipboardError("GlobalLock failed")
        try:
            ctypes.memmove(pointer, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(handle)
        return handle

    png_format = user32.RegisterClipboardFormatW("PNG")
    # The DIB the clipboard wants has no BITMAPFILEHEADER -- that belongs to a
    # .bmp on disk, not to a clipboard handle.
    payloads = [(CF_DIB, dib_bytes(image))]
    if png_format:
        payloads.insert(0, (png_format, png_bytes(image)))

    if not user32.OpenClipboard(None):
        raise ClipboardError("Another application is holding the clipboard open")
    try:
        if not user32.EmptyClipboard():
            raise ClipboardError("EmptyClipboard failed")
        for fmt, payload in payloads:
            handle = moveable(payload)
            if not user32.SetClipboardData(fmt, handle):
                # Ownership only transfers on success, so on failure the block
                # is still ours and leaks if we don't free it.
                kernel32.GlobalFree(handle)
                raise ClipboardError(f"SetClipboardData failed for format {fmt}")
    finally:
        user32.CloseClipboard()
