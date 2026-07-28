#!/usr/bin/env python3
"""Generate a countdown GIF in MM:SS format, one frame per second.

Standalone: depends only on Pillow. Does not import giflite.

Examples
--------
    python tools/countdown_gif.py 5:00
    python tools/countdown_gif.py 90 -o egg_timer.gif --size 320x160
    python tools/countdown_gif.py 10:00 --fg "#ff3355" --bg black --step 5
    python tools/countdown_gif.py 1:30 --font C:/Windows/Fonts/consolab.ttf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

# ---------------------------------------------------------------- fonts

# Tried in order when --font is not given. Monospace first so digits do not
# jitter; the fallback bitmap font is ugly but always present.
FONT_CANDIDATES = (
    # Windows
    "C:/Windows/Fonts/consolab.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/courbd.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    # macOS
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
)


def find_font_path(explicit: str | None = None) -> str | None:
    """Return a usable TrueType path, or None to signal the bitmap fallback."""
    if explicit:
        if not Path(explicit).is_file():
            raise SystemExit(f"font not found: {explicit}")
        return explicit
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def fit_font(font_path: str | None, text: str, box: tuple[int, int]):
    """Largest font size whose rendered `text` fits inside `box` (w, h)."""
    max_w, max_h = box
    if font_path is None:
        return ImageFont.load_default()

    def measure(size: int) -> tuple[int, int]:
        f = ImageFont.truetype(font_path, size)
        left, top, right, bottom = f.getbbox(text)
        return right - left, bottom - top

    lo, hi, best = 4, 1024, 4
    while lo <= hi:
        mid = (lo + hi) // 2
        w, h = measure(mid)
        if w <= max_w and h <= max_h:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return ImageFont.truetype(font_path, best)


# ---------------------------------------------------------------- timing


def parse_duration(text: str) -> int:
    """Accept '5:00', '05:00', '1:02:03', or a bare seconds count."""
    text = text.strip()
    parts = text.split(":")
    if len(parts) > 3:
        raise argparse.ArgumentTypeError(f"cannot parse duration: {text!r}")
    try:
        values = [int(p) for p in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(f"cannot parse duration: {text!r}") from None
    if any(v < 0 for v in values):
        raise argparse.ArgumentTypeError("duration parts must not be negative")

    total = 0
    for value in values:
        total = total * 60 + value
    if total <= 0:
        raise argparse.ArgumentTypeError("duration must be greater than zero")
    return total


def format_time(seconds: int, force_hours: bool = False) -> str:
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours or force_hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_size(text: str) -> tuple[int, int]:
    sep = "x" if "x" in text.lower() else ","
    try:
        w, h = (int(p) for p in text.lower().split(sep))
    except ValueError:
        raise argparse.ArgumentTypeError(f"cannot parse size: {text!r}") from None
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("size must be positive")
    return w, h


# ---------------------------------------------------------------- render


def build_frames(
    total_seconds: int,
    *,
    size: tuple[int, int] = (400, 200),
    fg: str = "#ffffff",
    bg: str = "#101014",
    font_path: str | None = None,
    step: int = 1,
    include_zero: bool = True,
    pad: float = 0.12,
) -> list[Image.Image]:
    """Render one RGB frame per tick, counting down to zero."""
    width, height = size
    fg_rgb = ImageColor.getrgb(fg)
    bg_rgb = ImageColor.getrgb(bg)

    # Size the font once, against the widest label the countdown will show, so
    # every frame uses identical metrics.
    force_hours = total_seconds >= 3600
    widest = format_time(total_seconds, force_hours)
    inset = (int(width * (1 - pad * 2)), int(height * (1 - pad * 2)))
    font = fit_font(font_path, widest, inset)

    frames: list[Image.Image] = []
    remaining = total_seconds
    while remaining > 0:
        frames.append(
            _draw_frame(format_time(remaining, force_hours), size, fg_rgb, bg_rgb, font)
        )
        remaining -= step
    if include_zero:
        frames.append(
            _draw_frame(format_time(0, force_hours), size, fg_rgb, bg_rgb, font)
        )
    return frames


def _draw_frame(label, size, fg_rgb, bg_rgb, font) -> Image.Image:
    img = Image.new("RGB", size, bg_rgb)
    draw = ImageDraw.Draw(img)
    # anchor="mm" centres on the font's ascender/descender box rather than the
    # glyph ink, which keeps the text from drifting as digits change.
    draw.text((size[0] / 2, size[1] / 2), label, font=font, fill=fg_rgb, anchor="mm")
    return img


PALETTE_STEPS = 16


def build_palette_image(bg: str, fg: str, steps: int = PALETTE_STEPS) -> Image.Image:
    """A fixed bg->fg ramp, wide enough to carry the text's anti-aliased edges.

    Every frame is quantised against this one palette. That matters more than it
    looks: per-frame ADAPTIVE palettes make Pillow emit a *local* colour table
    per frame, and decoders that composite in palette-index space then read the
    untouched pixels through the wrong table -- which is what produced the
    white-background, outlined-digit frames.
    """
    bg_rgb = ImageColor.getrgb(bg)
    fg_rgb = ImageColor.getrgb(fg)
    entries: list[int] = []
    for i in range(steps):
        t = i / (steps - 1)
        entries.extend(round(b + (f - b) * t) for b, f in zip(bg_rgb, fg_rgb))
    entries.extend([0] * (768 - len(entries)))

    pal = Image.new("P", (1, 1))
    pal.putpalette(entries)
    return pal


def save_gif(
    frames: list[Image.Image],
    path: str | Path,
    *,
    frame_ms: int = 1000,
    hold_ms: int | None = None,
    loop: int = 0,
    bg: str = "#101014",
    fg: str = "#ffffff",
) -> Path:
    """Write frames as a palettised GIF. `hold_ms` overrides the last frame."""
    if not frames:
        raise ValueError("no frames to save")

    durations = [frame_ms] * len(frames)
    if hold_ms is not None:
        durations[-1] = hold_ms

    pal = build_palette_image(bg, fg)
    palette_frames = [
        f.convert("RGB").quantize(palette=pal, dither=Image.Dither.NONE) for f in frames
    ]

    path = Path(path)
    palette_frames[0].save(
        path,
        save_all=True,
        append_images=palette_frames[1:],
        duration=durations,
        loop=loop,
        # Full, self-contained frames. optimize=True would ship only the changed
        # rectangle, which is smaller but leans on the decoder to composite
        # correctly -- and several (Discord, some editors) do not. A flat
        # background LZW-compresses well enough that the trade is cheap.
        disposal=2,
        optimize=False,
        # Declare the palette as the *global* colour table. Without this Pillow
        # repeats it as a local table on every frame, and a 16-entry table keeps
        # the LZW minimum code size at 4 bits rather than 8.
        palette=bytes(pal.getpalette())[: PALETTE_STEPS * 3],
    )
    return path


# ---------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Generate a MM:SS countdown GIF, one frame per second.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "duration",
        type=parse_duration,
        help="countdown length: MM:SS, H:MM:SS, or a plain seconds count",
    )
    p.add_argument("-o", "--output", default="countdown.gif", help="output .gif path")
    p.add_argument("--size", type=parse_size, default=(400, 200), help="WIDTHxHEIGHT")
    p.add_argument("--fg", default="#ffffff", help="text colour")
    p.add_argument("--bg", default="#101014", help="background colour")
    p.add_argument("--font", default=None, help="path to a .ttf/.otf (default: auto)")
    p.add_argument("--step", type=int, default=1, help="seconds between frames")
    p.add_argument(
        "--frame-ms",
        type=int,
        default=None,
        help="ms per frame (default: step * 1000, i.e. real time)",
    )
    p.add_argument(
        "--hold-ms",
        type=int,
        default=None,
        help="ms to hold the final frame (default: same as other frames)",
    )
    p.add_argument("--loop", type=int, default=0, help="0 = loop forever, 1 = play once")
    p.add_argument(
        "--no-zero", action="store_true", help="stop at 00:01 instead of 00:00"
    )
    args = p.parse_args(argv)

    if args.step < 1:
        p.error("--step must be at least 1")

    frame_ms = args.frame_ms if args.frame_ms is not None else args.step * 1000
    if frame_ms < 10:
        p.error("--frame-ms below 10 is unreliable in most GIF players")

    font_path = find_font_path(args.font)
    if font_path is None:
        print(
            "warning: no TrueType font found; falling back to a small bitmap font. "
            "Pass --font to pick one.",
            file=sys.stderr,
        )

    frames = build_frames(
        args.duration,
        size=args.size,
        fg=args.fg,
        bg=args.bg,
        font_path=font_path,
        step=args.step,
        include_zero=not args.no_zero,
    )
    out = save_gif(
        frames,
        args.output,
        frame_ms=frame_ms,
        hold_ms=args.hold_ms,
        loop=args.loop,
        bg=args.bg,
        fg=args.fg,
    )
    kb = out.stat().st_size / 1024
    print(
        f"{out}  {len(frames)} frames  {args.size[0]}x{args.size[1]}  "
        f"{frame_ms} ms/frame  {kb:.1f} KB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
