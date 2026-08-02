"""
compose_page.py — Kitsune page layer  (Prompt 2 of 4, Fork B: QR only)

Builds the PAGE layer on top of qr_crypto WITHOUT modifying its crypto or QR
format. Two directions:

    compose_page(secret) -> a single printable page PNG:  title + a grid of the
        segmented QR PNGs that qr_crypto already produces.

    decode_page(page.png) -> secret:  find EVERY QR on the one page image,
        reassemble the chunks (same envelope format as qr_crypto), AES-decrypt.

Why a new decoder instead of qr_crypto.qr_to_bytes: qr_to_bytes decodes exactly
one QR per PNG file. Prompt 2 photographs the whole composed page, so we must
pull ALL QRs out of ONE (degraded) image. That multi-symbol read is the only new
primitive here; it reuses qr_crypto's zbar wrapper and BINARY-mode settings so
the bytes stay exact. The AES layer, the QR envelope, and DATA_PER_CHUNK are
untouched — imported straight from qr_crypto.

Fork decision (Prompt 2): QR only. No decoy cover text. GLM-OCR is not in this
pipeline; it survives only as an optional "an outsider's OCR sees noise"
screenshot for the report.
"""

from __future__ import annotations

import os
import tempfile
from ctypes import cast, c_void_p, string_at
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from qr_crypto import (
    aes_encrypt, aes_decrypt, load_or_create_key, bytes_to_qr,
    HEADER_SIZE, ZBAR_CFG_BINARY, _ZBAR_FORMAT_L800,
)
from pyzbar.wrapper import (
    zbar_image_scanner_create, zbar_image_scanner_destroy,
    zbar_image_scanner_set_config,
    zbar_image_create, zbar_image_destroy,
    zbar_image_set_format, zbar_image_set_size, zbar_image_set_data,
    zbar_scan_image, zbar_image_first_symbol,
    zbar_symbol_get_data, zbar_symbol_get_data_length, zbar_symbol_next,
    ZBarConfig, ZBarSymbol,
)

# ── page layout constants (page pixels, not QR modules) ───────────────────────
PAGE_BG = 255              # white page
PAGE_MARGIN = 48           # white margin around the whole page
QR_GAP = 36                # white gutter between adjacent QRs (extra quiet zone)
TITLE_GAP = 24             # space between the title band and the QR grid
GRID_COLS = 3              # QRs per row before wrapping to the next row
DEFAULT_TITLE = "Kitsune - encrypted page (unreadable without the key)"


# ── compose: secret -> one printable page PNG ─────────────────────────────────
def _grid_dims(n: int, cols: int = GRID_COLS) -> tuple[int, int]:
    """Return (cols, rows) for laying out n items at most `cols` per row."""
    cols = max(1, min(cols, n))
    rows = (n + cols - 1) // cols
    return cols, rows


def compose_page(plaintext_secret: str,
                 out_path: str | os.PathLike,
                 *,
                 key: bytes | None = None,
                 title: str = DEFAULT_TITLE,
                 key_path: str | os.PathLike | None = None) -> str:
    """Encrypt `plaintext_secret` and lay its QR PNGs onto one printable page.

    plaintext -> AES-256-GCM (qr_crypto) -> segmented QR PNGs (qr_crypto) ->
    composed onto a white page (title band + QR grid) -> saved to `out_path`.

    Returns the written page path.
    """
    if key is None:
        key = (load_or_create_key(key_path) if key_path is not None
               else load_or_create_key())

    blob = aes_encrypt(plaintext_secret, key)

    with tempfile.TemporaryDirectory() as qr_dir:
        qr_paths = bytes_to_qr(blob, qr_dir)          # qr_crypto owns the format
        qr_imgs = [Image.open(p).convert("L") for p in qr_paths]

        # Normalise every QR into a uniform cell (max QR box) so the grid is
        # even and each QR keeps a clean white surround.
        cell_w = max(im.width for im in qr_imgs)
        cell_h = max(im.height for im in qr_imgs)

        cols, rows = _grid_dims(len(qr_imgs))

        # Title band height from the default bitmap font.
        font = ImageFont.load_default()
        tmp_draw = ImageDraw.Draw(Image.new("L", (1, 1)))
        t_bbox = tmp_draw.textbbox((0, 0), title, font=font)
        title_h = (t_bbox[3] - t_bbox[1])

        grid_w = cols * cell_w + (cols - 1) * QR_GAP
        grid_h = rows * cell_h + (rows - 1) * QR_GAP

        page_w = grid_w + 2 * PAGE_MARGIN
        page_h = PAGE_MARGIN + title_h + TITLE_GAP + grid_h + PAGE_MARGIN

        page = Image.new("L", (page_w, page_h), color=PAGE_BG)
        draw = ImageDraw.Draw(page)
        draw.text((PAGE_MARGIN, PAGE_MARGIN), title, fill=0, font=font)

        grid_top = PAGE_MARGIN + title_h + TITLE_GAP
        for i, qr in enumerate(qr_imgs):
            r, c = divmod(i, cols)
            cell_x = PAGE_MARGIN + c * (cell_w + QR_GAP)
            cell_y = grid_top + r * (cell_h + QR_GAP)
            # centre the QR inside its uniform cell
            off_x = cell_x + (cell_w - qr.width) // 2
            off_y = cell_y + (cell_h - qr.height) // 2
            page.paste(qr, (off_x, off_y))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    page.save(out)
    return str(out)


