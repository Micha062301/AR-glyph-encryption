"""
qr_crypto.py — Kitsune crypto + QR channel  (Prompt 1 of 4)

Lossless, in-process round-trip:

    plaintext(str) --AES-256-GCM--> ciphertext bytes --> one or more QR PNGs
    QR PNG(s) --pyzbar (binary mode)--> ciphertext bytes --AES-256-GCM--> plaintext(str)

No camera, no OCR, no cover text — those are later prompts.

------------------------------------------------------------------------------
DESIGN NOTES / DEVIATIONS FROM THE BRIEF  (every one verified empirically)
------------------------------------------------------------------------------
1. AES layer originated in `mu:cipher.py`. Contrary to the brief's "the code
   generates a random IV and discards it", the committed mu version ALREADY
   prepended the IV on encrypt and split it off on decrypt — so there was no IV
   bug to fix. That CBC layer has now been UPGRADED to AES-256-GCM, an
   authenticated cipher (no PKCS7 padding — GCM is a stream mode). The 12-byte
   GCM nonce and the 16-byte auth tag are prepended to the ciphertext, reusing
   the very same "prepend it so decrypt can recover it" pattern the IV used:

       blob = nonce(12) || tag(16) || ciphertext

   Because GCM authenticates, ANY tampered or corrupted byte (nonce, tag, or
   ciphertext) makes aes_decrypt raise cryptography's InvalidTag instead of
   returning garbage plaintext.

2. The 152-glyph table, the %152/%256 mapping and the time.time() three-rotor
   shift were DELETED, not salvaged.

3. QR carries the RAW ciphertext bytes in byte mode — no base64 wrapping.

4. Decoder is pyzbar (libzbar), NOT cv2.QRCodeDetector. Measured reasons:
     - cv2 returns an EMPTY string for much raw-binary content (e.g. bytes
       0..255, ~1 KB payloads) and cannot decode high-version QRs at all
       (v37 -> 0/20 in testing); it is even a few-percent flaky on small
       clean QRs.
     - pyzbar's *default* output re-encodes bytes 0x80-0xFF as UTF-8, so it is
       not byte-exact either.
     - pyzbar with zbar's BINARY mode (ZBAR_CFG_BINARY) returns EXACT raw bytes
       and was 100% reliable across all sizes tested (100/100 up to v36).
   pyzbar 0.1.9 does not expose CFG_BINARY, so we enable it through the
   documented wrapper call `zbar_image_scanner_set_config`.

5. A single QR / decoder has finite capacity, and long messages need it anyway,
   so the ciphertext is SEGMENTED across multiple QR PNGs (qr_000.png, ...).
   Per-QR envelope (raw bytes, big-endian length):

       [index:1][total:1][chunk_len:2][chunk bytes...]

   Reassembly sorts by index, validates total/index/length consistency, and
   concatenates the chunks back into the ciphertext blob before AES-decrypt.
------------------------------------------------------------------------------
"""

from __future__ import annotations

import os
from ctypes import cast, c_void_p, string_at
from pathlib import Path

import qrcode
import qrcode.constants
from PIL import Image
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidTag  # re-exported for callers

# pyzbar 0.1.9 exposes the low-level zbar bindings we need to turn on BINARY
# mode (the high-level pyzbar.decode() has no option for it).
from pyzbar.wrapper import (
    zbar_image_scanner_create, zbar_image_scanner_destroy,
    zbar_image_scanner_set_config,
    zbar_image_create, zbar_image_destroy,
    zbar_image_set_format, zbar_image_set_size, zbar_image_set_data,
    zbar_scan_image, zbar_image_first_symbol,
    zbar_symbol_get_data, zbar_symbol_get_data_length, zbar_symbol_next,
    ZBarConfig, ZBarSymbol,
)

# ── constants ────────────────────────────────────────────────────────────────
KEY_SIZE = 32                 # AES-256
NONCE_SIZE = 12               # 96-bit GCM nonce (the recommended size)
TAG_SIZE = 16                 # 128-bit GCM authentication tag
DEFAULT_KEY_PATH = Path(__file__).resolve().parent / "key.bin"

QR_ECC = qrcode.constants.ERROR_CORRECT_H   # highest ECC (camera-friendly later)
QR_BOX_SIZE = 10
QR_BORDER = 4

HEADER_SIZE = 4               # [index:1][total:1][chunk_len:2]
MAX_CHUNKS = 255              # index/total are one byte each
# Ciphertext bytes per QR. 256 + 4-byte header -> ~QR v17 at ECC-H, which pyzbar
# decoded 100% reliably in testing. Lower this to shrink each QR (friendlier for
# camera capture in a later prompt) at the cost of more images.
DATA_PER_CHUNK = 256

