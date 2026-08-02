# INTEGRATION_PLAN.md — `mu` (cipher) × `gs` (scanner)

**Status: PHASE 1 — investigate + plan only. No production code written, no
`integration` branch, no commits. Awaiting approval before Phase 2.**

Repo: `Micha062301/AR-glyph-encryption` · local clone
`/Users/mukundswaroop/ar - github/AR-glyph-encryption`
Verified against: `git show mu:cipher.py`, `git show gs:scanner.py`, run under an
isolated venv with `cryptography 49.0.0`, Python 3.13. Every number below comes
from executing the **unmodified** `mu:cipher.py`, not from the code comments and
not from any prior plan. (A prior, uncommitted `INTEGRATION_PLAN.md` was in the
working tree, written against the older "AR overlay" brief; it has been replaced by
this one — see note at the end.)

---

## 0. TL;DR

1. **The `mu` cipher does not round-trip its own output — not even close, and not
   even under best-case assumptions it doesn't actually meet.** Over every
   printable-ASCII input, with the AES key *and* rotor shift captured perfectly
   (which the real code never does), **101 of 101 inputs fail** — each throws on AES
   unpad because the bytes are already corrupted before AES ever sees them. The
   Phase-2 GATE (`plaintext → encrypt → decrypt → assert equal`) **cannot pass**
   against `cipher.py` as written. Fixing this is a **rewrite of the cipher's core
   domain**, not a one-line patch.

2. **`cipher.py` is not the "simple rotor cipher" the brief describes.** It is
   branded "KITSUNE CIPHER ENGINE v0.2" and does **AES-256-CBC → byte→glyph map →
   time rotor**. Its output alphabet is a **152-symbol Unicode glyph table**
   (Devanagari + CJK + APL/technical symbols), *not* letters. The comments claim
   "military grade, unbroken… practically unbreakable" — exactly the overstatement
   you warned about.

3. **`gs` is a single-shot scanner, confirmed.** Webcam → one still on Space →
   GLM-OCR → `output.txt`. No live loop, no overlay, no draw-back. Its OCR function
   *can* be pointed at a PNG file directly (good — that is the Phase-2 second gate),
   but it needs a small refactor to be importable and to return text.

**This is a "fix the cipher + build two thin scripts around `gs`'s OCR" problem,
not a merge problem.** (The branches merge cleanly; that was never the issue.)

---

## 1. What each file ACTUALLY does (verified, not doc claims)

### 1a. `mu:cipher.py` — "KITSUNE CIPHER ENGINE v0.2"

| Piece | What it really does |
|---|---|
| `GLYPHS` | **152 Unicode glyphs, only 151 unique** — `'ह'` is at **index 13 and index 30** (duplicate). Devanagari + CJK + technical/APL symbols. |
| `aes_encrypt(plaintext:str, key:bytes)->bytes` | Real AES-256-CBC. PKCS7 pad, random 16-byte IV, returns `iv + ciphertext`. Reversible **in isolation**. |
| `aes_decrypt(ciphertext:bytes, key:bytes)->str` | Strips IV, decrypts, PKCS7 unpad, utf-8 decode. Throws `ValueError` on bad padding / wrong key / corrupted bytes. |
| `get_rotor_shift()->int` | Reads `time.time()`; `(floor(t/1)+floor(t/7)+floor(t/59)) % 152`. **Non-deterministic** (wall clock). |
| `bytes_to_glyphs(data:bytes)->str` | Calls `get_rotor_shift()` **internally**; per byte `i`: `GLYPHS[(byte + shift + i) % 152]`; space-joins. **Does not return or store the shift it used.** |
| `glyphs_to_bytes(glyph_str:str, shift:int)->bytes` | Splits on `' '`; per glyph `(GLYPHS.index(glyph) - shift - i) % 256`. **Silently drops** any token not in `GLYPHS`. Modulus: **encode `% 152`, decode `% 256`.** |
| `live_demo()` | Interactive CLI under `__main__`. Makes a throwaway `os.urandom(32)` key, encrypts, then calls `get_rotor_shift()` **a second time** and hopes it still matches. |

Dependency: `cryptography` — **undeclared** (no `requirements.txt` on `mu`).

### 1b. `gs:scanner.py` — webcam scanner + GLM-OCR

| Piece | What it really does |
|---|---|
| `check_dependencies()` | **Runs at module import time** and `sys.exit(1)` if `cv2/torch/torchvision/PIL/transformers` are missing → hostile to `import`. |
| `capture_image(filename)->bool` | `cv2.VideoCapture(0)`, preview window, captures **one** frame on Space (key 32), Esc cancels. Writes the JPG, returns a **bool** (not the frame/path). Blocks on a GUI window. |
| `extract_text_and_save(image_path, output_txt)` | Loads `zai-org/GLM-OCR` (~2.6 GB), OCRs the **image path given**, writes `output.txt`, prints a preview. **Returns `None`.** Reloads the model every call. |
| `__main__` | `capture_image()` → if captured, `extract_text_and_save()`. Single shot. No loop, no overlay. |

