"""Srun SRBX1 wire format, implemented as standalone pure functions."""

import base64
import hashlib
import hmac
import json
import re
import struct


_ALPHABET = b"LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"
_TRANSLATION = bytes.maketrans(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", _ALPHABET
)
_MASK = 0xFFFFFFFF


class ProtocolError(ValueError):
    pass


def parse_reply(text: str, callback: str | None = None) -> dict:
    """Accept JSON or a single JSONP invocation, never evaluate JavaScript."""
    text = text.strip().lstrip("\ufeff").strip()
    if not text.startswith("{"):
        match = re.fullmatch(r"([\w.$]+)\s*\((.*)\)\s*;?", text, re.S)
        if not match or (callback and match[1] != callback):
            raise ProtocolError("Invalid JSONP reply.")
        text = match[2]
    try:
        value = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise ProtocolError("Invalid JSON reply.") from exc
    if not isinstance(value, dict):
        raise ProtocolError("Invalid reply type.")
    return value


def encode_info(data: bytes, token: str) -> str:
    """Srun's modified block-TEA (not standard XXTEA), little-endian words."""
    if not data:
        return "{SRBX1}"
    padded = data + bytes(-len(data) % 4)
    words = list(struct.unpack(f"<{len(padded) // 4}I", padded)) + [len(data)]
    key_bytes = token.encode("ascii")[:16].ljust(16, b"\0")
    key = struct.unpack("<4I", key_bytes)
    total = 0
    previous = words[-1]
    for _ in range(6 + 52 // len(words)):
        total = (total + 0x9E3779B9) & _MASK
        selector = (total >> 2) & 3
        for index in range(len(words)):
            following = words[(index + 1) % len(words)]
            mix = (previous >> 5) ^ (following << 2)
            mix += ((following >> 3) ^ (previous << 4)) ^ (total ^ following)
            mix += key[(index & 3) ^ selector] ^ previous
            previous = words[index] = (words[index] + mix) & _MASK
    encrypted = struct.pack(f"<{len(words)}I", *words)
    return "{SRBX1}" + base64.b64encode(encrypted).translate(_TRANSLATION).decode("ascii")


def login_parameters(username: str, password: str, ip: str, ac_id: str,
                     token: str, password_hmac: bool = False) -> dict[str, str]:
    """Support HMAC-MD5(token, empty) and password-based HMAC portal variants."""
    document = json.dumps(
        {"username": username, "password": password, "ip": ip,
         "acid": ac_id, "enc_ver": "srun_bx1"},
        separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    info = encode_info(document, token)
    digest = hmac.new(token.encode("ascii"),
                      password.encode("utf-8") if password_hmac else b"",
                      hashlib.md5).hexdigest()
    parts = (username, digest, ac_id, ip, "200", "1", info)
    checksum = hashlib.sha1("".join(token + part for part in parts).encode("utf-8")).hexdigest()
    return {
        "action": "login", "username": username, "password": "{MD5}" + digest,
        "ac_id": ac_id, "ip": ip, "n": "200", "type": "1", "double_stack": "0",
        "os": "Windows", "name": "Python", "info": info, "chksum": checksum,
    }
