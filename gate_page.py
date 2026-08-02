"""
gate_page.py — Kitsune Prompt 2 gate  (Fork B: QR only)

Bridges "clean digital QR round-trips" (Prompt 1) to "a PHOTOGRAPHED page
round-trips" — still NO live webcam (that is Prompt 3). Everything runs on saved
image files so it is fast and repeatable.

For each payload size (short, ~256B, ~1KB):
    compose_page(secret) -> SAVE .png -> RE-OPEN it
    -> photograph-simulate the reopened image -> decode_page -> compare
The four photograph simulations (per the brief):
    1. downscale + re-upscale   (limited camera resolution / blur)
    2. mild JPEG compression    (lossy capture)
    3. +/-5 deg rotation        (page not square to the lens)
    4. brightness gradient      (uneven lighting)

Two metrics per size:
  * COMBINED: ~20 seeded variants that stack ALL FOUR at once with randomized,
    realistic parameters — the honest "what a photo actually does" case.
  * PER-TRANSFORM: each degradation swept alone across its range, to show the
    margin and pinpoint the culprit if the combined rate drops.

GATE: small payloads (short, ~256B) must decode ~100% on the COMBINED metric.
If not, STOP and report — the live webcam in Prompt 3 will be worse, not better.

Run:  .venv/bin/python gate_page.py
"""

from __future__ import annotations

import io
import os
import random
import sys
import tempfile

import numpy as np
from PIL import Image

from compose_page import compose_page, decode_page_image

KEY = bytes(range(32))          # fixed AES-256 key; the gate must not touch key.bin
N_COMBINED = 20                 # ~20 degraded variants per payload size
WHITE = 255

# Realistic-but-mild parameter ranges for the stacked "photograph" simulation.
ROT_RANGE = (-5.0, 5.0)         # degrees (brief: +/-5)
SCALE_RANGE = (0.45, 0.70)      # shrink to 45-70% then back up (resolution loss)
JPEG_RANGE = (45, 80)           # JPEG quality (brief: "mild")
GRAD_LO_RANGE = (0.50, 0.75)    # darkest end of the lighting gradient
GRAD_HI_RANGE = (1.05, 1.30)    # brightest end


# ── individual photograph-simulation transforms (PIL "L" -> PIL "L") ──────────
def t_downscale_upscale(img: Image.Image, factor: float) -> Image.Image:
    """Shrink to `factor` of size then back up — loses fine detail like a camera."""
    w, h = img.size
    small = img.resize((max(1, round(w * factor)), max(1, round(h * factor))),
                       Image.BILINEAR)
    return small.resize((w, h), Image.BILINEAR)