Deps in `requirements.txt` (torch ≥2.10, transformers ≥5.12, opencv, pillow, …).
Windows-oriented README (`.\ARvenv\Scripts\Activate.ps1`).

---

## 2. Empirical reversibility results (the core question)

Method: imported the unmodified `mu:cipher.py` and drove its own functions. Harness
kept out of the repo (scratchpad `verify.py`).

**Test A — byte → glyph → byte (the layer the brief flags):**
- **shift = 0, position 0:** **105 / 256** byte values fail. First failure = byte
  **30** (duplicate-glyph victim); the rest are bytes ≥ 152, which wrap `% 152` on
  encode but are rebuilt `% 256` on decode.
- **all shifts 0–151 swept, position 0:** **27 315 / 38 912 = 70.2%** of
  (byte, shift) pairs fail — i.e. even knowing the shift exactly, ~70% of raw byte
  values are unrecoverable. (This ~70% average is why the earlier plan quoted
  "≈181/256"; the exact count depends on the shift.)
- **duplicate-glyph loss, isolated:** bytes **30** and **182** both map to the second
  `'ह'`; `GLYPHS.index()` returns the first, so both decode as byte 13.

**Test B — full real pipeline, best case:**
`plaintext → aes_encrypt → bytes_to_glyphs → glyphs_to_bytes(shift) → aes_decrypt`,
with the AES key **and** exact rotor shift **captured** (better than the real code,
which captures neither).
- **101 / 101** printable-ASCII inputs FAIL exact round-trip.
- Every failure is a `ValueError` on AES unpad: the Test-A byte corruption guarantees
  invalid PKCS7 padding, so AES decrypt cannot even return.

**Test C — the time rotor:**
- Shift at `t0` = 117; at `t0 + 1s` = 119. One second of drift moves every decoded
  byte by 254 (mod 256). The shift is **never stored or transmitted** in `cipher.py`,
  so the real `live_demo` only works if encode and decode land in the same rotor tick
  by luck.

**Conclusion:** the cipher is not "slightly lossy." End-to-end it recovers **zero**
inputs. The failure is over-determined by four independent defects (§3b).

---

## 3. The explicit questions

### (a) Output alphabet: letters, or the 152-symbol glyph table? Minimum change to emit A–Z only?

**It is the 152-symbol glyph table** — Devanagari/CJK/technical Unicode, not letters,
not A–Z.

Emitting **A–Z only** is *not* just swapping the table: 26 < 256 makes the domain
squeeze **worse**. To emit A–Z **and** be reversible you must change the *domain*,
not just the alphabet. Two honest options:

- **(Recommended, matches your Phase-2 target)** Stop enciphering bytes. Run the rotor
  directly on **letters A–Z** — a Caesar/Vigenère-style bijection on `Z₂₆`: letters
  in, letters out, `% 26` on both sides, position-dependent shift. Small, clean,
  trivially reversible. Case/punctuation handling is a defined choice (strip to A–Z,
  or escape).
- To carry arbitrary bytes through A–Z instead, use **base-26 expansion** (2 letters
  per byte, 26² = 676 ≥ 256). Lossless, but a longer page.

The current AES layer is incompatible with an A–Z page and, per your scope, should be
**dropped** for the MVP.

### (b) Is the rotor invertible in principle, or does the design lose information?

**The rotor arithmetic itself is invertible in principle** — add a shift mod N,
subtract it: a bijection on `Z_N`. The loss is *not* the rotor; it is four separate
things — two **design**, two **bugs**:

1. **Design loss (fundamental):** a **256-value** byte domain onto a **152-glyph**
   codomain cannot be injective (pigeonhole) — ~104 byte values per shift collapse.
   No shift value fixes this.
2. **Design loss:** the AES key and rotor shift are **never persisted**, so decode
   cannot reproduce the transform even where the math would allow it.
3. **Bug:** modulus mismatch — encode `% 152`, decode `% 256`.
4. **Bug:** duplicate glyph `'ह'` collapses two indices to one.

So the *concept* (a rotor over a fixed alphabet) is invertible; **this implementation
loses information by construction** and stays lossy even after fixing bugs 3–4, until
the domain (1) and key persistence (2) are fixed. Your Phase-2 A–Z design fixes (1) by
construction (26→26) and (2) via the header line.

### (c) Where does the key/rotor setting come from, and where must it be stored?

Two secrets today, **neither stored**:
- **AES key:** `os.urandom(32)`, fresh every run, discarded at process exit.
- **Rotor shift:** from `time.time()` at encode, **recomputed** at decode (so it
  drifts), never written down.

For `decrypt.py` to work, the rotor setting must travel **with the ciphertext** — per
your Phase-2 design, the **plaintext header line on the page** (`KEY: AXQ`). The AES
key problem disappears because AES is dropped for the MVP. No key management, no crypto
hygiene — and that limitation gets stated plainly in your report.

