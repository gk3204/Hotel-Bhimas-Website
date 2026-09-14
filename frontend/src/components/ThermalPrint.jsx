// 80mm thermal-roll printing from the browser (backlog v2 TBC-4).
//
// The tablet prints through its OWN browser — the backend runs in the cloud and cannot reach
// a printer inside the hotel. So the docket is rendered into a hidden block that only exists
// on the printed page, and window.print() sends it to the tablet's default printer.
//
// Setup that makes this one tap instead of three: install the thermal printer as the tablet's
// DEFAULT printer and launch Chrome with `--kiosk-printing`, which makes window.print() go
// straight to it with no dialog. Without that flag it still works — the user just confirms
// the print dialog. Either way it can only reach the tablet's own default printer, which is
// why the KOT and the bill both come out there.
import React, { useEffect } from "react";

const money = (v) =>
  `Rs.${Number(v || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const fmt2 = (v) =>
  Number(v || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const dt = (iso) => {
  if (!iso) return "";
  const [d, t] = String(iso).split("T");
  const [y, m, day] = (d || "").split("-");
  return `${day}-${m}-${y} ${(t || "").slice(0, 5)}`;
};

/**
 * Renders `children` into a print-only 80mm sheet and fires the print dialog once.
 * `onDone` runs after printing so the caller can clear it.
 */
export function ThermalSheet({ children, onDone }) {
  useEffect(() => {
    // Let the browser paint the sheet before printing, or it prints a blank page.
    const id = window.setTimeout(() => {
      window.print();
      if (onDone) onDone();
    }, 120);
    return () => window.clearTimeout(id);
  }, [onDone]);

  return (
    <>
      <style>{`
        @media print {
          /* Everything except the sheet disappears. */
          body * { visibility: hidden !important; }
          #thermal-sheet, #thermal-sheet * { visibility: visible !important; }
          #thermal-sheet {
            position: absolute; left: 0; top: 0;
            width: 72mm;                /* 80mm roll minus the printer's margins */
            color: #000; background: #fff;
            font-family: "Courier New", monospace;
            font-size: 11px; line-height: 1.35;
          }
          @page { size: 80mm auto; margin: 3mm; }
        }
        @media screen { #thermal-sheet { display: none; } }
      `}</style>
      <div id="thermal-sheet">{children}</div>
    </>
  );
}

const Rule = ({ solid }) => (
  <div style={{ borderTop: solid ? "1px solid #000" : "1px dashed #000", margin: "4px 0" }} />
);

// v5i: fixed column grids so numbers line up down the sheet (monospace + right-aligned cells).
const KOT_COLS = "34px 1fr";                    // QTY | ITEM
const BILL_COLS = "1fr 26px 52px 60px";          // ITEM | QTY | RATE | AMT
const Row = ({ cols, cells, bold, size }) => (
  <div style={{ display: "grid", gridTemplateColumns: cols, columnGap: "4px", alignItems: "baseline",
    fontWeight: bold ? "bold" : "normal", fontSize: size || "11px", margin: "1px 0" }}>
    {cells.map((c, i) => (
      <span key={i} style={{ textAlign: i === 0 ? "left" : "right", overflowWrap: "anywhere" }}>{c}</span>
    ))}
  </div>
);
const Kv = ({ k, v, bold, size }) => (
  <div style={{ display: "flex", justifyContent: "space-between", fontWeight: bold ? "bold" : "normal",
    fontSize: size || "11px" }}>
    <span>{k}</span><span>{v}</span>
  </div>
);
const hhmm = () => {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

/** Kitchen docket — what to cook, for which room. Deliberately no prices. */
export function KotSheet({ kot, onDone }) {
  if (!kot) return null;
  return (
    <ThermalSheet onDone={onDone}>
      <div style={{ textAlign: "center", fontWeight: "bold", fontSize: "14px", letterSpacing: "1px" }}>KITCHEN ORDER</div>
      <div style={{ textAlign: "center", fontSize: "17px", fontWeight: "bold", margin: "2px 0" }}>{kot.kot_no}</div>
      {kot.reprint && <div style={{ textAlign: "center", fontWeight: "bold" }}>*** REPRINT ***</div>}
      <Rule solid />
      <div style={{ textAlign: "center", fontSize: "22px", fontWeight: "bold", margin: "3px 0" }}>
        ROOM {kot.room_number || "—"}
      </div>
      <Kv k={dt(kot.placed_at)} v={kot.source === "portal" ? "Guest (QR)" : "Staff"} />
      <Kv k={`Order #${kot.order_id}`} v="" />
      <Rule solid />
      <Row cols={KOT_COLS} cells={["QTY", "ITEM"]} bold size="10px" />
      <Rule />
      {(kot.items || []).map((it, i) => (
        <div key={i} style={{ display: "grid", gridTemplateColumns: KOT_COLS, columnGap: "4px",
          fontSize: "14px", margin: "3px 0", alignItems: "baseline" }}>
          <span style={{ textAlign: "right", fontWeight: "bold" }}>{it.qty}x</span>
          <span style={{ overflowWrap: "anywhere" }}>{it.name}</span>
        </div>
      ))}
      {kot.note && (
        <>
          <Rule />
          <div style={{ fontWeight: "bold", fontSize: "12px", textTransform: "uppercase" }}>NOTE: {kot.note}</div>
        </>
      )}
      <Rule solid />
      <div style={{ textAlign: "center", fontSize: "10px" }}>
        {(kot.items || []).reduce((n, it) => n + (Number(it.qty) || 0), 0)} item(s) · printed {hhmm()}
      </div>
      <div style={{ height: "12mm" }} />
    </ThermalSheet>
  );
}

