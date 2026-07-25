"""Tally / accounting export (prompt 18, slice 1).

Turns the day's recognised sales + collections into an accountant-importable file:
  * **Tally XML** — the standard `ENVELOPE > IMPORTDATA > Vouchers` shape Tally Prime/ERP-9 imports.
  * **Accounting CSV** — a generic double-entry journal (one row per ledger line) for any tool.

Totals reconcile with the Reports screen because the numbers come from the SAME helpers
(`routers.reports.sales_by_date` / `_gst_split`) — this satisfies the "totals match reports"
acceptance criterion. Voucher kinds per day:
  * **Sales** — Dr Debtors (gross), Cr Sales (taxable) + Cr CGST + Cr SGST (revenue recognition).
  * **Receipt** — Dr Cash/Bank (amount), Cr Debtors (payments collected, by method).
  * **Receipt (corporate)** — Dr Cash/Bank, Cr Debtors, for settlements a COMPANY paid against its
    city ledger (prompt 18 slice 7). Corporate stays recognise revenue through the Sales voucher
    like any other stay, but the money arrives later as a `CompanyLedger` payment rather than a
    `Payment` row — without this voucher Sundry Debtors would grow forever in the accountant's books.

Tally sign convention: debit amounts are NEGATIVE with ISDEEMEDPOSITIVE=Yes; credits POSITIVE.
Ledger + company names are admin-editable via utils.settings.get_compliance_config.
"""
import csv
import io
from datetime import datetime
from xml.sax.saxutils import escape

from sqlalchemy import func

from models import Company, CompanyLedger, Payment
from routers.reports import sales_by_date


def _tdate(iso: str) -> str:
    """'2026-07-21' -> '20260721' (Tally date format)."""
    return iso.replace("-", "")


def _payments_by_day_method(db, dfrom, dto):
    """{(date_iso, method): amount} for paid payments in range (method None -> 'other')."""
    rows = (db.query(func.date(Payment.created_at), Payment.method,
                     func.coalesce(func.sum(Payment.amount), 0))
            .filter(Payment.status == "paid",
                    func.date(Payment.created_at) >= dfrom,
                    func.date(Payment.created_at) <= dto)
            .group_by(func.date(Payment.created_at), Payment.method).all())
    out = {}
    for d, method, amt in rows:
        key = (str(d), (method or "other").lower())
        out[key] = round(out.get(key, 0.0) + float(amt or 0), 2)
    return out


def _receipt_ledger(method: str, cfg: dict) -> str:
    """Map a payment method to the cash vs bank ledger."""
    return cfg["tally_cash_ledger"] if method == "cash" else cfg["tally_bank_ledger"]


def _company_receipts(db, dfrom, dto):
    """Settlements companies paid against their city ledger, in range (prompt 18 slice 7).

    Returns [(date_iso, company_name, method, amount)] with amount POSITIVE. Ledger payments are
    stored negative (they reduce what the company owes); the sign is flipped here so the receipt
    voucher reads like any other collection."""
    rows = (db.query(func.date(CompanyLedger.created_at), Company.name, CompanyLedger.method,
                     func.coalesce(func.sum(CompanyLedger.amount), 0))
            .join(Company, Company.id == CompanyLedger.company_id)
            .filter(CompanyLedger.type == "payment",
                    CompanyLedger.created_at >= datetime.combine(dfrom, datetime.min.time()),
                    CompanyLedger.created_at < datetime.combine(dto, datetime.max.time()))
            .group_by(func.date(CompanyLedger.created_at), Company.name, CompanyLedger.method)
            .all())
    out = []
    for d, name, method, amt in rows:
        amount = round(-float(amt or 0), 2)     # stored negative -> positive receipt
        if amount != 0:
            out.append((str(d), name, (method or "bank_transfer").lower(), amount))
    return sorted(out)


