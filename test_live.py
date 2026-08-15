"""
test_live.py — Kitsune Prompt 3 decode-from-frame plumbing (camera-free).

The live webcam loop (live_decode.py) decodes each cv2 frame through the shared
decode_frame(). These tests prove that per-frame path WITHOUT a camera, by
feeding a SAVED page image — loaded as a BGR numpy array, exactly as cv2 delivers
a webcam frame — through the same decode_frame() the live loop calls on every
frame. This is the headless half of the Prompt 3 gate; the optics half is manual.
"""

import os
import tempfile

from cryptography.exceptions import InvalidTag

from compose_page import compose_page
from live_decode import decode_frame, frame_from_image_file

KEY = bytes(range(32))              # fixed key so tests never touch key.bin
OTHER_KEY = bytes(range(32, 64))


def test_decode_frame_roundtrips_saved_page():
    """A saved page, loaded as a BGR frame, decodes to the exact secret through
    the same decode_frame() the webcam loop uses."""
    secret = "rendezvous 0400 - pier 7"
    with tempfile.TemporaryDirectory() as d:
        page = os.path.join(d, "page.png")
        compose_page(secret, page, key=KEY)
        frame = frame_from_image_file(page)          # BGR numpy, like cv2 gives
        assert frame is not None, "failed to load the saved page as a frame"
        assert frame.ndim == 3, "a webcam frame is a 3-channel BGR array"
        assert decode_frame(frame, key=KEY) == secret


def test_decode_frame_multi_qr_page():
    """A multi-QR (~1KB) page still reassembles from one BGR frame."""
    secret = "K" * 1024
    with tempfile.TemporaryDirectory() as d:
        page = os.path.join(d, "page.png")
        compose_page(secret, page, key=KEY)
        frame = frame_from_image_file(page)
        assert decode_frame(frame, key=KEY) == secret


def test_decode_frame_downscaled_still_roundtrips():
    """The live loop feeds ZBar a downscaled grayscale copy (max_dim) for speed;
    a demo-sized secret must still round-trip through that path."""
    secret = "meet at dawn - 0400 pier 7"
    with tempfile.TemporaryDirectory() as d:
        page = os.path.join(d, "page.png")
        compose_page(secret, page, key=KEY)
        frame = frame_from_image_file(page)
        assert decode_frame(frame, key=KEY, max_dim=900) == secret


def test_decode_frame_wrong_key_raises():
    """A frame that decodes+reassembles but fails GCM auth must raise InvalidTag,
    never return garbage — the live loop shows 'INVALID', never a fake secret."""
    with tempfile.TemporaryDirectory() as d:
        page = os.path.join(d, "page.png")
        compose_page("classified", page, key=KEY)
        frame = frame_from_image_file(page)
        try:
            decode_frame(frame, key=OTHER_KEY)
        except InvalidTag:
            return
        raise AssertionError("wrong key decoded a frame without an auth failure")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
