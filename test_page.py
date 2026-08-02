"""
test_page.py — Kitsune page layer  (Prompt 2, Fork B: QR only)

The page layer (compose_page.py) sits ON TOP of qr_crypto without touching its
crypto or QR format. It composes the segmented QR PNGs onto ONE printable page
image, and decodes the SECRET back out of that single page image (finding every
QR on the page, not one-PNG-per-file like qr_crypto does).

These are the clean, non-degraded round-trip tests: render -> SAVE -> RE-OPEN ->
decode. The photograph-simulation gate lives in gate_page.py.
"""

import os
import tempfile

from cryptography.exceptions import InvalidTag

from compose_page import compose_page, decode_page

KEY = bytes(range(32))  # fixed 32-byte AES-256 key so tests don't touch key.bin
OTHER_KEY = bytes(range(32, 64))


def _roundtrip(secret: str) -> str:
    """compose_page -> save PNG -> re-open from disk -> decode_page. Returns plaintext."""
    with tempfile.TemporaryDirectory() as d:
        page_path = os.path.join(d, "page.png")
        compose_page(secret, page_path, key=KEY)
        assert os.path.exists(page_path), "compose_page did not write the page PNG"
        return decode_page(page_path, key=KEY)


def test_clean_roundtrip_short():
    """A short secret survives compose -> save -> reopen -> decode (single QR)."""
    secret = "meet at dawn"
    assert _roundtrip(secret) == secret


def test_clean_roundtrip_unicode_short():
    """Non-ASCII short secret survives the page round-trip (bytes stay exact)."""
    secret = "会う、夜明けに 🦊"
    assert _roundtrip(secret) == secret


def test_clean_roundtrip_multi_qr():
    """A ~1KB secret spans several QRs; decode_page must find ALL of them on the
    one page image and reassemble in order."""
    secret = "K" * 1024                      # ~1KB -> multiple QRs on the page
    assert _roundtrip(secret) == secret


def test_clean_roundtrip_256B():
    """A ~256B secret (2 QRs) survives the page round-trip."""
    secret = "x" * 256
    assert _roundtrip(secret) == secret


def test_wrong_key_fails():
    """Decoding a page with the wrong key must fail authentication, not return
    garbage — the crypto boundary still holds through the page layer."""
    with tempfile.TemporaryDirectory() as d:
        page_path = os.path.join(d, "page.png")
        compose_page("classified", page_path, key=KEY)
        try:
            decode_page(page_path, key=OTHER_KEY)
        except InvalidTag:
            return
        raise AssertionError("wrong key decoded the page without an auth failure")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