/** Guest copy of a delivered order. Not a tax invoice — the stay's GST invoice is.
 *  v5i: menu prices are ex-GST; the bill shows Rate x Qty, then GST per slab, then the total. */
export function BillSheet({ bill, onDone }) {
  if (!bill) return null;
  const items = bill.items || [];
  const subtotal = bill.subtotal != null ? bill.subtotal : items.reduce((s, it) => s + (Number(it.amount) || 0), 0);
  const gstRows = bill.gst_rows || [];
  const gstTotal = bill.gst_total != null ? bill.gst_total : gstRows.reduce((s, r) => s + (Number(r.gst) || 0), 0);
  const total = bill.total != null ? bill.total : subtotal + gstTotal;
  return (
    <ThermalSheet onDone={onDone}>
      <div style={{ textAlign: "center", fontWeight: "bold", fontSize: "15px", letterSpacing: "1px" }}>HOTEL BHIMAS</div>
      <div style={{ textAlign: "center", fontSize: "10px" }}>42, G Car Street, Tirupati - 517501</div>
      <div style={{ textAlign: "center", fontWeight: "bold", marginTop: "3px" }}>ROOM SERVICE BILL</div>
      <Rule solid />
      <Kv k={`Room: ${bill.room_number || "—"}`} v={bill.guest_name || ""} bold />
      <Kv k={`KOT: ${bill.kot_no || "—"}`} v={`Bill #${bill.order_id}`} />
      <Kv k={dt(bill.delivered_at || bill.placed_at)} v="" />
      <Rule solid />
      <Row cols={BILL_COLS} cells={["ITEM", "QTY", "RATE", "AMT"]} bold size="10px" />
      <Rule />
      {items.map((it, i) => (
        <Row key={i} cols={BILL_COLS}
          cells={[it.name, Number(it.qty || 0).toLocaleString("en-IN"), fmt2(it.unit_price), fmt2(it.amount)]} />
      ))}
      <Rule />
      <Kv k="Subtotal (excl. GST)" v={money(subtotal)} />
      {gstRows.map((r, i) => (
        <React.Fragment key={i}>
          <Kv k={`CGST ${(r.percent / 2)}%`} v={money(r.cgst)} size="10px" />
          <Kv k={`SGST ${(r.percent / 2)}%`} v={money(r.sgst)} size="10px" />
        </React.Fragment>
      ))}
      {gstRows.length === 0 && gstTotal > 0 && <Kv k="GST" v={money(gstTotal)} size="10px" />}
      <Rule solid />
      <Kv k="TOTAL" v={money(total)} bold size="14px" />
      <Rule solid />
      <div style={{ textAlign: "center", fontWeight: "bold" }}>CHARGED TO ROOM</div>
      <div style={{ textAlign: "center", fontSize: "10px", marginTop: "2px" }}>
        Payable with your room bill at check-out.
        <br />
        This is not a tax invoice — GST is shown on the stay's invoice.
      </div>
      {bill.reprint && (
        <div style={{ textAlign: "center", fontWeight: "bold", marginTop: "3px" }}>*** REPRINT ***</div>
      )}
      <div style={{ textAlign: "center", fontSize: "10px", marginTop: "4px" }}>Thank you — Hotel Bhimas</div>
      <div style={{ height: "12mm" }} />
    </ThermalSheet>
  );
}
