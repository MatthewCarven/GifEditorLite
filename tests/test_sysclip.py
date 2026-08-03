"""The system clipboard, minus the system.

Everything here runs without a clipboard, a display, or Windows -- which is the
point of how `giflite/app/sysclip.py` is split. The wire format and the decision
rules are pure functions and are tested properly; `put_image`, which is the only
thing that actually touches the OS, is tested exactly as far as it can be
(it refuses off Windows) and is otherwise Matthew's to verify.

The orientation and channel-order checks earn their keep: get either wrong and
the clipboard still accepts the handle, no error is raised anywhere, and the
picture simply arrives upside down or blue in whatever application you paste
into. Nothing about the failure points at this file.
"""

from __future__ import annotations

import io
import struct
import sys

import pytest
from PIL import Image

from giflite.app import sysclip


def sample() -> Image.Image:
    """Deliberately not square and not symmetric: a square image hides a
    transposition and a symmetric one hides a flip."""
    image = Image.new("RGBA", (6, 4), (10, 20, 30, 255))
    image.putpixel((0, 0), (255, 0, 0, 255))    # top-left    red
    image.putpixel((5, 3), (0, 255, 0, 255))    # bottom-right green
    image.putpixel((5, 0), (0, 0, 255, 128))    # top-right   half-alpha blue
    return image


class TestTheDibFormat:
    def test_it_round_trips_through_its_own_inverse(self):
        image = sample()
        back = sysclip.image_from_dib(sysclip.dib_bytes(image))
        assert back.tobytes() == image.tobytes()

    def test_pillow_reads_it_as_a_bitmap(self):
        """The check that matters, because it is *independent*.

        A round trip through `image_from_dib` proves only that two functions in
        this module agree -- and a swapped channel order or a flipped row order
        implemented consistently in both would agree perfectly while producing
        a wrong picture everywhere else. So: bolt on the 14-byte
        BITMAPFILEHEADER that turns a DIB into a .bmp and hand it to Pillow's
        BMP decoder, which was written by somebody else.
        """
        image = sample()
        dib = sysclip.dib_bytes(image)
        header = b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, 14 + 40)
        got = Image.open(io.BytesIO(header + dib)).convert("RGB")
        assert got.size == (6, 4)
        assert got.getpixel((0, 0)) == (255, 0, 0), "rows are upside down"
        assert got.getpixel((5, 3)) == (0, 255, 0), "rows are upside down"
        assert got.getpixel((3, 1)) == (10, 20, 30), "channels are swapped"

    def test_the_header_says_what_cf_dib_requires(self):
        dib = sysclip.dib_bytes(sample())
        size, width, height, planes, bpp, compression = struct.unpack(
            "<IiiHHI", dib[:20])
        assert size == 40, "CF_DIB takes a BITMAPINFOHEADER and nothing else"
        assert (width, height) == (6, 4)
        assert height > 0, "positive height means bottom-up, which is the safe one"
        assert (planes, bpp, compression) == (1, 32, 0)

    def test_it_is_exactly_header_plus_pixels(self):
        """No BITMAPFILEHEADER. That belongs to a .bmp on disk; putting one on
        the clipboard gives every reader 14 bytes of garbage where the header
        should be."""
        assert len(sysclip.dib_bytes(sample())) == 40 + 6 * 4 * 4

    def test_alpha_survives_the_encoding(self):
        back = sysclip.image_from_dib(sysclip.dib_bytes(sample()))
        assert back.getpixel((5, 0)) == (0, 0, 255, 128)

    def test_it_takes_an_image_that_is_not_already_rgba(self):
        flat = Image.new("P", (4, 2))
        dib = sysclip.dib_bytes(flat)
        assert len(dib) == 40 + 4 * 2 * 4

    def test_the_decoder_refuses_what_it_does_not_understand(self):
        with pytest.raises(ValueError):
            sysclip.image_from_dib(b"\x00" * 8)
        with pytest.raises(ValueError):
            # A 24bpp DIB is perfectly legal and this decoder does not read it;
            # saying so is better than returning a picture made of noise.
            sysclip.image_from_dib(
                struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 24, 0, 0, 0, 0, 0, 0)
                + b"\x00" * 24)


