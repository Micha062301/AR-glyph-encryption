# Kitsune — encrypted-page privacy demo

Kitsune is a privacy demo: a secret is printed onto an ordinary-looking page as
QR codes, unreadable to anyone glancing at it, and recovered only by an
authorized viewer who points a webcam at the page. The privacy comes from
encryption — an onlooker who scans the QR gets nothing but random ciphertext
bytes; without the key it stays meaningless. This is an MVP / college project:
no AR glasses, no headset, no live tracking, no multi-user auth, and no key
distribution — all out of scope by design.

## Architecture

```
encode:  plaintext ──AES-256-GCM──▶ ciphertext bytes ──segment──▶ QR PNG(s)
                                              └──────compose──────▶ page image (PNG)

decode:  webcam frame ──▶ find ALL QRs on the page ──▶ reassemble chunks
                     ──▶ AES-256-GCM decrypt ──▶ plaintext
```

- **Encryption:** AES-256-GCM, authenticated. The blob is `nonce(12) ‖ tag(16) ‖
  ciphertext`. Any altered or garbled byte fails the GCM tag on decrypt (raises
  `InvalidTag`) instead of returning garbage — so a bad read is never mistaken
  for a real secret.
- **QR channel:** the ciphertext is split across one or more QR codes (envelope
  `[index:1][total:1][chunk_len:2BE][chunk]`, ECC-H). Decoding uses `pyzbar` in
  zbar **binary** mode to get the exact raw bytes back; reassembly is
  order-independent.
- **Fork B (QR only).** There is **no decoy cover text**, and **GLM-OCR is not in
  this pipeline**. (The webcam + GLM-OCR document scanner lives only on the `gs`
  branch and is unrelated to this decode path.)

## Files

| File | Role |
|------|------|
| `qr_crypto.py`     | AES-256-GCM ⇄ segmented QR PNGs (the crypto + QR channel) |
| `compose_page.py`  | `compose_page(secret)` → page PNG; `decode_page_image(img)` decodes **all** QRs on one image + reassembles + decrypts |
| `live_decode.py`   | Live webcam continuous-retry decode; `decode_frame(bgr_frame)` is the per-frame path; `--selfcheck` runs it headless |
| `test_roundtrip.py`| Prompt 1 gate — clean digital QR round-trips (17/17) |
| `test_page.py`     | Prompt 2 — page compose → save → reopen → decode round-trips |
| `gate_page.py`     | Prompt 2 gate — **simulated** photo degradation (downscale, JPEG, ±5° rotation, brightness gradient) |
| `test_live.py`     | Prompt 3 — decode-from-frame plumbing (camera-free) |
| `key.bin`          | Local AES-256 key, auto-created on first use. **Gitignored — never committed.** |

## Setup

Requires **Python 3.10+** (developed on 3.13) and the native **zbar** library —
`pyzbar` binds to it and `pip` cannot install it for you:

```bash
brew install zbar          # macOS
# sudo apt install libzbar0  # Debian / Ubuntu
```

Then create the virtual environment and install the Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install cryptography qrcode pillow pyzbar numpy opencv-python
```

Versions this was built against: `cryptography 49.0.0`, `qrcode 8.2`,
`pillow 12.3.0`, `pyzbar 0.1.9`, `numpy 2.5.1`, `opencv-python 5.0.0.93`.

Sanity-check the whole stack before touching a camera:

```bash
python test_roundtrip.py     # Prompt 1: 17/17
python test_page.py          # Prompt 2: page round-trips
python gate_page.py          # Prompt 2: simulated-photo gate (small payloads ~100%)
python test_live.py          # Prompt 3: decode-from-frame
python live_decode.py --selfcheck   # decode a saved page frame, no camera
```

## Running the demo (Prompt 3 manual test)

The live decoder needs a real camera, so run it by hand:

1. **Generate a page for a known secret.** This also creates `key.bin` if it
   doesn't exist; the same key is reused for decoding.

   ```bash
   python -c "from compose_page import compose_page; compose_page('meet at dawn - 0400 pier 7', 'demo_page.png'); print('wrote demo_page.png')"
   ```

2. **Display or print `demo_page.png`.** Open it and make it large — fill much of
   the screen (or print it and lay it flat). Bigger, sharper QR modules decode
   far more reliably than a small on-screen thumbnail.

3. **Run the live decoder** (uses the same `key.bin`):

   ```bash
   python live_decode.py
   ```

   Options: `--camera-index N` to force a specific webcam (it probes 0, 1, 2 by
   default); `--key-path PATH` to point at a different `key.bin`.

4. **Point the webcam at the page.** A live window shows the feed with a
   "Scanning…" status. Hold reasonably steady, fill the frame with the page, and
   let the camera focus. The loop tries every frame, so one clean frame wins.

5. **Confirm the recovery.** On the first good frame the window **freezes**,
   shows **DECODED OK** with the recovered text overlaid, and the full plaintext
   prints to the terminal — it must read `meet at dawn - 0400 pier 7`, the same
   secret from step 1. Press **Esc** to quit (releases the camera cleanly).

**What to expect / if it struggles.** The per-frame decode rate will be *lower*
than the Prompt 2 simulation — real capture adds motion blur, glare, focus
hunting, and non-flat paper that the simulated gate doesn't. That's exactly what
the continuous retry loop is for: at many frames per second, one clean frame is
enough. If a frame's QRs reassemble but the key/authentication is wrong, the
window shows **INVALID - auth failed** and keeps scanning — a bad decode is never
shown as if it were real. If it *consistently* fails to decode, the cause is
almost certainly **optical**, not the crypto/QR format (which is proven by the
gates above):

- the QR is too small on screen → enlarge it / move the camera closer;
- glare or reflection on the screen/paper → change the angle or lighting;
- the camera can't focus that close → back off slightly until it sharpens;
- the page is curved or tilted → flatten it and square it to the lens.

## Known limitations (MVP scope)

- **System `zbar` is required.** `pip install pyzbar` does not bundle the native
  library; without `brew install zbar` / `apt install libzbar0`, importing the
  decode path fails with an `ImportError`/`OSError`.
- **Key management is intentionally minimal.** The AES-256 key is a single local
  `key.bin` (gitignored). There is **no key distribution or exchange** — encode
  and decode must share the same `key.bin` (same machine). Real key distribution
  is explicitly out of scope for this MVP.
- **Large payloads lose decode headroom.** Small secrets (≤ ~256 B) round-trip
  at ~100% through the simulated-photo gate; a ~1 KB payload spans ~5 dense QR
  codes and loses margin under stacked degradation (~95% in simulation, and
  expect worse on a live camera). Keep demo secrets short.
