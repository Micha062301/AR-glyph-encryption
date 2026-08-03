"""
live_decode.py — Kitsune live webcam decode  (Prompt 3 of 4, Fork B: QR only)

Points a webcam at a printed/displayed Kitsune page and recovers the plaintext
with a CONTINUOUS RETRY LOOP: every frame is a fresh decode attempt, so out of
the many frames per second, one clean frame wins even when most are blurred,
glared, or mid-focus. This is the whole point — the retry loop absorbs the
optical noise that a single capture would lose to.

Decode path (unchanged crypto/QR): each cv2 frame (a BGR numpy array) ->
decode_frame() converts it to a grayscale PIL image and calls the SAME
compose_page.decode_page_image() the Prompt 2 gate proved. qr_crypto.py and
compose_page.py are NOT modified. GLM-OCR is NOT in this pipeline.

Two ways to exercise it:
  * live:        python live_decode.py            (needs a physical camera)
  * headless:    python live_decode.py --selfcheck (no camera — proves the
                 decode-from-frame plumbing on a SAVED page image first)

Security: a frame whose QRs reassemble but fail GCM authentication raises
InvalidTag and is shown as "INVALID - auth failed" while the loop KEEPS GOING.
A bad/garbled/wrong-key decode is never frozen or shown as if it were real.

The page must be composed with the SAME key.bin used here (same machine / same
key). key.bin is gitignored and never committed.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

import cv2
import numpy as np
from PIL import Image
from cryptography.exceptions import InvalidTag

from compose_page import compose_page, decode_page_image

FONT = cv2.FONT_HERSHEY_SIMPLEX
# cv2 colors are BGR. (Hershey fonts are ASCII-only, hence "OK" not a ✓ glyph.)
AMBER = (0, 191, 255)
RED = (0, 0, 255)
GREEN = (0, 220, 0)
WHITE = (255, 255, 255)
GREY = (200, 200, 200)

SELFCHECK_KEY = bytes(range(32))   # fixed key: selfcheck never touches key.bin


# ── the per-frame decode: BGR numpy frame -> secret (shared by loop + selfcheck)
def decode_frame(frame, *, key: bytes | None = None,
                 key_path: str | os.PathLike | None = None) -> str:
    """Decode one webcam frame (BGR numpy array) to the plaintext secret.

    Converts the frame to a grayscale PIL image (no cv2 needed here) and runs the
    exact compose_page.decode_page_image() path. Raises ValueError if no complete
    set of QRs is readable yet, or cryptography's InvalidTag if the reassembled
    blob fails authentication (wrong key / garbled) — callers must treat that as
    "not decoded", never as a secret.
    """
    if frame is None:
        raise ValueError("empty frame")
    arr = np.asarray(frame)
    if arr.ndim == 2:                                  # already single-channel
        pil = Image.fromarray(arr, "L")
    elif arr.ndim == 3 and arr.shape[2] == 4:          # BGRA -> RGB -> L
        pil = Image.fromarray(np.ascontiguousarray(arr[:, :, [2, 1, 0]]),
                              "RGB").convert("L")
    elif arr.ndim == 3 and arr.shape[2] == 3:          # BGR (cv2 default) -> RGB -> L
        pil = Image.fromarray(np.ascontiguousarray(arr[:, :, ::-1]),
                              "RGB").convert("L")
    else:
        raise ValueError(f"unexpected frame shape {arr.shape}")
    return decode_page_image(pil, key=key, key_path=key_path)


def frame_from_image_file(path: str | os.PathLike):
    """Load a saved image as a BGR frame, exactly as cv2 delivers a webcam frame.

    Used by the headless selfcheck/tests to feed decode_frame() without a camera.
    """
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if frame is None:
        raise FileNotFoundError(f"could not read image: {path}")
    return frame


# ── camera handling ───────────────────────────────────────────────────────────
def open_camera(preferred: int | None = None):
    """Open the first working webcam. Tries `preferred`, else indices 0,1,2.

    gs:scanner.py hardcodes index 0, which bites on multi-camera Macs — so we
    probe a few and confirm a frame actually reads (isOpened() alone can lie).
    Returns (cap, index) or (None, None).
    """
    indices = [preferred] if preferred is not None else [0, 1, 2]
    for idx in indices:
        cap = cv2.VideoCapture(idx)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                return cap, idx
        cap.release()
    return None, None


# ── on-screen overlays ─────────────────────────────────────────────────────────
def _wrap(text: str, width: int = 44) -> list[str]:
    """Wrap plaintext to `width` chars/line, preserving explicit newlines."""
    lines: list[str] = []
    for para in text.split("\n"):
        if para == "":
            lines.append("")
            continue
        while len(para) > width:
            lines.append(para[:width])
            para = para[width:]
        lines.append(para)
    return lines


def _draw_status(img, text: str, color) -> None:
    """Top status bar for the live (not-yet-decoded) state."""
    h, w = img.shape[:2]
    cv2.rectangle(img, (0, 0), (w, 42), (0, 0, 0), -1)
    cv2.putText(img, text, (10, 29), FONT, 0.7, color, 2, cv2.LINE_AA)
    cv2.putText(img, "Esc quits", (max(10, w - 140), 29), FONT, 0.6, GREY, 1,
                cv2.LINE_AA)


def _draw_check(img, origin, color) -> None:
    """A little check mark drawn with lines (Hershey fonts can't render ✓)."""
    x, y = origin
    cv2.line(img, (x, y + 8), (x + 8, y + 16), color, 3, cv2.LINE_AA)
    cv2.line(img, (x + 8, y + 16), (x + 22, y - 4), color, 3, cv2.LINE_AA)


def _draw_decoded(img, text: str) -> None:
    """Frozen-frame overlay: DECODED banner + the recovered plaintext."""
    h, w = img.shape[:2]
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.45, img, 0.55, 0, img)   # dim so text pops

    cv2.putText(img, "DECODED  OK", (20, 46), FONT, 1.1, GREEN, 3, cv2.LINE_AA)
    _draw_check(img, (270, 22), GREEN)

    lines = _wrap(text, 44)
    shown, truncated = lines[:16], len(lines) > 16
    y = 92
    for ln in shown:
        cv2.putText(img, ln, (20, y), FONT, 0.7, WHITE, 2, cv2.LINE_AA)
        y += 30
    if truncated:
        cv2.putText(img, "... (truncated on screen)", (20, y), FONT, 0.7, GREY,
                    2, cv2.LINE_AA)
    cv2.putText(img, "Full plaintext printed to terminal.  Esc to quit.",
                (20, h - 18), FONT, 0.6, GREY, 1, cv2.LINE_AA)