class TestThePngPayload:
    def test_it_is_a_png(self):
        assert sysclip.png_bytes(sample())[:8] == b"\x89PNG\r\n\x1a\n"

    def test_it_keeps_the_alpha_the_dib_cannot_promise(self):
        """The whole reason both formats go on the clipboard: 32bpp BI_RGB has
        somewhere to put alpha but no promise anyone reads it, while a PNG's
        alpha is not optional."""
        back = Image.open(io.BytesIO(sysclip.png_bytes(sample())))
        assert back.mode == "RGBA"
        assert back.getpixel((5, 0)) == (0, 0, 255, 128)


class TestInterpretingWhatWasOnTheClipboard:
    def test_an_image_comes_back_as_rgba(self):
        image, why = sysclip.interpret_grab(Image.new("RGB", (3, 3)))
        assert why == ""
        assert image.mode == "RGBA"

    def test_an_empty_clipboard_says_so(self):
        assert sysclip.interpret_grab(None) == (None, "Nothing on the clipboard")

    def test_a_list_of_files_is_not_an_image(self):
        """Windows puts a *file list* on the clipboard when you copy a file in
        Explorer, and a caller assuming "image or None" gets an AttributeError
        for its trouble. It gets its own message because copying a GIF in
        Explorer and pasting it as one frame would be the wrong answer to a
        perfectly reasonable action."""
        image, why = sysclip.interpret_grab(["C:/x.png", "C:/y.png"])
        assert image is None
        assert "file" in why.lower()
        assert why != "Nothing on the clipboard"

    def test_an_empty_list_is_just_empty(self):
        assert sysclip.interpret_grab([]) == (None, "Nothing on the clipboard")


class TestGrabbing:
    def test_it_hands_back_what_the_grabber_found(self):
        image, why = sysclip.grab_image(lambda: Image.new("RGBA", (2, 2)))
        assert why == "" and image.size == (2, 2)

    def test_a_grabber_that_raises_becomes_a_message(self):
        """Pillow raises for a missing xclip on Linux, and the clipboard being
        held open by another process is ordinary on Windows. Neither is this
        program's fault and neither should reach a traceback."""
        def angry():
            raise OSError("clipboard busy")

        image, why = sysclip.grab_image(angry)
        assert image is None
        assert "clipboard busy" in why


class TestTheSizeComplaint:
    def test_matching_sizes_have_nothing_to_say(self):
        assert sysclip.size_complaint((40, 20), (40, 20)) == ""

    def test_it_names_both_sizes(self):
        """Matthew's call, and the reason is in the text: "wrong size" tells you
        that something is wrong, and these numbers tell you what to do."""
        message = sysclip.size_complaint((60, 60), (40, 20))
        assert "60x60" in message and "40x20" in message


class TestWritingIsWindowsOnly:
    def test_can_copy_agrees_with_the_platform(self):
        assert sysclip.can_copy() == (sys.platform == "win32")

    @pytest.mark.skipif(sys.platform == "win32",
                        reason="on Windows this actually writes the clipboard")
    def test_put_image_refuses_rather_than_failing_obscurely(self):
        with pytest.raises(sysclip.ClipboardError) as caught:
            sysclip.put_image(sample())
        assert sys.platform in str(caught.value)


class TestTheHeaderFieldsNobodyChecks:
    def test_it_reports_the_image_size_even_though_bi_rgb_may_omit_it(self):
        """`biSizeImage` is allowed to be 0 for an uncompressed bitmap, so
        leaving it out is legal and nothing would ever raise. It is filled in
        anyway because "legal" is not "universally handled" -- some consumers
        use it to find the pixel data rather than computing it -- and a claim
        made on purpose deserves a test rather than a comment."""
        dib = sysclip.dib_bytes(sample())
        (size_image,) = struct.unpack("<I", dib[20:24])
        assert size_image == 6 * 4 * 4

    def test_the_complaint_notices_a_height_difference_on_its_own(self):
        """The obvious mismatch has both dimensions wrong, which is exactly the
        case that cannot tell a full comparison from a sloppy one."""
        assert sysclip.size_complaint((40, 30), (40, 20)) != ""
        assert sysclip.size_complaint((30, 20), (40, 20)) != ""
