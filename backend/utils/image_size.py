"""Width x height of an uploaded image, read from its header - no imaging library needed (v8d).

Used to refuse an "ID scan" that cannot be one: production holds 13 check-ins (2-4 Oct) whose
front AND back scans are the same 16 x 81 pixel scrap of a screenshot, attached to get past the
"scans required" rule. A card scanned at even 150 dpi is ~500 x 320 pixels.
"""
import struct


def image_size(data: bytes):
    """(width, height), or None when the format is not recognised or the header is cut short."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            w, h = struct.unpack(">II", data[16:24])
            return w, h
        if data[:2] == b"BM" and len(data) >= 26:
            w, h = struct.unpack("<ii", data[18:26])
            return abs(w), abs(h)
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            w, h = struct.unpack("<HH", data[6:10])
            return w, h
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
            chunk = data[12:16]
            if chunk == b"VP8X":
                w = 1 + int.from_bytes(data[24:27], "little")
                h = 1 + int.from_bytes(data[27:30], "little")
                return w, h
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                b = data[21:25]
                w = 1 + (((b[1] & 0x3F) << 8) | b[0])
                h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
                return w, h
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                # SOF0..SOF15 except DHT (C4), JPG (C8), DAC (CC)
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + seg
    except Exception:
        return None
    return None


# Smaller than this cannot be a readable ID card (85.6 x 54 mm at 150 dpi is ~505 x 319).
MIN_LONG_SIDE = 400
MIN_SHORT_SIDE = 250


def too_small_for_an_id(data: bytes, mime: str | None):
    """A refusal message when an image is too small to be an ID scan, else None. PDFs and
    unrecognised formats pass (the type check already limits what reaches here)."""
    if (mime or "").lower() == "application/pdf":
        return None
    size = image_size(data)
    if size is None:
        return None
    w, h = size
    if max(w, h) < MIN_LONG_SIDE or min(w, h) < MIN_SHORT_SIDE:
        return (f"This image is only {w} x {h} pixels - too small to be a scan of an ID. "
                "Scan the card, or attach a clear photo of it.")
    return None
