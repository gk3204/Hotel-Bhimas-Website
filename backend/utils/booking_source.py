"""Where a stay came from, as printed on every receipt and invoice (owner list 11 Oct, item 6).

ONE place, so a receipt and the invoice for the same stay can never disagree. Taken from the booking
itself - its channel, its travel agent, its bill-to company - never a default.
"""

SOURCE_LABELS = {"walk_in": "Walk-in", "website": "Website", "online": "Website", "makemytrip": "MakeMyTrip",
                 "goibibo": "Goibibo", "yatra": "Yatra", "agoda": "Agoda", "booking_com": "Booking.com",
                 "expedia": "Expedia", "cleartrip": "Cleartrip", "phone": "Phone", "agent": "Travel agent",
                 "corporate": "Corporate", "other_ota": "OTA", "frontdesk": "Front desk"}


def channel_label(booking) -> str:
    """The channel alone (MakeMyTrip, Website, Walk-in...)."""
    src = (getattr(booking, "booking_source", None) or "").strip().lower()
    return SOURCE_LABELS.get(src, src.replace("_", " ").title() or "Walk-in")


def source_label(db, booking) -> str:
    """The channel, or the travel agent's name, plus the bill-to company when there is one."""
    if booking is None:
        return "—"
    label = channel_label(booking)
    try:
        if getattr(booking, "agent_id", None):
            from models import TravelAgent
            a = db.query(TravelAgent).filter(TravelAgent.id == booking.agent_id).first()
            if a is not None:
                label = f"Travel agent - {a.name}"
        if getattr(booking, "company_id", None):
            from models import Company
            c = db.query(Company).filter(Company.id == booking.company_id).first()
            if c is not None:
                label = f"{label} / {c.name}"
    except Exception:
        pass
    return label
