"""Small dependency-free AES decoder for Legado JavaScript bridges.

Legado exposes Android/JCA symmetric-crypto helpers to source scripts.  The
viewer otherwise has no crypto dependency, so the narrow AES decrypt path used
by source rules lives here instead of making every installation pull in a
large optional package.
"""
from __future__ import annotations


_SBOX = (
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
)
_INV_SBOX = tuple(_SBOX.index(value) for value in range(256))


def _xtime(value: int) -> int:
    return ((value << 1) ^ (0x11B if value & 0x80 else 0)) & 0xFF


def _multiply(left: int, right: int) -> int:
    result = 0
    while right:
        if right & 1:
            result ^= left
        left = _xtime(left)
        right >>= 1
    return result


def _expand_key(key: bytes) -> tuple[bytes, int]:
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key must contain 16, 24, or 32 bytes")
    nk = len(key) // 4
    rounds = nk + 6
    words = [list(key[pos:pos + 4]) for pos in range(0, len(key), 4)]
    rcon = 1
    for index in range(nk, 4 * (rounds + 1)):
        temp = words[index - 1][:]
        if index % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[value] for value in temp]
            temp[0] ^= rcon
            rcon = _xtime(rcon)
        elif nk > 6 and index % nk == 4:
            temp = [_SBOX[value] for value in temp]
        words.append([words[index - nk][i] ^ temp[i] for i in range(4)])
    return bytes(value for word in words for value in word), rounds


def _add_round_key(state: list[int], expanded: bytes, round_no: int) -> None:
    offset = round_no * 16
    for index in range(16):
        state[index] ^= expanded[offset + index]


def _inv_shift_rows(state: list[int]) -> None:
    original = state[:]
    for row in range(1, 4):
        for column in range(4):
            state[row + 4 * column] = original[row + 4 * ((column - row) % 4)]


def _inv_mix_columns(state: list[int]) -> None:
    for offset in range(0, 16, 4):
        a, b, c, d = state[offset:offset + 4]
        state[offset] = _multiply(a, 14) ^ _multiply(b, 11) ^ _multiply(c, 13) ^ _multiply(d, 9)
        state[offset + 1] = _multiply(a, 9) ^ _multiply(b, 14) ^ _multiply(c, 11) ^ _multiply(d, 13)
        state[offset + 2] = _multiply(a, 13) ^ _multiply(b, 9) ^ _multiply(c, 14) ^ _multiply(d, 11)
        state[offset + 3] = _multiply(a, 11) ^ _multiply(b, 13) ^ _multiply(c, 9) ^ _multiply(d, 14)


def _shift_rows(state: list[int]) -> None:
    original = state[:]
    for row in range(1, 4):
        for column in range(4):
            state[row + 4 * column] = original[row + 4 * ((column + row) % 4)]


def _mix_columns(state: list[int]) -> None:
    for offset in range(0, 16, 4):
        a, b, c, d = state[offset:offset + 4]
        state[offset] = _multiply(a, 2) ^ _multiply(b, 3) ^ c ^ d
        state[offset + 1] = a ^ _multiply(b, 2) ^ _multiply(c, 3) ^ d
        state[offset + 2] = a ^ b ^ _multiply(c, 2) ^ _multiply(d, 3)
        state[offset + 3] = _multiply(a, 3) ^ b ^ c ^ _multiply(d, 2)


def _decrypt_block(block: bytes, expanded: bytes, rounds: int) -> bytes:
    if len(block) != 16:
        raise ValueError("AES blocks must contain exactly 16 bytes")
    state = list(block)
    _add_round_key(state, expanded, rounds)
    for round_no in range(rounds - 1, 0, -1):
        _inv_shift_rows(state)
        state[:] = [_INV_SBOX[value] for value in state]
        _add_round_key(state, expanded, round_no)
        _inv_mix_columns(state)
    _inv_shift_rows(state)
    state[:] = [_INV_SBOX[value] for value in state]
    _add_round_key(state, expanded, 0)
    return bytes(state)


def _encrypt_block(block: bytes, expanded: bytes, rounds: int) -> bytes:
    if len(block) != 16:
        raise ValueError("AES blocks must contain exactly 16 bytes")
    state = list(block)
    _add_round_key(state, expanded, 0)
    for round_no in range(1, rounds):
        state[:] = [_SBOX[value] for value in state]
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, expanded, round_no)
    state[:] = [_SBOX[value] for value in state]
    _shift_rows(state)
    _add_round_key(state, expanded, rounds)
    return bytes(state)


def decrypt(data: bytes, key: bytes, *, mode: str = "ECB", iv: bytes = b"",
            unpad: bool = True) -> bytes:
    """Decrypt AES ECB/CBC data and optionally remove PKCS#5/#7 padding."""
    if not data or len(data) % 16:
        raise ValueError("AES ciphertext must be a non-empty multiple of 16 bytes")
    mode = mode.upper()
    if mode not in {"ECB", "CBC"}:
        raise ValueError(f"Unsupported AES mode: {mode}")
    if mode == "CBC" and len(iv) != 16:
        raise ValueError("AES CBC IV must contain exactly 16 bytes")

    expanded, rounds = _expand_key(key)
    plain = bytearray()
    previous = iv
    for offset in range(0, len(data), 16):
        cipher_block = data[offset:offset + 16]
        block = _decrypt_block(cipher_block, expanded, rounds)
        if mode == "CBC":
            block = bytes(left ^ right for left, right in zip(block, previous))
            previous = cipher_block
        plain.extend(block)

    if unpad:
        size = plain[-1]
        if not 1 <= size <= 16 or plain[-size:] != bytes([size]) * size:
            raise ValueError("Invalid PKCS padding")
        del plain[-size:]
    return bytes(plain)


def encrypt(data: bytes, key: bytes, *, mode: str = "ECB", iv: bytes = b"",
            pad: bool = True) -> bytes:
    """Encrypt AES ECB/CBC data and optionally add PKCS#5/#7 padding."""
    mode = mode.upper()
    if mode not in {"ECB", "CBC"}:
        raise ValueError(f"Unsupported AES mode: {mode}")
    if mode == "CBC" and len(iv) != 16:
        raise ValueError("AES CBC IV must contain exactly 16 bytes")
    if pad:
        size = 16 - len(data) % 16
        data += bytes([size]) * size
    if not data or len(data) % 16:
        raise ValueError("AES plaintext must be a non-empty multiple of 16 bytes")

    expanded, rounds = _expand_key(key)
    cipher = bytearray()
    previous = iv
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16]
        if mode == "CBC":
            block = bytes(left ^ right for left, right in zip(block, previous))
        encrypted = _encrypt_block(block, expanded, rounds)
        cipher.extend(encrypted)
        previous = encrypted
    return bytes(cipher)
