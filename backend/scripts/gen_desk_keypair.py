"""Generate the keypair that lets the front desk seal ID scans while OFFLINE.

    python backend/scripts/gen_desk_keypair.py

Prints two PEM blocks:
  * the PRIVATE key  -> Railway variable ID_SCAN_DESK_PRIVATE_KEY (paste the whole block,
                        newlines included — Railway's raw editor keeps them)
  * the PUBLIC key   -> for your records only; the server derives and serves it itself

Escrow the private key beside ID_SCAN_ENCRYPTION_KEY. Its blast radius is smaller than that
key's — it protects scans that are queued on the desk and not yet uploaded, and nothing that has
already arrived — but a scan captured during an outage is still a scan you are legally holding.

Run once. Rotating it means: set the new private key, and any envelope still queued on a desk
that fetched the OLD public key will be refused on upload ("sealed for a different desk key")
until that desk re-fetches — which it does on every login.
"""
import hashlib
import sys


def main() -> int:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        print("pip install cryptography", file=sys.stderr)
        return 2

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    priv = key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub_der = key.public_key().public_bytes(serialization.Encoding.DER,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    print("# key_id:", hashlib.sha256(pub_der).digest()[:16].hex())
    print("#")
    print("# ---- ID_SCAN_DESK_PRIVATE_KEY (Railway) — keep secret, escrow offline ----")
    print(priv, end="")
    print("# ---- public key (for reference; the server serves this itself) ----")
    print(pub, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
