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

const Rule = () => <div style={{ borderTop: "1px dashed #000", margin: "4px 0" }} />;

/** Kitchen docket — what to cook, for which room. Deliberately no prices. */
export function KotSheet({ kot, onDone }) {
  if (!kot) return null;
  return (
    <ThermalSheet onDone={onDone}>
      <div style={{ textAlign: "center", fontWeight: "bold", fontSize: "14px" }}>KITCHEN ORDER</div>
      <div style={{ textAlign: "center", fontSize: "16px", fontWeight: "bold", margin: "2px 0" }}>
        {kot.kot_no}
      </div>
      {kot.reprint && (
        <div style={{ textAlign: "center", fontWeight: "bold" }}>*** REPRINT ***</div>
      )}
      <Rule />
      <div style={{ fontSize: "15px", fontWeight: "bold" }}>ROOM {kot.room_number || "—"}</div>
      <div>{dt(kot.placed_at)}</div>
      <div>Source: {kot.source === "portal" ? "Guest (QR)" : "Staff"}</div>
      <Rule />
      {(kot.items || []).map((it, i) => (
        <div key={i} style={{ display: "flex", fontSize: "13px", margin: "2px 0" }}>
          <span style={{ width: "26px", fontWeight: "bold" }}>{it.qty}x</span>
          <span style={{ flex: 1 }}>{it.name}</span>
        </div>
      ))}
      {kot.note && (
        <>
          <Rule />
          <div style={{ fontWeight: "bold" }}>NOTE: {kot.note}</div>
        </>
      )}
      <Rule />
      <div style={{ textAlign: "center" }}>Order #{kot.order_id}</div>
      <div style={{ height: "12mm" }} />
    </ThermalSheet>
  );
}

/** Guest copy of a delivered order. Not a tax invoice — the stay's GST invoice is. */
export function BillSheet({ bill, onDone }) {
  if (!bill) return null;
  return (
    <ThermalSheet onDone={onDone}>
      <div style={{ textAlign: "center", fontWeight: "bold", fontSize: "14px" }}>HOTEL BHIMAS</div>
      <div style={{ textAlign: "center" }}>Room Service</div>
      <Rule />
      <div>Room: <b>{bill.room_number || "—"}</b></div>
      {bill.guest_name && <div>Guest: {bill.guest_name}</div>}
      <div>KOT: {bill.kot_no}</div>
      <div>{dt(bill.delivered_at || bill.placed_at)}</div>
      <Rule />
      {(bill.items || []).map((it, i) => (
        <div key={i} style={{ margin: "2px 0" }}>
          <div>{it.name}</div>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span>{it.qty} x {money(it.unit_price)}</span>
            <span>{money(it.amount)}</span>
          </div>
        </div>
      ))}
      <Rule />
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "13px", fontWeight: "bold" }}>
        <span>TOTAL</span>
        <span>{money(bill.total)}</span>
      </div>
      <div style={{ fontSize: "10px", marginTop: "2px" }}>(inclusive of GST)</div>
      <Rule />
      <div style={{ textAlign: "center", fontWeight: "bold" }}>CHARGED TO ROOM</div>
      <div style={{ textAlign: "center", fontSize: "10px", marginTop: "2px" }}>
        Payable with your room bill at check-out.
        <br />
        This is not a tax invoice.
      </div>
      {bill.reprint && (
        <div style={{ textAlign: "center", fontWeight: "bold", marginTop: "3px" }}>*** REPRINT ***</div>
      )}
      <div style={{ height: "12mm" }} />
    </ThermalSheet>
  );
}