def t_jpeg(img: Image.Image, quality: int) -> Image.Image:
    """Round-trip through JPEG at `quality` — introduces lossy block artifacts."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=int(quality))
    buf.seek(0)
    return Image.open(buf).convert("L")


def t_rotate(img: Image.Image, angle: float) -> Image.Image:
    """Rotate by `angle` degrees on a white background (page not square to lens)."""
    return img.rotate(angle, resample=Image.BILINEAR, expand=True, fillcolor=WHITE)


def t_brightness_gradient(img: Image.Image, lo: float, hi: float,
                          vertical: bool) -> Image.Image:
    """Multiply pixels by a linear lo->hi gradient across the page (uneven light)."""
    arr = np.asarray(img, dtype=np.float32)
    h, w = arr.shape
    ramp = (np.linspace(lo, hi, h, dtype=np.float32)[:, None] if vertical
            else np.linspace(lo, hi, w, dtype=np.float32)[None, :])
    return Image.fromarray(np.clip(arr * ramp, 0, 255).astype(np.uint8), mode="L")


def photograph_simulate(img: Image.Image, rng: random.Random) -> tuple[Image.Image, dict]:
    """Stack all four degradations with randomized realistic params. Returns
    (degraded_image, params) so a failing variant can be reported exactly."""
    p = {
        "grad_lo": round(rng.uniform(*GRAD_LO_RANGE), 3),
        "grad_hi": round(rng.uniform(*GRAD_HI_RANGE), 3),
        "grad_vertical": rng.random() < 0.5,
        "angle": round(rng.uniform(*ROT_RANGE), 2),
        "scale": round(rng.uniform(*SCALE_RANGE), 3),
        "jpeg_q": rng.randint(*JPEG_RANGE),
    }
    out = t_brightness_gradient(img, p["grad_lo"], p["grad_hi"], p["grad_vertical"])
    out = t_rotate(out, p["angle"])
    out = t_downscale_upscale(out, p["scale"])
    out = t_jpeg(out, p["jpeg_q"])
    return out, p


# ── one decode attempt: does the degraded page recover the exact secret? ──────
def _decodes_ok(img: Image.Image, secret: str) -> bool:
    try:
        return decode_page_image(img, key=KEY) == secret
    except Exception:
        return False


# ── per-size gate run ─────────────────────────────────────────────────────────
def run_size(label: str, secret: str, seed: int,
             save_example: str | None = None) -> dict:
    """Compose -> save -> reopen -> baseline + combined + per-transform decode."""
    with tempfile.TemporaryDirectory() as d:
        page_path = os.path.join(d, "page.png")
        compose_page(secret, page_path, key=KEY)
        reopened = Image.open(page_path).convert("L")   # the SAVED, RE-OPENED page

        baseline_ok = _decodes_ok(reopened, secret)

        # COMBINED: N seeded all-four-at-once variants.
        rng = random.Random(seed)
        combined_pass = 0
        first_failure = None
        for i in range(N_COMBINED):
            degraded, params = photograph_simulate(reopened, rng)
            if save_example and i == 0:
                degraded.save(save_example)             # one figure for the report
            if _decodes_ok(degraded, secret):
                combined_pass += 1
            elif first_failure is None:
                first_failure = params

        # PER-TRANSFORM: each degradation alone, swept across its range.
        per_transform = {
            "rotate(deg)": _sweep(reopened, secret,
                                  [-5, -4, -2, 0, 2, 4, 5], t_rotate),
            "downscale(x)": _sweep(reopened, secret,
                                   [0.40, 0.50, 0.60, 0.70, 0.80],
                                   t_downscale_upscale),
            "jpeg(q)": _sweep(reopened, secret,
                              [40, 50, 60, 70, 80, 90], t_jpeg),
            "gradient": _sweep_gradient(reopened, secret),
        }

    return {
        "label": label, "chars": len(secret), "baseline_ok": baseline_ok,
        "combined_pass": combined_pass, "combined_total": N_COMBINED,
        "first_failure": first_failure, "per_transform": per_transform,
    }


def _sweep(img: Image.Image, secret: str, values, transform) -> str:
    """Apply `transform(img, v)` for each v; return 'k/n' success string."""
    ok = sum(_decodes_ok(transform(img, v), secret) for v in values)
    return f"{ok}/{len(values)}"


def _sweep_gradient(img: Image.Image, secret: str) -> str:
    cases = [(0.50, 1.30, False), (0.55, 1.25, True), (0.65, 1.15, False),
             (0.75, 1.10, True), (0.60, 1.20, False)]
    ok = sum(_decodes_ok(t_brightness_gradient(img, lo, hi, v), secret)
             for lo, hi, v in cases)
    return f"{ok}/{len(cases)}"


# ── report ────────────────────────────────────────────────────────────────────
def main() -> int:
    example_dir = os.environ.get("KITSUNE_EXAMPLE_DIR")   # optional: dump a figure
    sizes = [
        ("short", "meet at dawn"),          # small: gated at ~100%
        ("~256B", "x" * 256),               # small: gated at ~100%
        ("~1KB",  "K" * 1024),              # large: reported, not gated
    ]
    small_labels = {"short", "~256B"}

    print("Kitsune Prompt 2 gate - photographed page round-trips (Fork B: QR only)")
    print("Pipeline: compose_page -> SAVE .png -> RE-OPEN -> photograph-simulate -> decode_page")
    print(f"Combined = {N_COMBINED} seeded variants stacking ALL FOUR degradations at once.\n")

    results = []
    for i, (label, secret) in enumerate(sizes):
        example = (os.path.join(example_dir, f"degraded_{label.strip('~')}.png")
                   if example_dir else None)
        results.append(run_size(label, secret, seed=1000 + i, save_example=example))

    hdr = f"{'size':<7} {'chars':>5} {'baseline':>9} {'combined':>10} {'rate':>7}   per-transform (alone, swept)"
    print(hdr)
    print("-" * len(hdr))
    gate_ok = True
    for r in results:
        rate = r["combined_pass"] / r["combined_total"]
        pt = r["per_transform"]
        pt_str = (f"rot {pt['rotate(deg)']}  down {pt['downscale(x)']}  "
                  f"jpeg {pt['jpeg(q)']}  grad {pt['gradient']}")
        print(f"{r['label']:<7} {r['chars']:>5} "
              f"{'ok' if r['baseline_ok'] else 'FAIL':>9} "
              f"{r['combined_pass']:>7}/{r['combined_total']:<2} "
              f"{rate:>6.0%}   {pt_str}")
        if not r["baseline_ok"]:
            gate_ok = False
        if r["label"] in small_labels and r["combined_pass"] < r["combined_total"]:
            gate_ok = False

    print()
    for r in results:
        if r["first_failure"] is not None:
            print(f"  first combined failure @ {r['label']}: {r['first_failure']}")

    print("\n" + "=" * len(hdr))
    if gate_ok:
        print("GATE PASSED - small payloads decode 100% through the photograph "
              "simulation.\nReady to attempt live webcam capture in Prompt 3.")
        rc = 0
    else:
        print("GATE FAILED - a small payload did NOT hit ~100% (or baseline "
              "broke).\nSTOP: fix page/QR robustness before Prompt 3 (live "
              "webcam will be worse).\nUse the per-transform column above to see "
              "which degradation is the culprit.")
        rc = 1
    print("=" * len(hdr))
    return rc


if __name__ == "__main__":
    sys.exit(main())