# ── decode: one page image -> every QR's bytes -> reassemble -> secret ────────
def _decode_all_qr_bytes(image: Image.Image) -> list[bytes]:
    """Return the EXACT raw bytes of every QR code found in `image`.

    Same zbar BINARY-mode path as qr_crypto._decode_qr_bytes, but walks the full
    symbol list (first_symbol -> symbol_next) instead of returning only the first
    — because a Kitsune page carries several QRs in one image.
    """
    if image.mode != "L":
        image = image.convert("L")
    pixels = image.tobytes()
    width, height = image.size

    scanner = zbar_image_scanner_create()
    try:
        for sym in set(ZBarSymbol).difference({ZBarSymbol.QRCODE}):
            zbar_image_scanner_set_config(scanner, sym, ZBarConfig.CFG_ENABLE, 0)
        zbar_image_scanner_set_config(scanner, ZBarSymbol.QRCODE,
                                      ZBarConfig.CFG_ENABLE, 1)
        zbar_image_scanner_set_config(scanner, ZBarSymbol.QRCODE,
                                      ZBAR_CFG_BINARY, 1)

        zimg = zbar_image_create()
        try:
            zbar_image_set_format(zimg, _ZBAR_FORMAT_L800)
            zbar_image_set_size(zimg, width, height)
            zbar_image_set_data(zimg, cast(pixels, c_void_p), len(pixels), None)
            if zbar_scan_image(scanner, zimg) < 0:
                raise ValueError("zbar could not process the page image")

            out: list[bytes] = []
            symbol = zbar_image_first_symbol(zimg)
            while symbol:
                out.append(string_at(zbar_symbol_get_data(symbol),
                                     zbar_symbol_get_data_length(symbol)))
                symbol = zbar_symbol_next(symbol)
            return out
        finally:
            zbar_image_destroy(zimg)
    finally:
        zbar_image_scanner_destroy(scanner)


def _reassemble(envelopes: list[bytes]) -> bytes:
    """Reassemble the ciphertext blob from decoded QR envelopes.

    Envelope format is qr_crypto's, unchanged: [index:1][total:1][chunk_len:2BE]
    [chunk...]. Order-independent (sorts by index), like qr_crypto.qr_to_bytes.
    """
    frames: dict[int, bytes] = {}
    total_expected: int | None = None
    for env in envelopes:
        if len(env) < HEADER_SIZE:
            continue                       # not one of ours; ignore stray reads
        index = env[0]
        total = env[1]
        chunk_len = int.from_bytes(env[2:4], "big")
        chunk = env[HEADER_SIZE:]
        if len(chunk) != chunk_len:
            raise ValueError(
                f"chunk {index}: header says {chunk_len} bytes but got {len(chunk)}")
        if total_expected is None:
            total_expected = total
        elif total != total_expected:
            raise ValueError(f"inconsistent total ({total} vs {total_expected})")
        if index >= total:
            raise ValueError(f"index {index} out of range for total {total}")
        if index in frames and frames[index] != chunk:
            raise ValueError(f"conflicting data for chunk index {index}")
        frames[index] = chunk

    if total_expected is None:
        raise ValueError("no QR codes decoded from the page")
    missing = [i for i in range(total_expected) if i not in frames]
    if missing:
        raise ValueError(f"missing chunk indices {missing}")
    return b"".join(frames[i] for i in range(total_expected))


def decode_page_image(image: Image.Image,
                      *,
                      key: bytes | None = None,
                      key_path: str | os.PathLike | None = None) -> str:
    """Read every QR on an in-memory page image, reassemble, and AES-decrypt.

    This is the core decode used by both decode_page (from disk) and the
    photograph-simulation gate (which degrades a reopened page in memory before
    decoding). Raises ValueError if a QR is unreadable / a chunk is missing, or
    cryptography's InvalidTag if the reassembled blob fails authentication.
    """
    if key is None:
        key = (load_or_create_key(key_path) if key_path is not None
               else load_or_create_key())
    envelopes = _decode_all_qr_bytes(image)
    blob = _reassemble(envelopes)
    return aes_decrypt(blob, key)


def decode_page(page_path: str | os.PathLike,
                *,
                key: bytes | None = None,
                key_path: str | os.PathLike | None = None) -> str:
    """Open a saved page PNG and decode the secret from it (see decode_page_image)."""
    return decode_page_image(Image.open(page_path), key=key, key_path=key_path)
