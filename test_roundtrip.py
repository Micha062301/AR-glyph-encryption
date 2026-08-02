"""
test_roundtrip.py — GATE for the Prompt-1 crypto + QR channel.

For a batch of test strings (short, long, unicode, empty, 1 KB, edge cases):

    s -> aes_encrypt -> bytes_to_qr -> qr_to_bytes -> aes_decrypt -> s'

asserts s == s' for every case. Also checks key.bin round-trip,
order-independent multi-chunk reassembly, and — now that the cipher is
AES-256-GCM — that a tampered ciphertext raises on auth failure rather than
decoding to garbage. Exits non-zero if ANY case fails.
"""

import os
import sys
import random
import tempfile
from pathlib import Path

import qr_crypto as qc


def _cases() -> dict[str, str]:
    return {
        "empty": "",
        "single_char": "x",
        "short_ascii": "hello",
        "block_boundary_15": "a" * 15,      # just under one AES block
        "block_boundary_16": "a" * 16,      # exactly one AES block
        "block_boundary_17": "a" * 17,      # just over one AES block
        "long_ascii": "The quick brown fox jumps over the lazy dog. " * 20,
        "unicode_mixed": "日本語 🦊🔐 देवनागरी Ωμέγα café — ✓ ∑ 𝕏",
        "unicode_emoji_heavy": "🦊🔐🗝️🧬🛰️" * 30,
        "whitespace_newlines": "line1\nline2\r\n\ttabbed\n   spaced   ",
        "json_like": '{"secret":"value","n":42,"nested":{"a":[1,2,3]}}',
        "1KB_ascii": "A" * 1024,
        "1KB_unicode": "🦊" * 256,           # 1024 bytes UTF-8
        "2KB_ascii": "Z" * 2048,             # multi-chunk stress
    }


def run_gate() -> int:
    key = os.urandom(qc.KEY_SIZE)            # hermetic in-memory key
    cases = _cases()
    npass = nfail = 0
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        print(f"Running gate: {len(cases)} cases "
              f"(AES-256-GCM + segmented QR + pyzbar binary decode)\n")
        for name, s in cases.items():
            case_dir = Path(tmp) / name
            try:
                paths = qc.encrypt_to_qr(s, case_dir, key=key)
                s2 = qc.decrypt_from_qr(paths, key=key)
                ok = (s == s2)
            except Exception as exc:                       # noqa: BLE001
                ok = False
                s2 = f"<{type(exc).__name__}: {exc}>"
                paths = []
            n_qr = len(paths)
            if ok:
                npass += 1
                print(f"  PASS  {name:22} chars={len(s):<5} qr_pngs={n_qr}")
            else:
                nfail += 1
                failures.append(name)
                print(f"  FAIL  {name:22} chars={len(s):<5} qr_pngs={n_qr}  "
                      f"got={s2[:60]!r}")

        # extra check 1: key.bin is created and reloads identically
        key_ok = _check_key_file(tmp)
        print(f"\n  {'PASS' if key_ok else 'FAIL'}  key.bin create+reload")
        npass += key_ok
        nfail += (not key_ok)
        if not key_ok:
            failures.append("key_file")

        # extra check 2: multi-chunk reassembly is order-independent
        order_ok = _check_order_independence(tmp, key)
        print(f"  {'PASS' if order_ok else 'FAIL'}  multi-chunk reassembly "
              f"(shuffled input order)")
        npass += order_ok
        nfail += (not order_ok)
        if not order_ok:
            failures.append("order_independence")

        # extra check 3: GCM authenticates — a tampered ciphertext must raise on
        # auth failure, not decode to garbage (the whole point of the CBC->GCM swap).
        tamper_ok = _check_tamper_detection(tmp, key)
        print(f"  {'PASS' if tamper_ok else 'FAIL'}  tamper detection "
              f"(altered ciphertext raises on auth failure)")
        npass += tamper_ok
        nfail += (not tamper_ok)
        if not tamper_ok:
            failures.append("tamper_detection")

    total = npass + nfail
    print(f"\n{'=' * 60}")
    print(f"RESULT: {npass}/{total} passed, {nfail} failed")
    if nfail:
        print(f"FAILURES: {', '.join(failures)}")
        print("GATE FAILED — module is NOT done.")
    else:
        print("GATE PASSED — lossless round-trip on every case.")
    print(f"{'=' * 60}")
    return 1 if nfail else 0


def _check_key_file(tmp: str) -> bool:
    kp = Path(tmp) / "key.bin"
    k1 = qc.load_or_create_key(kp)
    k2 = qc.load_or_create_key(kp)
    return kp.exists() and len(k1) == qc.KEY_SIZE and k1 == k2


def _check_order_independence(tmp: str, key: bytes) -> bool:
    s = "reassembly must not depend on filename/order — " * 40
    paths = qc.encrypt_to_qr(s, Path(tmp) / "order", key=key)
    if len(paths) < 2:
        return False                          # need a genuine multi-chunk case
    shuffled = paths[:]
    random.shuffle(shuffled)
    return qc.decrypt_from_qr(shuffled, key=key) == s


def _check_tamper_detection(tmp: str, key: bytes) -> bool:
    """A single altered ciphertext byte must fail GCM auth, not decode to garbage.

    The tampered blob is carried through a genuine QR round-trip, so the QR
    channel decodes it perfectly (lossless) yet AES-256-GCM still rejects it.
    """
    s = "authenticated payload — GCM must reject any tampering"
    blob = qc.aes_encrypt(s, key)
    if qc.aes_decrypt(blob, key) != s:
        return False                          # clean blob must decrypt first

    tampered = bytearray(blob)
    tampered[-1] ^= 0x01                       # flip one ciphertext bit
    paths = qc.bytes_to_qr(bytes(tampered), Path(tmp) / "tamper")
    recovered = qc.qr_to_bytes(paths)
    if recovered != bytes(tampered):
        return False                          # QR channel must be lossless...

    try:
        qc.aes_decrypt(recovered, key)         # ...but auth must reject the tamper
    except qc.InvalidTag:
        return True                            # expected: raised on auth failure
    return False                               # returned/other error -> FAIL


if __name__ == "__main__":
    sys.exit(run_gate())
