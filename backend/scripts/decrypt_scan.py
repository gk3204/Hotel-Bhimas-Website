"""Decrypt one archived ID scan, using only the escrowed key.

    python decrypt_scan.py <path-to.enc> [-o out.jpg]
    # key is read from stdin, or from ID_SCAN_ENCRYPTION_KEY if set

WHY THIS EXISTS AS A STANDALONE SCRIPT
    The weekly archive on the owner's PC is Fernet ciphertext and the key deliberately is NOT
    on that machine — that is the property that makes it safe to store there. Retrieving one
    scan must therefore be possible with the key from offline escrow and nothing else: no
    backend, no database, no network, no dependency beyond `cryptography`.

    A backup you have never restored is a rumour. Run this against one member of a real
    archive before you ever let anything delete from object storage, and once a quarter after.

Members inside `bhimas-scans-*.zip` sit at their ref path (`booking_<id>/<uuid>.enc`), so:
    unzip -j bhimas-scans-2026-09-10_0300.zip "booking_101/abc....enc"
    python decrypt_scan.py abc....enc -o recovered.jpg
"""
import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="Decrypt one archived ID scan (.enc).")
    ap.add_argument("path", help="the .enc file, extracted from a scan archive zip")
    ap.add_argument("-o", "--out", help="write plaintext here (default: alongside, without .enc)")
    args = ap.parse_args()

    if not os.path.isfile(args.path):
        print(f"No such file: {args.path}", file=sys.stderr)
        return 2

    key = os.getenv("ID_SCAN_ENCRYPTION_KEY")
    if not key:
        if sys.stdin.isatty():
            print("Paste ID_SCAN_ENCRYPTION_KEY (from offline escrow), then Enter:", file=sys.stderr)
        key = sys.stdin.readline().strip()
    if not key:
        print("No key supplied. Without it this file is permanently unreadable.", file=sys.stderr)
        return 2

    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:
        print("Install the dependency first:  pip install cryptography", file=sys.stderr)
        return 2

    with open(args.path, "rb") as fh:
        blob = fh.read()

    try:
        plaintext = Fernet(key.encode()).decrypt(blob)
    except InvalidToken:
        print("Wrong key, or the file is corrupt. Nothing written.", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"That key is not a valid Fernet key: {e}", file=sys.stderr)
        return 3

    out = args.out or (args.path[:-4] if args.path.endswith(".enc") else args.path + ".out")
    with open(out, "wb") as fh:
        fh.write(plaintext)
    print(f"Wrote {len(plaintext)} bytes to {out}")
    print("This is an unencrypted government ID. Delete it when you are done with it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
