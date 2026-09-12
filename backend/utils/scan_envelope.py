"""Open an ID-scan envelope sealed by the front desk while it was OFFLINE.

WHY THIS EXISTS
    A scan is stored Fernet-encrypted with ID_SCAN_ENCRYPTION_KEY, which deliberately never
    leaves the server. So with the internet down the desk could not encrypt, could not upload,
    and — because front+back scans are mandatory — could not check anyone in. Now the desk seals
    the scan with the server's PUBLIC key and queues the result; on sync the server opens it
    here and re-wraps it with Fernet before the first write. Plaintext exists only in this
    request's memory.

    The desk can never open what it sealed. A stolen front-desk PC yields ciphertext.

WIRE FORMAT  (little endian nowhere — the one integer is big-endian)
    "BHS1"        4    magic
    0x01          1    envelope version
    key_id       16    sha256(server public key DER)[:16] — which private key opens this
    wk_len        2    big-endian length of wrapped_key
    wrapped_key   n    RSA-OAEP-SHA256( 32-byte AES key )
    nonce        12    AES-GCM nonce
    tag          16    AES-GCM tag
    ciphertext    *    AES-256-GCM(plaintext), AAD = every byte from magic through nonce

    AAD over the header means a tampered key_id, length or nonce fails the tag, not just the
    ciphertext. The .NET side (ReceptionApp Services/ScanEnvelope.cs) writes exactly this.

KEYS
    ID_SCAN_DESK_PRIVATE_KEY   PKCS#8 PEM. Generate with backend/scripts/gen_desk_keypair.py.
    The public key is derived from it at runtime and served by GET /settings/desk-public-key.

    Blast radius, and it is a small one: this key protects IN-FLIGHT scans only. Everything
    ingested is Fernet from then on, so losing this key loses whatever was queued at that moment
    and nothing that has already arrived. Escrow it beside ID_SCAN_ENCRYPTION_KEY anyway.

Every failure raises ValueError with a short reason. Never a stack trace to the desk.
"""
import hashlib
import logging
import os
import struct

logger = logging.getLogger(__name__)

MAGIC = b"BHS1"
VERSION = 1
_HEADER_FIXED = 4 + 1 + 16 + 2          # up to and including wk_len
_NONCE = 12
_TAG = 16
_MIN_WRAPPED = 256                      # RSA-2048; we issue 3072 (=384) but accept either
_MAX_WRAPPED = 1024

_private = None
_public_pem: str | None = None
_key_id: bytes | None = None


def _load():
    """Load the private key once. Returns False (and logs) when unconfigured/invalid."""
    global _private, _public_pem, _key_id
    if _private is not None:
        return True
    pem = os.getenv("ID_SCAN_DESK_PRIVATE_KEY")
    if not pem:
        return False
    try:
        from cryptography.hazmat.primitives import serialization
        priv = serialization.load_pem_private_key(pem.encode(), password=None)
        pub_der = priv.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        _public_pem = priv.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        _key_id = hashlib.sha256(pub_der).digest()[:16]
        _private = priv
        logger.info("[scan_envelope] desk key loaded, key_id=%s", _key_id.hex())
        return True
    except Exception as e:
        logger.error("[scan_envelope] ID_SCAN_DESK_PRIVATE_KEY is set but invalid: %s", e)
        return False


def is_configured() -> bool:
    return _load()


def public_key() -> dict | None:
    """{"key_id": hex, "public_pem": str} for the desk to fetch and cache. None if unconfigured."""
    if not _load():
        return None
    return {"key_id": _key_id.hex(), "public_pem": _public_pem}


def open_envelope(blob: bytes) -> bytes:
    """Unwrap and decrypt. ValueError on anything that is not a valid envelope for OUR key."""
    if not _load():
        raise ValueError("offline scan ingest is not configured on the server")
    if len(blob) < _HEADER_FIXED + _MIN_WRAPPED + _NONCE + _TAG:
        raise ValueError("envelope too short")
    if blob[:4] != MAGIC:
        raise ValueError("not a scan envelope")
    if blob[4] != VERSION:
        raise ValueError(f"unsupported envelope version {blob[4]}")
    key_id = blob[5:21]
    if key_id != _key_id:
        # The desk sealed this with a key we do not hold — a rotated key, or another server's.
        raise ValueError("envelope was sealed for a different desk key")
    (wk_len,) = struct.unpack(">H", blob[21:23])
    if not (_MIN_WRAPPED <= wk_len <= _MAX_WRAPPED):
        raise ValueError("bad wrapped-key length")
    p = _HEADER_FIXED
    wrapped = blob[p:p + wk_len]; p += wk_len
    nonce = blob[p:p + _NONCE]; p += _NONCE
    tag = blob[p:p + _TAG]; p += _TAG
    ciphertext = blob[p:]
    if len(wrapped) != wk_len or len(nonce) != _NONCE or len(tag) != _TAG or not ciphertext:
        raise ValueError("envelope truncated")
    aad = blob[:_HEADER_FIXED + wk_len + _NONCE]   # magic .. nonce, inclusive

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        aes_key = _private.decrypt(
            wrapped,
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                         algorithm=hashes.SHA256(), label=None))
    except Exception:
        raise ValueError("wrapped key did not unwrap")
    if len(aes_key) != 32:
        raise ValueError("unwrapped key has the wrong size")
    try:
        # cryptography's AESGCM takes ciphertext||tag as one buffer.
        return AESGCM(aes_key).decrypt(nonce, ciphertext + tag, aad)
    except Exception:
        raise ValueError("envelope failed authentication")
