// Client-side CSV export of already-loaded rows — no backend round-trip.
//   exportCsv("guests.csv", rows, [{ header: "Name", value: (r) => r.name }, ...])
export function exportCsv(filename, rows, columns) {
  const esc = (v) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = columns.map((c) => esc(c.header)).join(",");
  const body = (rows || []).map((r) => columns.map((c) => esc(c.value(r))).join(",")).join("\n");
  // Prepend a UTF-8 BOM so Excel opens ₹ and other non-ASCII correctly.
  const blob = new Blob([`﻿${head}\n${body}`], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
