"""
live_decode.py — Kitsune live webcam decode  (Prompt 4 of 4, Fork B: QR only)

Points a webcam at a printed/displayed Kitsune page and recovers the plaintext
with a CONTINUOUS RETRY LOOP: every frame is a fresh decode attempt, so out of
the many frames per second, one clean frame wins even when most are blurred,
glared, or mid-focus. This is the whole point — the retry loop absorbs the
optical noise that a single capture would lose to.

Responsiveness (Prompt 4): DISPLAY is decoupled from DECODE. The raw feed is
shown on every captured frame so the preview stays smooth, while the expensive
ZBar decode runs only on every DECODE_EVERY-th frame, on a grayscale copy whose
long edge is downscaled to DECODE_MAX_DIM. Decode is off the display's critical
path, so the preview tracks the camera's native rate instead of being gated by
ZBar. On the first good decode the frame freezes and scanning STOPS. None of
this touches the crypto or QR format — the gates still decode at full resolution.

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
import time
from collections import deque

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

# ── live-loop responsiveness knobs (see run_live / the README perf section) ────
# ZBar's scan time scales with the pixel count it walks, and QR detection does
# NOT need 720p. Downscaling the grayscale frame so its long edge is at most
# DECODE_MAX_DIM before ZBar is the single biggest per-frame win; it is applied
# ONLY on the live path (the proven gates/tests still decode at full resolution).
DECODE_MAX_DIM = 900       # cap the long edge fed to ZBar (px); None = full res
DECODE_EVERY = 2           # run decode on every Nth captured frame (display all)


# ── the per-frame decode: BGR numpy frame -> secret (shared by loop + selfcheck)
def _to_grayscale_pil(frame) -> Image.Image:
    """cv2 BGR/BGRA/mono numpy frame -> grayscale PIL 'L' image (no cv2 needed)."""
    arr = np.asarray(frame)
    if arr.ndim == 2:                                  # already single-channel
        return Image.fromarray(arr, "L")
    if arr.ndim == 3 and arr.shape[2] == 4:            # BGRA -> RGB -> L
        return Image.fromarray(np.ascontiguousarray(arr[:, :, [2, 1, 0]]),
                               "RGB").convert("L")
    if arr.ndim == 3 and arr.shape[2] == 3:            # BGR (cv2 default) -> RGB -> L
        return Image.fromarray(np.ascontiguousarray(arr[:, :, ::-1]),
                               "RGB").convert("L")
    raise ValueError(f"unexpected frame shape {arr.shape}")


def _downscale_long_edge(pil: Image.Image, max_dim: int | None) -> Image.Image:
    """Shrink so the long edge is <= max_dim (only ever downscales, keeps ratio).

    BILINEAR matches the downscale the Prompt 2 photograph gate already proved
    QR-survivable. A no-op when max_dim is None or the image is already small.
    """
    if max_dim is None:
        return pil
    w, h = pil.size
    long_edge = max(w, h)
    if long_edge <= max_dim:
        return pil
    s = max_dim / long_edge
    return pil.resize((max(1, round(w * s)), max(1, round(h * s))), Image.BILINEAR)


def decode_frame(frame, *, key: bytes | None = None,
                 key_path: str | os.PathLike | None = None,
                 max_dim: int | None = None) -> str:
    """Decode one webcam frame (BGR numpy array) to the plaintext secret.

    Converts the frame to a grayscale PIL image (no cv2 needed here), optionally
    downscales its long edge to `max_dim` (ZBar speed-up; None = full res), then
    runs the exact compose_page.decode_page_image() path. Raises ValueError if no
    complete set of QRs is readable yet, or cryptography's InvalidTag if the
    reassembled blob fails authentication (wrong key / garbled) — callers must
    treat that as "not decoded", never as a secret.
    """
    if frame is None:
        raise ValueError("empty frame")
    pil = _downscale_long_edge(_to_grayscale_pil(frame), max_dim)
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


def _draw_status(img, text: str, color, *, hud: str = "") -> None:
    """Top status bar for the live (not-yet-decoded) state.

    `hud` is a small right-aligned live perf readout (preview fps + decode ms) so
    the responsiveness work is visible on screen during the demo.
    """
    h, w = img.shape[:2]
    cv2.rectangle(img, (0, 0), (w, 42), (0, 0, 0), -1)
    cv2.putText(img, text, (10, 29), FONT, 0.7, color, 2, cv2.LINE_AA)
    right = f"{hud}   Esc quits" if hud else "Esc quits"
    (tw, _), _ = cv2.getTextSize(right, FONT, 0.6, 1)
    cv2.putText(img, right, (max(10, w - tw - 12), 29), FONT, 0.6, GREY, 1,
                cv2.LINE_AA)


def _draw_check(img, origin, color, scale: int = 1) -> None:
    """A check mark drawn with lines (Hershey fonts can't render ✓)."""
    x, y = origin
    cv2.line(img, (x, y + 8 * scale), (x + 8 * scale, y + 16 * scale), color,
             3 * scale, cv2.LINE_AA)
    cv2.line(img, (x + 8 * scale, y + 16 * scale), (x + 22 * scale, y - 4 * scale),
             color, 3 * scale, cv2.LINE_AA)


def _draw_decoded(img, text: str) -> None:
    """Frozen-frame overlay: a big, obvious DECODED OK banner + the plaintext.

    Deliberately loud — other people watch this screen. A full green banner, a
    large "DECODED OK" with a check, then the recovered plaintext in a dark panel
    so it stays crisply readable over whatever the frozen webcam frame shows.
    """
    h, w = img.shape[:2]
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.62, img, 0.38, 0, img)   # dim hard so text pops

    # ── loud green success banner across the top ─────────────────────────────
    banner_h = max(64, h // 8)
    cv2.rectangle(img, (0, 0), (w, banner_h), GREEN, -1)
    fs = banner_h / 64.0                                 # scale text to banner
    _draw_check(img, (28, banner_h // 2 - 10), (0, 0, 0), scale=max(1, int(fs)))
    cv2.putText(img, "DECODED  OK", (28 + int(46 * fs), int(banner_h * 0.66)),
                FONT, 1.2 * fs, (0, 0, 0), max(2, int(3 * fs)), cv2.LINE_AA)

    # ── recovered plaintext in a readable dark panel ─────────────────────────
    lines = _wrap(text, 46)
    shown, truncated = lines[:14], len(lines) > 14
    label_y = banner_h + 44
    cv2.putText(img, "recovered plaintext:", (24, label_y), FONT, 0.7, GREEN, 2,
                cv2.LINE_AA)

    line_h = 34
    panel_top = label_y + 16
    panel_bot = panel_top + line_h * (len(shown) + (1 if truncated else 0)) + 16
    panel = img.copy()
    cv2.rectangle(panel, (16, panel_top), (w - 16, min(h - 44, panel_bot)),
                  (0, 0, 0), -1)
    cv2.addWeighted(panel, 0.55, img, 0.45, 0, img)

    y = panel_top + 30
    for ln in shown:
        cv2.putText(img, ln, (28, y), FONT, 0.82, WHITE, 2, cv2.LINE_AA)
        y += line_h
    if truncated:
        cv2.putText(img, "... (truncated on screen)", (28, y), FONT, 0.7, GREY,
                    2, cv2.LINE_AA)
    cv2.putText(img, "Full plaintext printed to terminal.  Esc to quit.",
                (24, h - 18), FONT, 0.62, GREY, 1, cv2.LINE_AA)


# ── the live loop ──────────────────────────────────────────────────────────────
def run_live(*, key: bytes | None = None,
             key_path: str | os.PathLike | None = None,
             camera_index: int | None = None,
             window: str = "Kitsune live decode",
             decode_every: int = DECODE_EVERY,
             decode_max_dim: int | None = DECODE_MAX_DIM) -> int:
    """Continuous webcam decode loop. Returns a process exit code.

    Responsiveness: the raw webcam feed is shown on EVERY captured frame so the
    preview stays smooth, but the (relatively expensive) decode runs only on
    every `decode_every`-th frame, on a grayscale copy downscaled to
    `decode_max_dim`. Decode is thus off the display's critical path — the
    preview tracks the camera's native rate instead of being gated by ZBar. On
    the first successful decode we freeze and STOP scanning (no more CPU burnt).
    """
    decode_every = max(1, decode_every)
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
    print(f"[live_decode] decode every {decode_every} frame(s), "
          f"long edge capped at {decode_max_dim or 'full res'} px for ZBar.")
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    decoded_text: str | None = None
    frozen = None
    frame_no = 0
    last_decode_ms = 0.0                     # per-frame decode time (HUD readout)
    stamps: deque[float] = deque(maxlen=30)  # recent shown-frame times -> fps
    try:
        while True:
            if decoded_text is None:
                ok, frame = cap.read()
                if not ok:
                    if (cv2.waitKey(1) & 0xFF) == 27:
                        break
                    continue
                frame_no += 1
                display = frame.copy()
                status, color = "Scanning for Kitsune page...", AMBER

                # DECODE only every Nth frame; every frame is still displayed.
                if frame_no % decode_every == 0:
                    t0 = time.perf_counter()
                    try:
                        text = decode_frame(frame, key=key, key_path=key_path,
                                            max_dim=decode_max_dim)
                        decoded_text = text
                        frozen = frame.copy()
                        print("\n================= DECODED =================")
                        print(decoded_text)
                        print("======================================="
                              "====\n", flush=True)
                    except InvalidTag:
                        # QRs reassembled but authentication failed: show it, keep
                        # looping. NEVER freeze a bad decode as if it were real.
                        status, color = "INVALID - auth failed (keep scanning)", RED
                    except Exception:
                        # no complete page in view yet (garbled/missing QRs): scan on
                        pass
                    last_decode_ms = (time.perf_counter() - t0) * 1000.0

                if decoded_text is None:
                    stamps.append(time.perf_counter())
                    fps = ((len(stamps) - 1) / (stamps[-1] - stamps[0])
                           if len(stamps) > 1 and stamps[-1] > stamps[0] else 0.0)
                    hud = f"{fps:4.1f} fps | decode {last_decode_ms:2.0f} ms"
                    _draw_status(display, status, color, hud=hud)
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
    ap.add_argument("--decode-every", type=int, default=DECODE_EVERY,
                    help=f"run decode on every Nth captured frame; every frame is "
                         f"still displayed (default: {DECODE_EVERY})")
    ap.add_argument("--decode-max-dim", type=int, default=DECODE_MAX_DIM,
                    help=f"cap the long edge (px) fed to ZBar; 0 = full res "
                         f"(default: {DECODE_MAX_DIM})")
    args = ap.parse_args(argv)

    if args.selfcheck:
        return 0 if selfcheck() else 1
    return run_live(key_path=args.key_path, camera_index=args.camera_index,
                    decode_every=args.decode_every,
                    decode_max_dim=(args.decode_max_dim or None))


if __name__ == "__main__":
    sys.exit(main())