def build_journal(db, dfrom, dto, cfg: dict):
    """Neutral intermediate: a list of voucher dicts. Both XML and CSV render from this.
    Each voucher = {vtype, date_iso, number, narration, lines:[{ledger, drcr, amount}]}."""
    vouchers = []
    sales = sales_by_date(db, dfrom, dto)
    for r in sales["rows"]:
        gross = round(r["gross_sales"], 2)
        if gross == 0 and r["taxable"] == 0:
            continue
        d = r["date"]
        vouchers.append({
            "vtype": "Sales",
            "date_iso": d,
            "number": f"S-{_tdate(d)}",
            "narration": f"Room & services sales {d}",
            "lines": [
                {"ledger": cfg["tally_debtors_ledger"], "drcr": "Dr", "amount": gross},
                {"ledger": cfg["tally_sales_ledger"], "drcr": "Cr", "amount": round(r["taxable"], 2)},
                {"ledger": cfg["tally_cgst_ledger"], "drcr": "Cr", "amount": round(r["cgst"], 2)},
                {"ledger": cfg["tally_sgst_ledger"], "drcr": "Cr", "amount": round(r["sgst"], 2)},
            ],
        })
    pays = _payments_by_day_method(db, dfrom, dto)
    for (d, method), amt in sorted(pays.items()):
        if amt == 0:
            continue
        vouchers.append({
            "vtype": "Receipt",
            "date_iso": d,
            "number": f"R-{_tdate(d)}-{method}",
            "narration": f"Collection {d} via {method}",
            "lines": [
                {"ledger": _receipt_ledger(method, cfg), "drcr": "Dr", "amount": amt},
                {"ledger": cfg["tally_debtors_ledger"], "drcr": "Cr", "amount": amt},
            ],
        })

    # Corporate settlements (prompt 18 slice 7). The stay's revenue was already recognised by the
    # Sales voucher when it happened; this clears the matching debtor balance when the company pays.
    for i, (d, company_name, method, amt) in enumerate(_company_receipts(db, dfrom, dto), start=1):
        vouchers.append({
            "vtype": "Receipt",
            "date_iso": d,
            "number": f"RC-{_tdate(d)}-{i:02d}",
            "narration": f"Corporate settlement {d} — {company_name} via {method}",
            "lines": [
                {"ledger": _receipt_ledger(method, cfg), "drcr": "Dr", "amount": amt},
                {"ledger": cfg["tally_debtors_ledger"], "drcr": "Cr", "amount": amt},
            ],
        })
    return vouchers, sales["totals"]


def build_tally_xml(db, dfrom, dto, cfg: dict) -> str:
    vouchers, _ = build_journal(db, dfrom, dto, cfg)
    company = escape(cfg["tally_company_name"])
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<ENVELOPE>",
        "<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>",
        "<BODY><IMPORTDATA>",
        "<REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME>",
        f"<STATICVARIABLES><SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY></STATICVARIABLES>",
        "</REQUESTDESC><REQUESTDATA>",
    ]
    for v in vouchers:
        parts.append('<TALLYMESSAGE xmlns:UDF="TallyUDF">')
        parts.append(f'<VOUCHER VCHTYPE="{v["vtype"]}" ACTION="Create">')
        parts.append(f"<DATE>{_tdate(v['date_iso'])}</DATE>")
        parts.append(f"<VOUCHERTYPENAME>{v['vtype']}</VOUCHERTYPENAME>")
        parts.append(f"<VOUCHERNUMBER>{escape(v['number'])}</VOUCHERNUMBER>")
        parts.append(f"<NARRATION>{escape(v['narration'])}</NARRATION>")
        for ln in v["lines"]:
            deemed = "Yes" if ln["drcr"] == "Dr" else "No"
            signed = -ln["amount"] if ln["drcr"] == "Dr" else ln["amount"]
            parts.append("<ALLLEDGERENTRIES.LIST>")
            parts.append(f"<LEDGERNAME>{escape(ln['ledger'])}</LEDGERNAME>")
            parts.append(f"<ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE>")
            parts.append(f"<AMOUNT>{signed:.2f}</AMOUNT>")
            parts.append("</ALLLEDGERENTRIES.LIST>")
        parts.append("</VOUCHER></TALLYMESSAGE>")
    parts.append("</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>")
    return "\n".join(parts)


def build_accounting_csv(db, dfrom, dto, cfg: dict) -> str:
    """Generic journal CSV: one row per ledger line."""
    vouchers, _ = build_journal(db, dfrom, dto, cfg)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Voucher Type", "Voucher No", "Ledger", "Dr/Cr", "Amount", "Narration"])
    for v in vouchers:
        for ln in v["lines"]:
            w.writerow([v["date_iso"], v["vtype"], v["number"], ln["ledger"],
                        ln["drcr"], f"{ln['amount']:.2f}", v["narration"]])
    return buf.getvalue()
