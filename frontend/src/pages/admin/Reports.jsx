import React, { useEffect, useMemo, useState } from "react";
import { FaSearch, FaFileCsv, FaFilePdf, FaChartBar } from "react-icons/fa";
import * as api from "../../api/reports";
import { usePaged, Paginator } from "../../components/admin/Paginator";
import { PageShell } from "../../components/admin/BackofficeUI";

const fmt = (n) =>
  `₹${Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
// v7s (owner): "in the cashier summary show it as DUE, not balance" - a pay-later bill's unpaid amount
const balCell = (x) => (x.due ? `DUE ${fmt(x.due)}` : fmt(x.balance));

const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
};

// Each report: how to fetch it, its API path (for export), and how to project its JSON into a
// (columns, rows, totals) table — matching the backend's CSV/PDF projection so all three agree.
const REPORTS = {
  occupancy: {
    label: "Occupancy",
    path: "occupancy",
    range: true,
    groupBy: true,
    fetch: (p) => api.getOccupancy(p),
    project: (d) =>
      d.group_by === "room_type"
        ? {
            columns: ["Room Type", "Capacity (room-nights)", "Sold", "Occupancy %"],
            rows: d.rows.map((r) => [r.room_type, r.capacity_room_nights, r.room_nights_sold, `${r.occupancy_pct}%`]),
          }
        : {
            columns: ["Date", "Occupied", "Available", "Total Rooms", "Occupancy %"],
            rows: d.rows.map((r) => [r.date, r.occupied, r.available, r.total_rooms, `${r.occupancy_pct}%`]),
          },
  },
  "occupancy-analysis": {
    label: "Occupancy Analysis",
    path: "occupancy-analysis",
    dateParam: "as_on",
    note: "Snapshot of every occupied room 'as on' the chosen date, with each stay's prorated revenue. Segments are our booking sources (direct / website / each OTA / agent / complimentary).",
    fetch: (p) => api.getOccupancyAnalysis(p),
    project: (d) => {
      const rows = d.rooms.map((r) => [r.floor, r.room_no, r.guest, r.segment, r.room_type,
        (r.arrival || "").replace("T", " ").slice(0, 16), (r.departure || "").replace("T", " ").slice(0, 16),
        r.pax || "", r.discount_pct ? `${r.discount_pct}%` : "", fmt(r.rack), fmt(r.room_rent),
        fmt(r.discount), fmt(r.gross_rent), fmt(r.gst), fmt(r.net)]);
      const block = (title, items, keyLabel) => {
        rows.push({ kind: "section", label: title });
        rows.push({ kind: "subtotal", cells: ["Group", "Occ", "Pax", "Room-nights", "Revenue", "ARR"] });
        items.forEach((s) => rows.push([keyLabel(s), s.occ, s.pax, s.room_nights, fmt(s.revenue), fmt(s.arr)]));
      };
      block("Floor-wise summary", d.floor_summary || [], (s) => `Floor ${s.floor}`);
      block("Type-wise revenue", d.by_type || [], (s) => s.key);
      block("Recapitulation (by source)", d.by_source || [], (s) => s.key);
      return {
        columns: ["Floor", "Room", "Guest", "Source", "Type", "Arrival", "Departure", "Pax",
          "Dis %", "Rack", "Room Rent", "Discount", "Gross", "GST", "Net"],
        rows,
        totals: ["TOTAL", "", "", "", "", "", "", d.totals.pax, "", "", "", "", "", "", fmt(d.totals.revenue)],
      };
    },
  },
  "cashier-summary": {
    label: "Cashier Summary",
    path: "cashier-summary",
    range: true,   // v7h: one day, or the dates you pick
    note: "Front-office collections for the dates picked, grouped by payment mode with Guest check-out bills / Advance receipts / Paid-outs, plus unsettled and nil-amount checkouts, every invoice raised, and the rooms in house.",
    fetch: (p) => api.getCashierSummary(p),
    project: (d) => {
      const rows = [];
      (d.modes || []).forEach((m) => {
        rows.push({ kind: "section", label: m.label });
        (m.sections || []).forEach((sec) => {
          rows.push({ kind: "section", label: `  ${sec.label}` });
          // v7n: a Source column; the server sends each section in document-number order
          sec.rows.forEach((x) => rows.push([x.bill_no, x.date, x.time, x.room,
            [x.guest, x.booking_id ? `#${x.booking_id}` : ""].filter(Boolean).join(" · "), x.source || "",
            x.receipt ? fmt(x.receipt) : "", x.payment ? fmt(x.payment) : "", balCell(x),
            x.cr_no, x.remarks || "", x.user || ""]));
        });
        rows.push({ kind: "subtotal", cells: [`Total (${m.label})`, "", "", "", "", "",
          fmt(m.receipt), fmt(m.payment), "", "", m.memo ? "MEMO - not in grand total" : "", ""] });
      });
      if ((d.unsettled || []).length) rows.push({ kind: "section", label: "Unsettled Checkout Bills" });
      (d.unsettled || []).forEach((x) => rows.push([x.bill_no, x.date || "", x.time || "", x.room, x.guest || "", x.source || "", "", "", fmt(x.balance), "", "UNSETTLED", x.user || ""]));
      // v7s (owner): agent bills the guest pays later - shown as DUE, not balance
      if ((d.due_bills || []).length) rows.push({ kind: "section", label: `Due Bills - Pay Later (${d.due_bills.length}) - ${fmt(d.due_bills_total)}` });
      (d.due_bills || []).forEach((x) => rows.push([x.bill_no, x.date || "", x.time || "", x.room, x.guest || "", x.source || "", "", "",
        `DUE ${fmt(x.due)}`, "", `PAY LATER${x.agent ? ` - ${x.agent}` : ""}${x.due_outstanding ? "" : " (since paid)"}`, x.user || ""]));
      if ((d.nil_checkouts || []).length) rows.push({ kind: "section", label: "Checkout Bills With Nil Amount" });
      (d.nil_checkouts || []).forEach((x) => rows.push([x.bill_no, x.date || "", x.time || "", x.room, x.guest || "", x.source || "", "", "", fmt(x.balance), "", "NIL", x.user || ""]));
      // v7h: every invoice raised in the period
      if ((d.invoices || []).length) rows.push({ kind: "section", label: `Invoices Raised (${d.invoices.length}) - ${fmt(d.invoices_total)}` });
      (d.invoices || []).forEach((x) => rows.push([x.bill_no, x.date, x.time, x.room, x.guest || "", x.source || "", "", "", balCell(x), "",
        `INVOICE ${fmt(x.amount)}${x.prepaid ? ` (prepaid ${fmt(x.prepaid)})` : ""}`, x.user || ""]));
      // v7e: rooms in house (end of the day / now)
      if ((d.in_house || []).length) rows.push({ kind: "section", label: `Rooms In House (${d.in_house.length})` });
      (d.in_house || []).forEach((x) => rows.push([`#${x.booking_id}`, (x.checked_in || "").split(" ")[0], (x.checked_in || "").split(" ")[1] || "",
        x.room, x.guest || "", x.source || "", "", "", fmt(x.balance), "", `IN HOUSE - out ${x.check_out || ""}`, ""]));
      return {
        columns: ["Bill/Vou", "Date", "Time", "Room", "Guest", "Source", "Receipt", "Payment", "Balance",
          "CR No", "Remarks", "User"],
        rows,
        totals: ["GRAND TOTAL", "", "", "", "", "", fmt(d.grand.receipt), fmt(d.grand.payment), "", "",
          `net ${fmt(d.grand.net)}`, ""],
      };
    },
  },
  "checkout-summary": {
    label: "Check-Out Summary",
    path: "checkout-summary",
    dateParam: "day",
    note: "One row per bill checked out on the day — room rent and food each with the CGST / SGST inside them, laundry, misc, discount, the bill's total CGST / SGST, advance (taken before check-out), balance (paid at check-out), refund, due (agent bills left to pay later) and bill amount.",
    fetch: (p) => api.getCheckoutSummary(p),
    project: (d) => ({
      // v7m (owner): room rent and food each show their CGST / SGST
      // v7o (owner): check-in and check-out date + time
      columns: ["Bill", "Rooms", "Check-in", "Check-out", "Room Rent", "Room CGST", "Room SGST", "Food",
        "Food CGST", "Food SGST", "Laundry", "Misc", "Discount", "Total CGST", "Total SGST", "Advance",
        "Balance", "Refund", "Due", "Bill Amt", "By"],
      // v7s (owner): Balance = paid at check-out; Due = left to pay later (agent bookings)
      rows: d.rows.map((r) => [r.bill_no, r.rooms, r.checked_in_at || "", r.checked_out_at || "",
        fmt(r.room_rent), fmt(r.room_cgst), fmt(r.room_sgst), fmt(r.food), fmt(r.food_cgst), fmt(r.food_sgst),
        fmt(r.laundry), fmt(r.misc), fmt(r.discount), fmt(r.cgst), fmt(r.sgst), fmt(r.advance), fmt(r.balance),
        fmt(r.refund), fmt(r.due), fmt(r.bill_amount), r.checked_out_by || ""]),
      totals: ["TOTAL", "", "", "", fmt(d.totals.room_rent), fmt(d.totals.room_cgst), fmt(d.totals.room_sgst),
        fmt(d.totals.food), fmt(d.totals.food_cgst), fmt(d.totals.food_sgst), fmt(d.totals.laundry),
        fmt(d.totals.misc), fmt(d.totals.discount), fmt(d.totals.cgst), fmt(d.totals.sgst),
        fmt(d.totals.advance), fmt(d.totals.balance), fmt(d.totals.refund), fmt(d.totals.due),
        fmt(d.totals.bill_amount), ""],
    }),
  },
  "sales/daily": {
    label: "Daily Sales",
    path: "sales/daily",
    range: true,
    note: "Room Service is the part of Other Revenue that came from the menu — it is included in that column, not additional to it.",
    fetch: (p) => api.getDailySales(p),
    project: (d) => ({
      // v6d (F-11): Net Sales sits between the discount and the tax, because that is the figure
      // the tax is now charged on. Without it the row reads as if Taxable + CGST + SGST should
      // add up to Gross Sales, and on a discounted day it does not.
      columns: ["Date", "Room Revenue", "Other Revenue", "of which Room Service", "Discount",
        "Gross Sales", "Net Sales", "Taxable", "CGST", "SGST"],
      rows: d.rows.map((r) => [r.date, fmt(r.room_revenue), fmt(r.other_revenue), fmt(r.room_service),
        fmt(r.discount), fmt(r.gross_sales), fmt(r.net_sales), fmt(r.taxable), fmt(r.cgst), fmt(r.sgst)]),
      totals: ["TOTAL", fmt(d.totals.room_revenue), fmt(d.totals.other_revenue), fmt(d.totals.room_service),
        fmt(d.totals.discount), fmt(d.totals.gross_sales), fmt(d.totals.net_sales),
        fmt(d.totals.taxable), fmt(d.totals.cgst), fmt(d.totals.sgst)],
    }),
  },
  "room-service/daily": {
    label: "Room Service (day-wise)",
    path: "room-service/daily",
    range: true,
    note: "Room-service sales per day. Reconciles with the Room Service column on Daily Sales — both read the delivered, non-voided menu lines.",
    fetch: (p) => api.getRoomServiceDaily(p),
    project: (d) => ({
      columns: ["Date", "Orders", "Items", "Gross", "Taxable", "CGST", "SGST"],
      rows: d.rows.map((r) => [r.date, r.orders, r.items, fmt(r.gross), fmt(r.taxable),
        fmt(r.cgst), fmt(r.sgst)]),
      totals: ["TOTAL", d.totals.orders, d.totals.items, fmt(d.totals.gross), fmt(d.totals.taxable),
        fmt(d.totals.cgst), fmt(d.totals.sgst)],
    }),
  },
  "room-service/items": {
    label: "Room Service (product-wise)",
    path: "room-service/items",
    range: true,
    note: "Which dishes sell, best first. Charges posted before this feature shipped carry no menu link and are not counted.",
    fetch: (p) => api.getRoomServiceItems(p),
    project: (d) => ({
      columns: ["Item", "Category", "Qty Sold", "Gross", "Avg Price", "% of RS Sales"],
      rows: d.rows.map((r) => [r.item, r.category, r.qty, fmt(r.gross), fmt(r.avg_price),
        `${r.share_percent}%`]),
      totals: ["TOTAL", `${d.totals.items} item${d.totals.items === 1 ? "" : "s"}`, d.totals.qty,
        fmt(d.totals.gross), "", `${d.totals.share_percent}%`],
    }),
  },
  "room-service/by-room": {
    label: "Room Service (by room)",
    path: "room-service/by-room",
    range: true,
    note: "Room-service sales grouped by room, product-wise within each room. A charge is attributed to the stay's room.",
    fetch: (p) => api.getRoomServiceByRoom(p),
    project: (d) => {
      const rows = [];
      d.rows.forEach((g) => {
        const bk = `#${g.booking_id}${g.guest ? ` ${g.guest}` : ""}`;
        rows.push({ kind: "section", label: `Room ${g.room} · ${bk}` });
        g.items.forEach((it) => rows.push([it.item, it.qty, fmt(it.amount)]));
        rows.push({ kind: "subtotal", cells: ["Subtotal", g.qty, fmt(g.subtotal)] });
      });
      return {
        columns: ["Item", "Qty", "Amount"],
        rows,
        totals: [`TOTAL — ${d.totals.groups} stay(s) · ${d.totals.rooms} room(s)`, "", fmt(d.totals.gross)],
      };
    },
  },
  "room-detail": {
    label: "Room Detail (HK + frauds)",
    path: "room-detail",
    range: true,
    note: "Per checked-out stay (by check-out date): check-in/out, cleaning started/ended, inspected time, cleaned-by, inspected-by, and any frauds.",
    fetch: (p) => api.getRoomDetail(p),
    project: (d) => {
      const t = (s) => (s || "").replace("T", " ").slice(0, 16);
      return {
        // v7g: cleaning card cut / returned; a start taken from the card cut is marked "(card)"
        columns: ["Room", "Guest", "Check-in", "Check-out", "Card cut", "Card returned", "Cleaning start",
          "Cleaning end", "Inspected", "Cleaned by", "Inspected by", "Frauds"],
        // v7p: frauds in plain words; a row with a fraud alert is shaded red
        rows: d.rows.map((r) => ({ kind: "row", danger: r.has_fraud, cells: [r.room, r.guest, t(r.check_in), t(r.check_out),
          t(r.card_cut), (r.card_returned || "").startsWith("20") ? t(r.card_returned) : (r.card_returned || ""),
          t(r.cleaning_started) + (r.cleaning_started_from_card ? " (card)" : ""), t(r.cleaning_ended),
          t(r.inspected_at), r.cleaned_by || "", r.inspected_by || "", r.frauds || ""] })),
      };
    },
  },
  "maintenance-detail": {
    label: "Maintenance Detail",
    path: "maintenance-detail",
    range: true,
    note: "Per ticket raised in the range: issue, status (fixed?/inspected?), assignee, verified-by, items used and parts cost.",
    fetch: (p) => api.getMaintenanceDetail(p),
    project: (d) => ({
      columns: ["Ticket", "Location", "Category", "Priority", "Issue", "Status", "Fixed",
        "Inspected", "Assignee", "Verified by", "Items", "Parts ₹"],
      rows: d.rows.map((r) => [r.ticket_id, r.location, r.category, r.priority, r.issue, r.status,
        r.fixed ? "Yes" : "No", r.inspected ? "Yes" : "No", r.assignee || "", r.verified_by || "",
        r.items.map((i) => `${i.item}×${i.qty} [${i.status}]`).join("; ") || "—", fmt(r.parts_cost)]),
      totals: [`${d.totals.tickets} ticket(s)`, `${d.totals.fixed} fixed / ${d.totals.open} open`,
        "", "", "", "", "", `${d.totals.inspected} insp.`, "", "", "", fmt(d.totals.parts_cost)],
    }),
  },
  "shift-payments": {
    label: "Shift Payment Split",
    path: "shift-payments",
    range: true,
    note: "Collections per shift by method. Counted Cash / Variance are the drawer only — card and UPI never enter it, so they are not part of the variance.",
    fetch: (p) => api.getShiftPayments(p),
    project: (d) => ({
      columns: ["Shift", "Station", "Staff", "Opened", "Closed", "Cash", "Card", "UPI", "Bank",
        "Collected", "Refunds", "Net", "Counted Cash", "Variance"],
      rows: d.rows.map((r) => [r.shift_id, r.station_id || "—", r.staff_name || "—",
        r.opened_at ? r.opened_at.slice(0, 16).replace("T", " ") : "—",
        r.closed_at ? r.closed_at.slice(0, 16).replace("T", " ") : "open",
        fmt(r.cash), fmt(r.card), fmt(r.upi), fmt(r.bank), fmt(r.total_collected),
        fmt(r.refunds), fmt(r.net_collected),
        r.counted_cash === null ? "—" : fmt(r.counted_cash),
        r.variance === null ? "—" : fmt(r.variance)]),
      totals: ["TOTAL", "", "", "", "", fmt(d.totals.cash), fmt(d.totals.card), fmt(d.totals.upi),
        fmt(d.totals.bank), fmt(d.totals.total_collected), fmt(d.totals.refunds),
        fmt(d.totals.net_collected), "", fmt(d.totals.variance)],
    }),
  },
  "payments-daily": {
    label: "Payments (day-wise)",
    path: "payments-daily",
    range: true,
    fetch: (p) => api.getPaymentsDaily(p),
    project: (d) => ({
      columns: ["Date", "Cash", "Card", "UPI", "Bank", "Collected", "Refunds", "Net"],
      rows: d.rows.map((r) => [r.date, fmt(r.cash), fmt(r.card), fmt(r.upi), fmt(r.bank),
        fmt(r.collected), fmt(r.refunds), fmt(r.net)]),
      totals: ["TOTAL", fmt(d.totals.cash), fmt(d.totals.card), fmt(d.totals.upi), fmt(d.totals.bank),
        fmt(d.totals.collected), fmt(d.totals.refunds), fmt(d.totals.net)],
    }),
  },
  "arrivals-departures": {
    label: "Arrivals / Departures",
    path: "arrivals-departures",
    range: true,
    fetch: (p) => api.getArrivalsDepartures(p),
    project: (d) => ({
      columns: ["Date", "Arrivals", "Departures"],
      rows: d.rows.map((r) => [r.date, r.arrivals, r.departures]),
      totals: ["TOTAL", d.totals.arrivals, d.totals.departures],
    }),
  },
  "in-house": {
    label: "In-House",
    path: "in-house",
    range: false,
    fetch: () => api.getInHouse(),
    project: (d) => ({
      columns: ["Booking", "Guest", "Phone", "Rooms", "Check-in", "Check-out", "Nights", "Balance"],
      rows: d.rows.map((r) => [r.booking_id, r.guest_name, r.phone, r.rooms, r.check_in, r.check_out,
        r.nights, r.balance == null ? "—" : fmt(r.balance)]),
    }),
  },
  "arrival-exceptions": {
    label: "Arrival Exceptions",
    path: "arrival-exceptions",
    range: true,
    fetch: (p) => api.getArrivalExceptions(p),
    note: "Early check-ins, late arrivals and hourly (late-checkout) extensions as the arrival rules recorded them, with the fee charged and who approved a change. Voided fees show as Voided. Rules live in Settings → Arrival & departure.",
    project: (d) => {
      const dev = (m) => (m == null ? "" : `${Math.floor(Math.abs(m) / 60)}h ${String(Math.abs(m) % 60).padStart(2, "0")}m`);
      const t = d.totals || {};
      return {
        columns: ["Date", "Booking", "Guest", "Room", "Source", "Kind", "Expected", "Actual", "Deviation", "Hours", "Charge", "Basis", "Approved by"],
        rows: d.rows.map((r) => [r.date, r.booking_id, r.guest, r.rooms, r.source, r.kind_label, r.expected_at || "", r.actual_at || "",
          dev(r.deviation_minutes), r.hours || "", fmt(r.charge), r.basis_label, r.approved_by || ""]),
        totals: ["TOTAL", "", "", "", "",
          `Early ${t.early_checkin?.count ?? 0} · Late ${t.late_arrival?.count ?? 0} · Hours ${t.hourly_extension?.count ?? 0} · No-shows ${d.no_shows ?? 0}`,
          "", "", "", "", fmt(d.charged_total), "", ""],
      };
    },
  },
  "travel-agents": {
    label: "Travel Agents",
    path: "travel-agents",
    range: true,
    fetch: (p) => api.getTravelAgents(p),
    project: (d) => ({
      columns: ["Agent", "Comm %", "Bookings", "Room Revenue", "Accrued", "Paid", "Outstanding"],
      rows: d.rows.map((r) => [r.agent_name, `${r.commission_percent}%`, r.bookings, fmt(r.room_revenue),
        fmt(r.commission_accrued), fmt(r.commission_paid), fmt(r.outstanding)]),
      totals: ["TOTAL", "", d.totals.bookings, fmt(d.totals.room_revenue), fmt(d.totals.commission_accrued),
        fmt(d.totals.commission_paid), fmt(d.totals.outstanding)],
    }),
  },
  ota: {
    label: "OTA Revenue",
    path: "ota",
    range: true,
    fetch: (p) => api.getOta(p),
    note: "OTA commission/payout reconciliation lands with the channel manager (prompt 17). Commission shown is an estimate.",
    project: (d) => ({
      columns: ["OTA", "Bookings", "Room Revenue", "Commission (est.)"],
      rows: d.rows.map((r) => [r.ota, r.bookings, fmt(r.room_revenue), fmt(r.commission_est)]),
      totals: ["TOTAL", d.totals.bookings, fmt(d.totals.room_revenue), fmt(d.totals.commission_est)],
    }),
  },
  gst: {
    label: "GST",
    path: "gst",
    range: true,
    fetch: (p) => api.getGst(p),
    project: (d) => ({
      // v6d (F-11): the filing report now shows the working, slab by slab — billed, the share of
      // the discount that came off this slab, the net it was taxed on, and the tax. An assessing
      // officer asking "why is the taxable value lower than the billing" can read the answer here.
      columns: ["GST Slab %", "Gross Billed", "Discount", "Net (taxed on)", "Taxable Value", "CGST", "SGST"],
      rows: d.slabs.map((r) => [`${r.gst_percent}%`, fmt(r.gross), fmt(r.discount), fmt(r.total),
        fmt(r.taxable), fmt(r.cgst), fmt(r.sgst)]),
      totals: ["TOTAL", fmt(d.gross_total), fmt(d.discount_total), fmt(d.net_total),
        fmt(d.taxable_total), fmt(d.cgst_total), fmt(d.sgst_total)],
    }),
  },
  "cash-shift": {
    label: "Cash / Shift",
    path: "cash-shift",
    range: true,
    fetch: (p) => api.getCashShift(p),
    project: (d) => ({
      columns: ["Shift", "Station", "Status", "Staff", "Opening", "Collections", "Expenses", "Payouts",
        "Expected", "Counted", "Variance"],
      rows: d.rows.map((r) => [r.shift_id, r.station_id, r.status, r.staff_name, fmt(r.opening_balance),
        fmt(r.collections_cash), fmt(r.expenses_total), fmt(r.payouts_total), fmt(r.expected_cash),
        r.counted_cash == null ? "—" : fmt(r.counted_cash), r.variance == null ? "—" : fmt(r.variance)]),
      totals: ["TOTAL", "", "", "", fmt(d.totals.opening_balance), fmt(d.totals.collections_cash),
        fmt(d.totals.expenses_total), fmt(d.totals.payouts_total), fmt(d.totals.expected_cash), "",
        fmt(d.totals.variance)],
    }),
  },
  "lost-cards": {
    label: "Lost Key Cards",
    path: "lost-cards",
    range: true,
    note: "Every card reported lost, and whether the fee was charged. Set the amount in " +
          "Settings → Front desk; at ₹0 nothing is billed.",
    fetch: (p) => api.getLostCards(p),
    project: (d) => ({
      columns: ["Reissued", "Room", "Guest", "Booking", "Lost card", "New card", "Cut by", "Fee", "Status",
        "Fee decided by", "Reason / approval"],
      rows: d.rows.map((r) => [(r.reissued_at || "").replace("T", " ").slice(0, 16), r.room,
        r.guest, r.booking_id, r.lost_card_id, r.new_card_id, r.issued_by, r.fee, r.fee_status,
        r.fee_decided_by || "", r.fee_reason || ""]),
      totals: ["TOTAL", "", "", "", "", "", "", d.charged_total,
        `${d.uncharged} not charged`],
    }),
  },
  "card-audit": {
    label: "Card Audit",
    path: "card-audit",
    range: true,
    fetch: (p) => api.getCardAudit(p),
    project: (d) => ({
      columns: ["Card", "Booking", "Room", "UID", "Type", "Issue Type", "Status", "Issued By", "Station", "Issued At"],
      rows: d.rows.map((r) => [r.card_id, r.booking_id, r.room, r.card_uid, r.card_type, r.issue_type,
        r.status, r.issued_by, r.station_id, (r.issued_at || "").replace("T", " ").slice(0, 16)]),
    }),
  },
  "room-turnaround": {
    label: "Room Turnaround & Cards",
    path: "room-turnaround",
    range: true,
    note: "Every step of each stay - check-in, key card cut, check-out, card wiped, cleaning card cut, cleaning start / end, cleaning card back, inspected - with the minutes between them (in brackets) and red flags; then the viewing cards and whether the room was checked in after. KPIs on top.",
    fetch: (p) => api.getRoomTurnaround(p),
    project: (d) => {
      const t = (s) => (s && s.startsWith("20") ? `${s.slice(8, 10)}/${s.slice(5, 7)} ${s.slice(11, 16)}` : (s || "—"));
      const m = (v) => (v == null ? "" : ` (${v >= 0 ? "+" : ""}${Math.round(v)}m)`);
      const k = d.kpis || {};
      const v = (x, u = "") => (x == null ? "—" : `${x}${u}`);
      const tiles = [
        { label: "Rooms checked out", value: v(k.stays) },
        { label: "Stays with a red flag", value: v(k.suspicious_stays), tone: k.suspicious_stays ? "bad" : "" },
        { label: "Avg check-in → key card", value: v(k.avg_checkin_to_card_min, " min") },
        { label: "Key cards wiped at check-out", value: v(k.pct_cards_wiped_at_checkout, "%") },
        { label: "Avg check-out → cleaning start", value: v(k.avg_checkout_to_clean_start_min, " min") },
        { label: "Avg cleaning time", value: v(k.avg_clean_minutes, " min") },
        { label: "Cleanings too fast", value: v(k.pct_cleaned_too_fast, "%"), tone: k.pct_cleaned_too_fast ? "bad" : "" },
        { label: "Cleaning cards returned", value: v(k.pct_cleaning_cards_returned, "%") },
        { label: "Avg check-out → room ready", value: v(k.avg_turnaround_min, " min") },
        { label: "Rooms inspected", value: v(k.pct_inspected, "%") },
        { label: "Viewing cards", value: v(k.viewings) },
        { label: "Viewings → check-in", value: v(k.viewing_to_checkin_pct, "%") },
        { label: "Viewing cards returned", value: v(k.viewing_cards_returned_pct, "%") },
        { label: "Viewings with a red flag", value: v(k.suspicious_viewings), tone: k.suspicious_viewings ? "bad" : "" },
      ];
      const rows = [{ kind: "section", label: `Room stays (${(d.rows || []).length})` }];
      (d.rows || []).forEach((r) => {
        const x = r.diff || {};
        rows.push({ kind: "row", danger: r.suspicious, cells: [
          r.room, r.guest || "", t(r.check_in), t(r.checkin_card) + m(x.ci_to_card), t(r.check_out),
          r.checkout_wipe_note || (t(r.checkout_wipe) + m(x.co_to_wipe)),
          t(r.clean_card_cut) + m(x.co_to_clean_card), t(r.clean_start), t(r.clean_end) + m(x.clean_minutes),
          t(r.clean_card_returned) + m(x.end_to_card_return),
          r.inspected_at ? `${t(r.inspected_at)}${m(x.end_to_inspected)}${r.inspected_by ? ` by ${r.inspected_by}` : ""}` : "—",
          x.turnaround == null ? "—" : `${Math.round(x.turnaround)} min`,
          (r.flags || []).join("; ") || "OK"] });
      });
      rows.push({ kind: "section", label: `Viewing cards (${(d.viewings || []).length}) - room, cut, returned, minutes out (allowed), cut by, checked in after?, red flags` });
      (d.viewings || []).forEach((w) => rows.push({ kind: "row", danger: w.suspicious, cells: [
        w.room, "viewing", t(w.cut), "", w.returned ? t(w.returned) : w.status, "",
        w.minutes_out == null ? "" : `${Math.round(w.minutes_out)} min out`, w.allowed_minutes ? `allowed ${w.allowed_minutes}` : "",
        w.issued_by || "", "", w.checked_in ? `Checked in - #${w.checkin_booking_id}, ${Math.round(w.minutes_to_checkin)} min later` : "Not checked in",
        "", (w.flags || []).join("; ") || "OK"] }));
      return {
        tiles,
        columns: ["Room", "Guest", "Check-in", "Key card cut", "Check-out", "Card wiped", "Clean card cut",
          "Clean start", "Clean end", "Clean card back", "Inspected", "Ready after", "Red flags"],
        rows,
      };
    },
  },
  "fraud-summary": {
    label: "Fraud Summary",
    path: "fraud-summary",
    range: true,
    fetch: (p) => api.getFraudSummary(p),
    note: "Every alert in plain words - what happened, in which room, who did it, and whether anyone has checked it. Grouped by kind, most frequent first; each group says why it matters and what to check.",
    // v7p (owner): readable - grouped by kind, each alert as a sentence
    project: (d) => {
      const rows = [];
      (d.kinds || []).forEach((k) => {
        rows.push({ kind: "section", label: `${k.title} - ${k.count} alert${k.count === 1 ? "" : "s"} (${k.open} not reviewed). ${k.meaning} Check: ${k.check}` });
        d.rows.filter((r) => r.type === k.type).forEach((r) => rows.push([
          r.when, r.room || "—", [r.guest, r.booking_id ? `#${r.booking_id}` : ""].filter(Boolean).join(" · ") || "—",
          r.what_happened, r.staff || "—", r.status_text]));
      });
      return { columns: ["When", "Room", "Guest / booking", "What happened", "Staff", "Status"], rows };
    },
  },
};

