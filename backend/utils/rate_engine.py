"""Rate engine (prompt 10) — the ONE shared price resolver.

Price depends on room type x channel/agent x date. Rates are stored per-night,
GST-EXCLUSIVE (same basis as RoomType.price_per_night), so a resolved rate is a
drop-in replacement for price_per_night — the existing GST + promotion math in the
booking routers is unchanged.

Resolution precedence (highest score wins, no blending):
  - rack       : RoomType.price_per_night                       -> score 0 (the floor)
  - agent_rate : negotiated card for (agent, room_type) on date -> score 100
  - rate_plan  : channel/date override                          -> score 200 + priority
                 (+50 agent-specific, +10 dated window, +5 weekday-scoped)

So a seasonal/weekend rate_plan with priority overrides a plain agent rate; any
plan or agent rate overrides the rack rate. days_of_week (CSV 0=Mon..6=Sun; null =
every day) scopes a plan to particular weekdays (e.g. "5,6" = weekend).

Usage:
    from utils.rate_engine import resolve_nightly_rate, quote_stay
    q = quote_stay(db, room_type, check_in, check_out, channel="agent", agent_id=3, quantity=2)
    base = q["base"]   # GST-exclusive room base for the whole stay
"""
from datetime import date, timedelta

from models import RatePlan, AgentRate


def _agent_rate_covers(ar: AgentRate, on_date: date) -> bool:
    if ar.valid_from and on_date < ar.valid_from:
        return False
    if ar.valid_to and on_date > ar.valid_to:
        return False
    return True


def _best_agent_rate(agent_rates, on_date: date):
    """Most specific agent rate covering the date: prefer a dated window over an
    open-ended one, then the most recently created (highest id)."""
    covering = [ar for ar in agent_rates if _agent_rate_covers(ar, on_date)]
    if not covering:
        return None
    return max(covering, key=lambda ar: ((ar.valid_from is not None or ar.valid_to is not None), ar.id))


def _plan_score(plan: RatePlan, on_date: date, agent_id) -> int | None:
    """Score a rate plan for this night, or None if it does not apply."""
    if plan.agent_id is not None and plan.agent_id != agent_id:
        return None
    if plan.valid_from and on_date < plan.valid_from:
        return None
    if plan.valid_to and on_date > plan.valid_to:
        return None
    if plan.days_of_week:
        allowed = {int(x) for x in plan.days_of_week.split(",") if x != ""}
        if on_date.weekday() not in allowed:   # Mon=0..Sun=6
            return None
    score = 200 + int(plan.priority or 0)
    if plan.agent_id is not None:
        score += 50
    if plan.valid_from is not None or plan.valid_to is not None:
        score += 10
    if plan.days_of_week:
        score += 5
    return score


def _pick(room_type, plans, agent_rate, on_date: date, agent_id):
    """Return (rate: float, source: str) for one night from prefetched candidates."""
    # (score, tiebreak_id, rate, source) — rack is the floor.
    best = (0, 0, float(room_type.price_per_night), "rack")
    if agent_rate is not None:
        cand = (100, agent_rate.id, float(agent_rate.rate), "agent_rate")
        if cand[:2] > best[:2]:
            best = cand
    for plan in plans:
        score = _plan_score(plan, on_date, agent_id)
        if score is None:
            continue
        cand = (score, plan.id, float(plan.price), "rate_plan")
        if cand[:2] > best[:2]:
            best = cand
    return round(best[2], 2), best[3]


def resolve_nightly_rate(db, room_type, night_date: date, channel: str = "walk_in", agent_id=None):
    """Resolved GST-exclusive per-night rate for a single night. (room, channel/agent, date) -> (rate, source)."""
    plans = db.query(RatePlan).filter(
        RatePlan.room_type_id == room_type.room_type_id,
        RatePlan.channel == channel,
        RatePlan.is_active == True,  # noqa: E712
    ).all()
    agent_rate = None
    if channel == "agent" and agent_id:
        rates = db.query(AgentRate).filter(
            AgentRate.agent_id == agent_id,
            AgentRate.room_type_id == room_type.room_type_id,
        ).all()
        agent_rate = _best_agent_rate(rates, night_date)
    return _pick(room_type, plans, agent_rate, night_date, agent_id)


def quote_stay(db, room_type, check_in: date, check_out: date,
               channel: str = "walk_in", agent_id=None, quantity: int = 1) -> dict:
    """Sum the resolved per-night rate over the stay (candidates prefetched once).
    Returns the GST-exclusive room base + a per-night breakdown for preview/audit."""
    plans = db.query(RatePlan).filter(
        RatePlan.room_type_id == room_type.room_type_id,
        RatePlan.channel == channel,
        RatePlan.is_active == True,  # noqa: E712
    ).all()
    agent_rates = []
    if channel == "agent" and agent_id:
        agent_rates = db.query(AgentRate).filter(
            AgentRate.agent_id == agent_id,
            AgentRate.room_type_id == room_type.room_type_id,
        ).all()

    nightly = []
    d = check_in
    while d < check_out:
        ar = _best_agent_rate(agent_rates, d) if agent_rates else None
        rate, source = _pick(room_type, plans, ar, d, agent_id)
        nightly.append({"date": str(d), "rate": rate, "source": source})
        d += timedelta(days=1)

    base = round(sum(n["rate"] for n in nightly) * quantity, 2)
    return {
        "base": base,
        "nights": len(nightly),
        "quantity": quantity,
        "nightly": nightly,
        "sources": sorted({n["source"] for n in nightly}),
    }