# zbar: "don't convert binary data to text". Value confirmed from the installed
# zbar.h enum (ENABLE=0, ADD_CHECK, EMIT_CHECK, ASCII, BINARY=4). Not present in
# pyzbar 0.1.9's ZBarConfig, so we pass the raw int.
ZBAR_CFG_BINARY = 4
_ZBAR_FORMAT_L800 = 808466521   # 8-bit greyscale fourcc, as used by pyzbar


# ── AES-256-GCM (authenticated; nonce + tag prepended, same pattern as the IV) ─
def aes_encrypt(plaintext: str, key: bytes) -> bytes:
    """Encrypt `plaintext` -> (nonce || tag || ciphertext) bytes with AES-256-GCM.

    GCM authenticates the ciphertext, so aes_decrypt can detect tampering. The
    nonce and tag are prepended (same idea as the old CBC IV) so decrypt can
    recover them. GCM is a stream mode, so there is no PKCS7 padding.
    """
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes, got {len(key)}")
    nonce = os.urandom(NONCE_SIZE)
    encryptor = Cipher(
        algorithms.AES(key), modes.GCM(nonce), backend=default_backend()
    ).encryptor()
    ciphertext = encryptor.update(plaintext.encode("utf-8")) + encryptor.finalize()
    # `encryptor.tag` is only populated after finalize(). Prepend nonce + tag so
    # decrypt can recover them, exactly as the IV was prepended before.
    return nonce + encryptor.tag + ciphertext


def aes_decrypt(blob: bytes, key: bytes) -> str:
    """Inverse of aes_encrypt: (nonce || tag || ciphertext) bytes -> plaintext str.

    Raises cryptography's InvalidTag if the nonce, tag, or ciphertext was altered
    or corrupted — a tampered blob fails authentication instead of decoding to
    garbage.
    """
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes, got {len(key)}")
    if len(blob) < NONCE_SIZE + TAG_SIZE:
        raise ValueError("ciphertext blob is too short to hold nonce + tag")
    nonce = blob[:NONCE_SIZE]
    tag = blob[NONCE_SIZE:NONCE_SIZE + TAG_SIZE]
    ciphertext = blob[NONCE_SIZE + TAG_SIZE:]
    decryptor = Cipher(
        algorithms.AES(key), modes.GCM(nonce, tag), backend=default_backend()
    ).decryptor()
    # finalize() verifies the GCM tag and raises InvalidTag on any mismatch.
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    return plaintext.decode("utf-8")


# ── key handling (MVP: local key.bin, os.urandom if absent) ───────────────────
def load_or_create_key(path: str | os.PathLike = DEFAULT_KEY_PATH) -> bytes:
    """Load a 32-byte key from `path`, generating and storing one if absent."""
    p = Path(path)
    if p.exists():
        key = p.read_bytes()
        if len(key) != KEY_SIZE:
            raise ValueError(f"{p} is {len(key)} bytes, expected {KEY_SIZE}")
        return key
    key = os.urandom(KEY_SIZE)
    p.write_bytes(key)
    try:
        os.chmod(p, 0o600)          # best effort: keep the secret private
    except OSError:
        pass
    return key


# ── QR encode: ciphertext bytes -> segmented QR PNGs ──────────────────────────
def _chunk(blob: bytes, size: int) -> list[bytes]:
    if not blob:
        return [b""]                # empty payload still yields one QR
    return [blob[i:i + size] for i in range(0, len(blob), size)]


def bytes_to_qr(blob: bytes, out_dir: str | os.PathLike,
                prefix: str = "qr", ecc: int = QR_ECC) -> list[str]:
    """Write `blob` as one or more QR PNGs in `out_dir` (qr_000.png, ...).

    Returns the list of written file paths, in index order.
    """
    chunks = _chunk(blob, DATA_PER_CHUNK)
    total = len(chunks)
    if total > MAX_CHUNKS:
        raise ValueError(
            f"payload needs {total} chunks but the envelope supports "
            f"at most {MAX_CHUNKS}"
        )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths: list[str] = []
    for index, chunk in enumerate(chunks):
        envelope = bytes([index, total]) + len(chunk).to_bytes(2, "big") + chunk
        qr = qrcode.QRCode(error_correction=ecc,
                           box_size=QR_BOX_SIZE, border=QR_BORDER)
        qr.add_data(envelope)       # bytes -> QR byte mode
        qr.make(fit=True)
        path = out / f"{prefix}_{index:03d}.png"
        qr.make_image(fill_color="black", back_color="white").save(path)
        paths.append(str(path))
    return paths


