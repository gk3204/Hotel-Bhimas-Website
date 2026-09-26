"""One GST calculation for the whole system (v6d / finding F-11).

Every rupee on this folio is stored **GST-inclusive**. A slab's taxable value is therefore
`gross / (1 + g/100)`, and CGST = SGST = half the tax, with CGST absorbing the odd paisa so that
`cgst + sgst == gst` exactly.

**What changed, and why it matters.** A discount used to be a non-taxable adjustment: the tax was
computed on the full amount and the discount then knocked off the bottom line, so the hotel paid GST
on money it never received. The owner's accountant confirmed the correct treatment — a discount
shown on the invoice at the time of supply reduces the **transaction value**, so it must reduce the
**taxable value** and the tax with it (CGST/SGST Act s.15(3)(a)).

A folio-level discount is not attached to a slab, so it is **apportioned across the slabs pro-rata
by each slab's gross**. That is the only defensible split: taking it all off the 12% room line
would change the tax by a different amount than taking it off the 5% food line, and the guest chose
neither. The largest slab absorbs the rounding residue so the apportioned parts add back to the
discount exactly, to the paisa.

This module is the single implementation. The invoice, the on-screen folio, the corporate
consolidated bill, the GST filing report and the Tally export all call it — deliberately, because
the danger here is not an arithmetic slip, it is two of those five disagreeing and the hotel's
filing no longer matching the documents it issued.
"""


def split(pairs, discount: float = 0.0) -> dict:
    """Split GST-inclusive sale amounts into taxable / CGST / SGST per slab, after discount.

    `pairs`     — iterable of `(gst_percent, gross_inclusive_amount)` over SALE lines only
                  (no payments, no discount lines).
    `discount`  — the folio-level discount as a **POSITIVE** number of rupees. Folio discount lines
                  are stored NEGATIVE, so callers pass `abs(...)`; the sign is taken here anyway
                  rather than trusted, because a sign error would silently inflate the tax.

    Returns per-slab rows and totals. Each row carries both `gross` (before discount) and `total`
    (after it) — `total` keeps the key name the invoice PDF, the stored `gst_breakup` JSON and the
    e-invoice payload already use, and after this change the figure they want from it is the net.
    """
    slabs: dict[float, float] = {}
    for g, amt in pairs:
        g = float(g or 0)
        slabs[g] = round(slabs.get(g, 0.0) + float(amt or 0), 2)

    gross_total = round(sum(slabs.values()), 2)

    # A discount can never exceed what was billed. `/folio/{id}/discount` already refuses that, but
    # a consolidated bill or a report range can pair a discount with a filtered set of sale lines,
    # and a negative taxable value would be filed.
    discount = min(abs(float(discount or 0)), max(gross_total, 0.0))

    # Apportion pro-rata by gross. The largest slab takes the residue, so the parts sum to the
    # discount exactly however the paisa fall.
    order = sorted(slabs)
    apportioned = {g: 0.0 for g in order}
    if discount > 0 and gross_total > 0:
        biggest = max(order, key=lambda g: (slabs[g], g))
        running = 0.0
        for g in order:
            if g == biggest:
                continue
            part = round(discount * slabs[g] / gross_total, 2)
            apportioned[g] = part
            running = round(running + part, 2)
        apportioned[biggest] = round(discount - running, 2)

    rows = []
    taxable_total = cgst_total = sgst_total = net_total = 0.0
    for g in order:
        gross = slabs[g]
        disc = apportioned[g]
        net = round(gross - disc, 2)
        taxable = round(net / (1 + g / 100), 2)
        gst = round(net - taxable, 2)
        sgst = round(gst / 2, 2)
        cgst = round(gst - sgst, 2)   # absorbs the odd paisa so cgst + sgst == gst exactly
        rows.append({"gst_percent": g, "gross": gross, "discount": disc,
                     "taxable": taxable, "cgst": cgst, "sgst": sgst, "total": net})
        taxable_total = round(taxable_total + taxable, 2)
        cgst_total = round(cgst_total + cgst, 2)
        sgst_total = round(sgst_total + sgst, 2)
        net_total = round(net_total + net, 2)

    return {
        "rows": rows,
        "gross_total": gross_total,        # before discount
        "discount_total": round(discount, 2),   # positive
        "net_total": net_total,            # what the guest owes for these lines, GST-inclusive
        "taxable_total": taxable_total,
        "cgst_total": cgst_total,
        "sgst_total": sgst_total,
    }
