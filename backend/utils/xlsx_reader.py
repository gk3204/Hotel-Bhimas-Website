"""Read the first sheet of an .xlsx into a list of rows - standard library only (zip + XML).

P1 batch 3: Go-MMT's TDS / TCS / commission statements arrive as small .xlsx attachments. openpyxl is
not a dependency of this backend and is not needed for reading plain cell values, so this reads the
workbook's shared strings and first worksheet directly. Numbers come back as floats, text as str;
formulas give their cached value. Dates in these statements are text ("12/07/2026", "01/Aug/2026"),
so no date-serial handling is attempted.
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _first_sheet_path(z: zipfile.ZipFile) -> str:
    """The first sheet in workbook order (not simply sheet1.xml, which a re-saved file may not have)."""
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        first = wb.find(f"{_NS}sheets/{_NS}sheet")
        rid = first.get(f"{_REL_NS}id") if first is not None else None
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        for r in rels:
            if r.get("Id") == rid:
                target = r.get("Target").lstrip("/")
                return target if target.startswith("xl/") else "xl/" + target
    except (KeyError, ET.ParseError, AttributeError):
        pass
    sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    if not sheets:
        raise ValueError("no worksheet in the file")
    return sheets[0]


def read_rows(data: bytes) -> list[list]:
    """Every row of the first sheet as a list (missing cells are None), in sheet order."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{_NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
        sheet = ET.fromstring(z.read(_first_sheet_path(z)))
    out = []
    for row in sheet.iter(f"{_NS}row"):
        cells = {}
        for c in row.iter(f"{_NS}c"):
            t, v = c.get("t"), c.find(f"{_NS}v")
            if t == "inlineStr":
                val = "".join(e.text or "" for e in c.iter(f"{_NS}t"))
            elif v is None or v.text is None:
                continue
            elif t == "s":
                val = shared[int(v.text)]
            elif t in ("str", "e"):
                val = v.text
            elif t == "b":
                val = v.text == "1"
            else:
                try:
                    val = float(v.text)
                except ValueError:
                    val = v.text
            cells[_col_index(c.get("r"))] = val
        width = max(cells) + 1 if cells else 0
        out.append([cells.get(i) for i in range(width)])
    return out


def read_dicts(data: bytes) -> list[dict]:
    """Rows of the first sheet keyed by the header row's text (first row with any value)."""
    rows = [r for r in read_rows(data) if any(x not in (None, "") for x in r)]
    if not rows:
        return []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    return [{header[i]: (r[i] if i < len(r) else None) for i in range(len(header)) if header[i]}
            for r in rows[1:]]