### (d) Can `gs`'s two functions be imported as a library, or only run as a script?

**Only as a script, without a small refactor.** Three blockers:
1. `check_dependencies()` runs at **import time** and `sys.exit(1)` — importing
   `scanner.py` where any heavy dep is missing kills the caller.
2. `extract_text_and_save()` **returns `None`** and only writes `output.txt`; a caller
   must re-read the file (or the function must `return output_text`).
3. `capture_image()` returns a **bool** and blocks on a GUI window; it doesn't hand
   back the frame/path (the filename is known, though).

**Good news for the gates:** `extract_text_and_save(image_path, …)` already takes an
**image path**, so Phase-2's second gate (rendered PNG → OCR, bypass webcam) works
today. Clean fix: guard `check_dependencies()` under `__main__` (or make it
non-fatal), have the OCR function `return` the text, and optionally accept a
pre-loaded model so a loop doesn't reload 2.6 GB per frame.

---

## 4. Gap analysis vs the Phase-2 target

| Phase-2 needs | Exists today? | Work |
|---|---|---|
| `encrypt.py`: text → rotor → A–Z 5-letter groups | **No** — cipher emits glyphs, lossy | **Rewrite** cipher core to an A–Z bijection |
| Reversible round-trip (GATE 1) | **No** — 101/101 fail | Falls out of the rewrite; lock with a test |
| Rotor setting on the page (`KEY: AXQ`) | **No** — shift from clock, unstored | Add header emit/parse |
| Render A–Z page → PNG, monospace, high-contrast | **No** | New (Pillow — already a `gs` dep) |
| `decrypt.py`: capture → OCR → normalize → decrypt | Partial — `gs` OCR reusable | Refactor `gs` (§3d) + normalize + decrypt |
| OCR normalize (uppercase, strip non-A–Z, re-chunk 5s) | **No** | New, small |
| Feed PNG directly to OCR (GATE 2) | **Yes** (path arg) | Just call it |

---

## 5. Ranked — what is most likely to break

1. **The cipher rewrite silently stays lossy.** Highest risk. A residual `%` mismatch,
   an off-by-one on the position index, or asymmetric case/punctuation handling
   re-introduces loss. Mitigation: GATE 1 over **full printable ASCII** must be green
   before anything else; it *is* the cipher's definition of done.
2. **OCR ambiguity within A–Z.** A perfect cipher still dies if OCR confuses letters.
   `O`/`0` is designed out (no digits) but `B/8`, `S/5`, and shape-confusions among
   letters remain under a real camera. Mitigation: GATE 2 (PNG→OCR) measures
   character-error-rate before any camera; the monospace/high-contrast render is the
   biggest lever.
3. **Rotor-setting survival through OCR.** The `KEY: AXQ` header must OCR exactly; one
   wrong letter turns the entire body to garbage with no partial failure. Mitigation:
   same robust font for the header; fold it into GATE 2's CER.
4. **Length/desync from OCR whitespace.** `glyphs_to_bytes` splits on spaces and
   silently drops unknown tokens; the new decoder must **not** inherit that — a
   dropped/split group shifts every later position index and cascades. Mitigation:
   normalize by stripping **all** non-A–Z then re-chunking to 5s, as specified; never
   trust OCR spacing.
5. **`gs` import side effects / model reload.** `sys.exit` at import and a 2.6 GB
   reload per call. Mitigation: the §3d refactor; load the model once.
6. **Environment drift.** `gs` wants torch ≥2.10 / transformers ≥5.12 and ~4 GB;
   README is PowerShell/Windows. `tesseract` is absent locally but not needed
   (GLM-OCR is the OCR path). Low correctness risk, real setup risk.

---

## 6. Decisions needed before Phase 2 (I will not pick these)

1. **Cipher domain:** confirm **A–Z rotor bijection, AES dropped** (my read of your
   brief) vs. base-26 byte expansion. I recommend the A–Z bijection.
2. **Non-letter handling:** plaintext has spaces, punctuation, digits, case. For an
   A–Z-only page, **strip to uppercase A–Z and lose the rest** (simplest MVP) or
   preserve via an escape scheme? I recommend strip-and-uppercase, stated as a
   limitation.
3. **Rotor-setting format:** `KEY: AXQ` — how many letters (rotor count), one fixed
   shift or position-stepping? This sets the header parser.
4. **Refactor `gs` in place vs. adapter:** edit `scanner.py` (guard the dep check,
   return text) vs. wrap it from `decrypt.py`. I recommend a minimal in-place edit.

---

*Note on the prior plan:* the working tree already held an `INTEGRATION_PLAN.md` from
an earlier session, written against the older "AR overlay / anchoring / draw-back"
brief that your current CONTEXT rules out of scope, and quoting the cipher loss as
"≈181/256" (the cross-shift average). This file replaces it with results verified
against the current, deflated MVP scope and reports exact per-condition counts. The
prior file was uncommitted; nothing in git history was touched.

---

**STOP — awaiting your approval of this plan before any Phase 2 work.**