# ── the live loop ──────────────────────────────────────────────────────────────
def run_live(*, key: bytes | None = None,
             key_path: str | os.PathLike | None = None,
             camera_index: int | None = None,
             window: str = "Kitsune live decode") -> int:
    """Continuous webcam decode loop. Returns a process exit code."""
    cap, idx = open_camera(camera_index)
    if cap is None:
        print("ERROR: could not open a webcam on index 0/1/2.\n"
              "  - Is another app using the camera?\n"
              "  - On macOS, has Terminal been granted Camera permission "
              "(System Settings > Privacy & Security > Camera)?\n"
              "  - Try: python live_decode.py --camera-index 1", file=sys.stderr)
        return 2

    print(f"[live_decode] webcam opened on index {idx}. "
          f"Point it at the Kitsune page. Esc to quit.")
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    decoded_text: str | None = None
    frozen = None
    try:
        while True:
            if decoded_text is None:
                ok, frame = cap.read()
                if not ok:
                    if (cv2.waitKey(1) & 0xFF) == 27:
                        break
                    continue
                display = frame.copy()
                status, color = "Scanning for Kitsune page...", AMBER
                try:
                    text = decode_frame(frame, key=key, key_path=key_path)
                    decoded_text = text
                    frozen = frame.copy()
                    print("\n================= DECODED =================")
                    print(decoded_text)
                    print("===========================================\n",
                          flush=True)
                except InvalidTag:
                    # QRs reassembled but authentication failed: show it, keep
                    # looping. NEVER freeze a bad decode as if it were real.
                    status, color = "INVALID - auth failed (keep scanning)", RED
                except Exception:
                    # no complete page in view yet (missing/garbled QRs): scan on
                    pass
                if decoded_text is None:
                    _draw_status(display, status, color)
                    cv2.imshow(window, display)

            if decoded_text is not None:
                disp = frozen.copy()
                _draw_decoded(disp, decoded_text)
                cv2.imshow(window, disp)

            if (cv2.waitKey(1) & 0xFF) == 27:      # Esc
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
    return 0


# ── headless self-check (the camera-free half of the Prompt 3 gate) ────────────
def selfcheck(verbose: bool = True) -> bool:
    """Compose a known secret -> save PNG -> load it as a BGR frame -> decode_frame.

    Proves the decode-from-frame plumbing end to end WITHOUT a camera, using the
    exact per-frame function the live loop calls. Uses a fixed key, so it never
    touches key.bin.
    """
    secret = ("kitsune live self-check :: " + "payload-block " * 40).strip()  # multi-QR
    with tempfile.TemporaryDirectory() as d:
        page = os.path.join(d, "selfcheck_page.png")
        compose_page(secret, page, key=SELFCHECK_KEY)
        frame = frame_from_image_file(page)          # BGR, exactly like a webcam frame
        try:
            got = decode_frame(frame, key=SELFCHECK_KEY)
        except Exception as e:                       # pragma: no cover - failure path
            got = f"<raised {type(e).__name__}: {e}>"
    ok = (got == secret)
    if verbose:
        print(f"[selfcheck] frame shape {tuple(frame.shape)} ({len(secret)} B secret)")
        print(f"[selfcheck] decode_frame round-trip: {'PASS' if ok else 'FAIL'}")
        if not ok:
            print(f"[selfcheck] expected: {secret!r}")
            print(f"[selfcheck] got:      {got!r}")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Kitsune live webcam decode (Prompt 3, Fork B: QR only).")
    ap.add_argument("--selfcheck", action="store_true",
                    help="headless: decode a saved page frame (no camera) and "
                         "assert the secret round-trips")
    ap.add_argument("--camera-index", type=int, default=None,
                    help="force a webcam index (default: probe 0,1,2)")
    ap.add_argument("--key-path", default=None,
                    help="path to key.bin (default: qr_crypto's key.bin next to "
                         "the sources)")
    args = ap.parse_args(argv)

    if args.selfcheck:
        return 0 if selfcheck() else 1
    return run_live(key_path=args.key_path, camera_index=args.camera_index)


if __name__ == "__main__":
    sys.exit(main())
