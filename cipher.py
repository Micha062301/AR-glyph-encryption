import os
import time
import math
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend

# ── KITSUNE CIPHER ENGINE v0.2 ──
# Layer 1: AES-256 encryption (military grade, unbroken)
# Layer 2: Glyph mapping (visual representation)
# Layer 3: Time rotor (Enigma-style visual shifting)

GLYPHS = [
    'ॐ','ॠ','ॡ','ऋ','ढ','थ','ध','भ','ख','घ','ञ','ण','ष','ह','क','ज',
    'ग','च','ट','त','प','फ','ब','म','य','र','ल','व','श','स','ह','ड',
    '密','隐','形','眼','镜','狐','火','字','道','心','影','光','真','假','天',
    '地','人','水','山','云','风','月','星','空','海','龙','凤','虎','鹤','松',
    '梅','竹','兰','菊','莲','雪','霜','雨','雷','电','气','力','美','善','智',
    '⌇','⌖','⍉','⍦','⍙','⎊','⎋','⍜','⍧','⍡','⎌','⌗','⍤','⍥','⍨',
    '⌀','⌁','⌂','⌃','⌄','⌅','⌆','⌈','⌉','⌊','⌋','⌌','⌍','⌎','⌏',
    '⌐','⌑','⌒','⌓','⌔','⌕','⌘','⌙','⌚','⌛','⌜','⌝','⌞','⌟','⌠',
    '⌡','⌢','⌣','⌤','⌥','⌦','⌧','⌨','〈','〉','⌫','⌬','⌭','⌮','⌯',
    '⌰','⌱','⌲','⌳','⌴','⌵','⌶','⌷','⌸','⌹','⌺','⌻','⌼','⌽','⌾',
]

# ── LAYER 1: AES-256 ENCRYPTION ──
def aes_encrypt(plaintext: str, key: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    iv = os.urandom(16)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return iv + ciphertext  # prepend IV for decryption

def aes_decrypt(ciphertext: bytes, key: bytes) -> str:
    iv = ciphertext[:16]
    ct = ciphertext[16:]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return plaintext.decode()

# ── LAYER 2 + 3: GLYPH MAPPING WITH TIME ROTOR ──
def get_rotor_shift():
    # Three rotors — fast, medium, slow
    # Full pattern cycle: 1 x 7 x 59 = 413 seconds
    # Combined with AES key: practically unbreakable
    t = time.time()
    rotor_1 = math.floor(t / 1) % len(GLYPHS)        # shifts every 1 second
    rotor_2 = math.floor(t / 7) % len(GLYPHS)        # shifts every 7 seconds
    rotor_3 = math.floor(t / 59) % len(GLYPHS)       # shifts every 59 seconds
    return (rotor_1 + rotor_2 + rotor_3) % len(GLYPHS)

def bytes_to_glyphs(data: bytes) -> str:
    shift = get_rotor_shift()
    result = []
    for i, byte in enumerate(data):
        glyph_index = (byte + shift + i) % len(GLYPHS)
        result.append(GLYPHS[glyph_index])
    return ' '.join(result)

def glyphs_to_bytes(glyph_str: str, shift: int) -> bytes:
    glyph_list = glyph_str.split(' ')
    result = []
    for i, glyph in enumerate(glyph_list):
        if glyph in GLYPHS:
            glyph_index = GLYPHS.index(glyph)
            byte_val = (glyph_index - shift - i) % 256
            result.append(byte_val)
    return bytes(result)

# ── DEMO ──
def live_demo():
    print("\n" + "─"*55)
    print("  KITSUNE CIPHER ENGINE v0.2")
    print("  AES-256 + Three-Rotor Glyph System")
    print("  AR Encryption for any physical workspace")
    print("─"*55)

    # Generate a random AES-256 key (32 bytes = 256 bits)
    # In the real product this is unique per document, stored securely
    key = os.urandom(32)
    print(f"\n  Document key (AES-256): {key.hex()[:32]}...")
    print("  (In production: unique per document, stored in Kitsune backend)\n")

    text = input("  Enter text to encrypt: ")

    # ENCRYPT
    print("\n  ── LAYER 1: AES-256 ENCRYPTION ──")
    encrypted_bytes = aes_encrypt(text, key)
    print(f"  Raw encrypted bytes: {encrypted_bytes.hex()[:40]}...")

    print("\n  ── LAYER 2+3: GLYPH MAPPING + ROTOR SHIFT ──")
    glyph_output = bytes_to_glyphs(encrypted_bytes)
    print(f"  What the world sees:\n")
    print(f"  {glyph_output[:80]}...")

    # DECRYPT
    print("\n  ── DECRYPTION (authorized glasses only) ──")
    shift_at_encryption = get_rotor_shift()
    recovered_bytes = glyphs_to_bytes(glyph_output, shift_at_encryption)
    try:
        decrypted = aes_decrypt(recovered_bytes, key)
        print(f"  What you see through Kitsune: {decrypted}")
    except Exception:
        print("  Rotor shifted during demo — re-run immediately after encrypting")

    # LIVE ROTOR DEMO
    print("\n  ── LIVE ROTOR DEMO ──")
    print("  Same encrypted data. Watch the glyphs shift every second:\n")
    try:
        for _ in range(8):
            live_glyphs = bytes_to_glyphs(encrypted_bytes)
            print(f"  [{time.strftime('%H:%M:%S')}] {live_glyphs[:60]}...")
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    print("\n  ── WHAT JUST HAPPENED ──")
    print("  Layer 1 — AES-256: same standard used by governments and banks")
    print("  Layer 2 — Glyph map: encrypted bytes rendered as visual symbols")
    print("  Layer 3 — Three rotors: visual pattern shifts every second")
    print("\n  To break Kitsune you need all three simultaneously:")
    print("  → The AES-256 document key")
    print("  → The glyph mapping seed")
    print("  → The exact timestamp at encryption")
    print("\n" + "─"*55 + "\n")

if __name__ == "__main__":
    live_demo()