const TAB_ORDER = Object.keys(REPORTS);

// Saved report views — the owner's recurring reports (tab + date range + grouping) kept in the browser.
const VIEWS_KEY = "admin.reports.views";
const loadViews = () => {
  try { return JSON.parse(localStorage.getItem(VIEWS_KEY)) || []; } catch { return []; }
};
const persistViews = (v) => {
  try { localStorage.setItem(VIEWS_KEY, JSON.stringify(v)); } catch { /* ignore quota */ }
};

// v7e (owner): "an option to zip receipts, invoices, reg slip of 1 for any date and download"
function DocumentsZip() {
  const [day, setDay] = useState(new Date(Date.now() - 864e5).toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const base = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
  const download = async () => {
    setBusy(true); setMsg("");
    try {
      const res = await fetch(`${base}/reports/documents-zip?day=${day}`,
        { headers: { Authorization: `Bearer ${localStorage.getItem("adminToken")}` } });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${res.status}`);
      }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url; a.download = `documents_${day}.zip`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) { setMsg(e.message || "Download failed"); }
    finally { setBusy(false); }
  };
  return (
    <div className="mb-6 p-4 rounded-2xl border border-[#E5C07B]/30 bg-slate-800/40 flex flex-wrap items-center gap-3">
      <div className="flex-1 min-w-[220px]">
        <div className="font-semibold text-white">Documents of a day (ZIP)</div>
        <div className="text-xs text-slate-400">
          Receipts (by payment type), refunds, prepaid receipts, food bills, invoices and registration
          slips - in folders, named RCPT-1, INV-…, REG-… - plus an index and the day's expenses.
        </div>
      </div>
      <input type="date" value={day} onChange={(e) => setDay(e.target.value)}
        className="px-3 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white text-sm" />
      <button onClick={download} disabled={busy}
        className="bg-[#E5C07B]/90 hover:bg-[#E5C07B] text-slate-900 font-semibold px-4 py-2 rounded-lg text-sm disabled:opacity-50">
        {busy ? "Preparing…" : "Download ZIP"}
      </button>
      {msg && <div className="w-full text-sm text-red-300">{msg}</div>}
    </div>
  );
}

export default function Reports() {
  const [tab, setTab] = useState("occupancy");
  const [range, setRange] = useState({ from: daysAgo(29), to: today() });
  const [singleDate, setSingleDate] = useState(today());   // for single-date snapshot reports
  const [groupBy, setGroupBy] = useState("day");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [views, setViews] = useState(loadViews);
  const [viewName, setViewName] = useState("");

  const cfg = REPORTS[tab];
  const showToast = (m) => {
    setToast(m);
    setTimeout(() => setToast(""), 3500);
  };

  const saveCurrentView = () => {
    const name = viewName.trim();
    if (!name) return;
    const v = { name, tab, from: range.from, to: range.to, groupBy };
    const next = [...views.filter((x) => x.name !== name), v];
    setViews(next);
    persistViews(next);
    setViewName("");
    showToast(`Saved view “${name}”`);
  };
  const applyView = (v) => {
    setData(null);
    setError("");
    setTab(v.tab);
    setRange({ from: v.from, to: v.to });
    if (v.groupBy) setGroupBy(v.groupBy);
  };
  const deleteView = (name) => {
    const next = views.filter((x) => x.name !== name);
    setViews(next);
    persistViews(next);
  };

  const params = useMemo(() => {
    const p = {};
    if (cfg.range) {
      p.from = range.from;
      p.to = range.to;
    }
    if (cfg.dateParam) p[cfg.dateParam] = singleDate;
    if (cfg.groupBy) p.group_by = groupBy;
    return p;
  }, [cfg, range, singleDate, groupBy]);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await cfg.fetch(params));
    } catch (e) {
      setError(e.message || "Failed to load report");
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // reload when the tab or its controls change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, groupBy]);

  const doExport = async (format) => {
    try {
      await api.exportReport(cfg.path, params, format);
    } catch (e) {
      showToast(e.message || "Export failed");
    }
  };

  // Safety net: never let a data/shape mismatch (e.g. an in-flight tab switch) crash the whole page.
  let projected = null;
  if (data) {
    try {
      projected = cfg.project(data);
    } catch {
      projected = null;
    }
  }

  // Paginate long reports (e.g. Card Audit / Fraud Summary) at 50 rows/page.
  const paged = usePaged(projected?.rows || [], 50);
  const onLastPage = paged.page >= paged.pageCount - 1;

  // Right-align columns whose header reads monetary/numeric, for a clean ledger look.
  const MONEY_RE = /amount|receipt|payment|balance|rent|gst|sgst|cgst|net|revenue|gross|discount|advance|refund|total|qty|price|commission|rack|₹|occ|pax|arr/i;
  const moneyCols = new Set((projected?.columns || [])
    .map((h, idx) => (MONEY_RE.test(String(h)) ? idx : -1)).filter((x) => x >= 0));

  return (
    <PageShell
      icon="📊"
      title="Reports"
      subtitle="Operational & financial reports — filter by date and export to CSV / PDF."
      toast={toast}
    >

        {/* v7e: every document of one day as a ZIP, in folders */}
        <DocumentsZip />

        {/* Tabs */}
        <div className="flex flex-wrap gap-2 mb-6">
          {TAB_ORDER.map((k) => (
            <button
              key={k}
              onClick={() => {
                // Clear the previous report's data in the SAME render as the tab change, so the new
                // tab's projector never runs against the old tab's differently-shaped response.
                setTab(k);
                setData(null);
                setError("");
              }}
              className={`px-4 py-2 rounded-lg font-semibold transition text-sm ${
                tab === k
                  ? "bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900"
                  : "bg-slate-800/60 border border-slate-700 text-slate-300 hover:bg-slate-700/60"
              }`}
            >
              {REPORTS[k].label}
            </button>
          ))}
        </div>

        {/* Saved views */}
        <div className="flex flex-wrap items-center gap-2 mb-6">
          {views.map((v) => (
            <span key={v.name}
              className="inline-flex items-center gap-1 bg-slate-800/60 border border-slate-700 rounded-lg pl-3 pr-1 py-1 text-sm">
              <button onClick={() => applyView(v)} className="text-slate-200 hover:text-[#FCD34D] font-medium">{v.name}</button>
              <button onClick={() => deleteView(v.name)} aria-label={`Delete ${v.name}`}
                className="text-slate-500 hover:text-red-400 px-1 leading-none">×</button>
            </span>
          ))}
          <span className="inline-flex items-center gap-1">
            <input
              value={viewName}
              onChange={(e) => setViewName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && saveCurrentView()}
              placeholder="Save current view as…"
              className="px-3 py-1.5 text-sm bg-slate-900/50 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-[#E5C07B] w-44"
            />
            <button onClick={saveCurrentView} disabled={!viewName.trim()}
              className="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-white text-sm font-semibold transition">
              Save view
            </button>
          </span>
        </div>

        {/* Filter card */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-5 rounded-2xl shadow-xl mb-6 backdrop-blur">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
            {cfg.range && (
              <>
                <Field label="From" type="date" value={range.from}
                  onChange={(e) => setRange({ ...range, from: e.target.value })} />
                <Field label="To" type="date" value={range.to}
                  onChange={(e) => setRange({ ...range, to: e.target.value })} />
              </>
            )}
            {cfg.dateParam && (
              <Field label={cfg.dateParam === "as_on" ? "As on" : "Day"} type="date" value={singleDate}
                onChange={(e) => setSingleDate(e.target.value)} />
            )}
            {cfg.groupBy && (
              <div className="flex flex-col">
                <label className="mb-2 text-sm font-semibold text-slate-300">Group by</label>
                <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)} className={inputCls}>
                  <option value="day">Day</option>
                  <option value="room_type">Room type</option>
                </select>
              </div>
            )}
            <button
              onClick={load}
              className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition-all hover:scale-105 flex items-center justify-center gap-2"
            >
              <FaSearch size={14} /> Apply
            </button>
            <div className="flex gap-2">
              <button onClick={() => doExport("csv")}
                className="flex-1 bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg transition flex items-center justify-center gap-2">
                <FaFileCsv size={14} /> CSV
              </button>
              <button onClick={() => doExport("pdf")}
                className="flex-1 bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg transition flex items-center justify-center gap-2">
                <FaFilePdf size={14} /> PDF
              </button>
            </div>
          </div>
          {cfg.note && <p className="text-slate-500 text-xs mt-3">ⓘ {cfg.note}</p>}
        </div>

        {/* v7p: KPI tiles (Room Turnaround & Cards) */}
        {projected?.tiles && !loading && !error && (
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3 mb-6">
            {projected.tiles.map((tl) => (
              <div key={tl.label} className={`rounded-xl border p-3 ${tl.tone === "bad"
                ? "border-red-500/50 bg-red-900/20" : "border-slate-700 bg-slate-800/60"}`}>
                <div className="text-xs text-slate-400">{tl.label}</div>
                <div className={`text-xl font-bold ${tl.tone === "bad" ? "text-red-300" : "text-[#E5C07B]"}`}>{tl.value}</div>
              </div>
            ))}
          </div>
        )}

        {/* Table */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          <div className="p-5 border-b border-slate-700 flex items-center gap-2">
            <FaChartBar className="text-[#E5C07B]" />
            <h2 className="text-xl font-bold">{cfg.label}</h2>
          </div>

          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="animate-spin">
                <div className="h-10 w-10 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full" />
              </div>
            </div>
          ) : error ? (
            <div className="p-8 text-center text-red-300">{error}</div>
          ) : !projected || projected.rows.length === 0 ? (
            <div className="p-12 text-center text-slate-400">
              <div className="text-5xl mb-4">📭</div>
              <p>No data for the selected period.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-900/80 border-b border-slate-700">
                  <tr>
                    {projected.columns.map((c) => (
                      <th key={c} className="px-4 py-3 font-semibold whitespace-nowrap">{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {paged.pageItems.map((r, i) => {
                    const key = paged.page * 50 + i;
                    // Grouped reports emit marker rows: a full-width section band, or a bold subtotal.
                    if (r && r.kind === "section") {
                      return (
                        <tr key={key}>
                          <td colSpan={projected.columns.length}
                              className="px-4 py-2 bg-slate-800 text-[#E5C07B] font-semibold uppercase text-xs tracking-wide">
                            {r.label}
                          </td>
                        </tr>
                      );
                    }
                    const isSub = r && r.kind === "subtotal";
                    // v7p: {kind: "row", cells, danger} - a row with a red flag is shaded red
                    const isRow = r && r.kind === "row";
                    const cells = isSub || isRow ? r.cells : r;
                    return (
                      <tr key={key} className={isSub
                        ? "bg-slate-800/40 font-semibold text-slate-100 border-t border-slate-600"
                        : isRow && r.danger ? "bg-red-900/25 hover:bg-red-900/35 transition"
                        : "hover:bg-slate-700/30 transition"}>
                        {cells.map((c, j) => (
                          <td key={j} className={`px-4 py-2.5 text-slate-200 ${String(c ?? "").length > 45
                            ? "whitespace-normal min-w-[18rem]" : "whitespace-nowrap"} ${moneyCols.has(j) ? "text-right tabular-nums" : ""}`}>
                            {c == null ? (isSub ? "" : "—") : String(c)}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                  {projected.totals && onLastPage && (
                    <tr className="bg-[#E5C07B]/10 font-bold text-[#FCD34D] border-t border-[#E5C07B]/30">
                      {projected.totals.map((c, j) => (
                        <td key={j} className={`px-4 py-3 whitespace-nowrap ${moneyCols.has(j) ? "text-right tabular-nums" : ""}`}>{c == null ? "" : String(c)}</td>
                      ))}
                    </tr>
                  )}
                </tbody>
              </table>
              <Paginator {...paged} />
            </div>
          )}
        </div>
    </PageShell>
  );
}

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

const Field = ({ label, ...p }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
    <input {...p} className={inputCls} />
  </div>
);