# ── QR decode: pyzbar in BINARY mode -> exact raw bytes ───────────────────────
def _decode_qr_bytes(path: str | os.PathLike) -> bytes:
    """Decode the single QR code in `path` and return its EXACT raw bytes."""
    img = Image.open(path)
    if img.mode != "L":
        img = img.convert("L")
    pixels = img.tobytes()
    width, height = img.size

    scanner = zbar_image_scanner_create()
    try:
        # QR only: silences zbar's DataBar assertion spam on binary data and
        # avoids mis-detections. Then enable raw-byte (binary) output.
        for sym in set(ZBarSymbol).difference({ZBarSymbol.QRCODE}):
            zbar_image_scanner_set_config(scanner, sym, ZBarConfig.CFG_ENABLE, 0)
        zbar_image_scanner_set_config(scanner, ZBarSymbol.QRCODE,
                                      ZBarConfig.CFG_ENABLE, 1)
        zbar_image_scanner_set_config(scanner, ZBarSymbol.QRCODE,
                                      ZBAR_CFG_BINARY, 1)

        image = zbar_image_create()
        try:
            zbar_image_set_format(image, _ZBAR_FORMAT_L800)
            zbar_image_set_size(image, width, height)
            zbar_image_set_data(image, cast(pixels, c_void_p), len(pixels), None)
            if zbar_scan_image(scanner, image) < 0:
                raise ValueError("zbar could not process the image")
            symbol = zbar_image_first_symbol(image)
            if not symbol:
                raise ValueError(f"no QR code found in {path}")
            return string_at(zbar_symbol_get_data(symbol),
                             zbar_symbol_get_data_length(symbol))
        finally:
            zbar_image_destroy(image)
    finally:
        zbar_image_scanner_destroy(scanner)


def _resolve_sources(source, prefix: str = "qr") -> list[str]:
    """Normalise `source` to a list of PNG paths.

    Accepts a list/tuple of paths, a directory (globs `<prefix>_*.png`), or a
    single PNG path.
    """
    if isinstance(source, (list, tuple)):
        if not source:
            raise ValueError("no QR paths supplied")
        return [str(s) for s in source]
    p = Path(source)
    if p.is_dir():
        files = sorted(p.glob(f"{prefix}_*.png"))
        if not files:
            raise ValueError(f"no {prefix}_*.png files in {p}")
        return [str(f) for f in files]
    return [str(p)]


def qr_to_bytes(source, prefix: str = "qr") -> bytes:
    """Decode + reassemble one or more QR PNGs back into the ciphertext blob.

    `source` may be the list returned by bytes_to_qr, a directory, or a single
    PNG path. Reassembly is by the envelope index header, so file order does not
    matter.
    """
    paths = _resolve_sources(source, prefix=prefix)

    frames: dict[int, bytes] = {}
    total_expected: int | None = None
    for path in paths:
        envelope = _decode_qr_bytes(path)
        if len(envelope) < HEADER_SIZE:
            raise ValueError(f"{path}: envelope shorter than header")
        index = envelope[0]
        total = envelope[1]
        chunk_len = int.from_bytes(envelope[2:4], "big")
        chunk = envelope[HEADER_SIZE:]
        if len(chunk) != chunk_len:
            raise ValueError(
                f"{path}: header says chunk_len={chunk_len} but got {len(chunk)}"
            )
        if total_expected is None:
            total_expected = total
        elif total != total_expected:
            raise ValueError(
                f"{path}: inconsistent total ({total} vs {total_expected})"
            )
        if index >= total:
            raise ValueError(f"{path}: index {index} out of range for total {total}")
        if index in frames:
            raise ValueError(f"duplicate chunk index {index}")
        frames[index] = chunk

    if total_expected is None:
        raise ValueError("no QR codes decoded")
    missing = [i for i in range(total_expected) if i not in frames]
    if missing:
        raise ValueError(f"missing chunk indices {missing}")
    return b"".join(frames[i] for i in range(total_expected))


# ── convenience: full string <-> QR pipeline ─────────────────────────────────
def encrypt_to_qr(plaintext: str, out_dir: str | os.PathLike,
                  key: bytes | None = None,
                  key_path: str | os.PathLike = DEFAULT_KEY_PATH) -> list[str]:
    """plaintext -> AES-256-GCM -> segmented QR PNGs. Returns written paths."""
    if key is None:
        key = load_or_create_key(key_path)
    return bytes_to_qr(aes_encrypt(plaintext, key), out_dir)


def decrypt_from_qr(source, key: bytes | None = None,
                    key_path: str | os.PathLike = DEFAULT_KEY_PATH) -> str:
    """QR PNG(s) -> ciphertext -> AES-256-GCM -> plaintext str."""
    if key is None:
        key = load_or_create_key(key_path)
    return aes_decrypt(qr_to_bytes(source), key)
