// Room service (v3 item 3) — its own page, because this is the list that grows.
//
// Two changes over the old inline card: the menu is grouped into CATEGORY TABS (the category
// is already a validated `menu` family value on each item), and the cart lives in a sticky
// bar so the order button is reachable without scrolling back up a long menu.
//
// Prices are display-only. The server re-prices every line from its own menu on submit
// (services/room_service.snapshot_items), so a tampered request cannot discount a meal.
import React, { useMemo, useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { apiRequest } from "../../../api/api";
import { Btn, Card } from "../../../components/portal/PortalKit";
import { inCls, money } from "../../../components/portal/portalTokens";
import { prettyCategory } from "../../../utils/useCategoryList";

const ALL = "__all__";

export default function PortalRoomService() {
  const { token, data, reload, flash, base } = useOutletContext();
  const navigate = useNavigate();
  const [cart, setCart] = useState({});     // menu_item_id -> qty
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState(ALL);

  // Show the WHOLE menu — items that are off or outside their time window render greyed and
  // non-orderable (the server enforces the same on submit), so guests can see what's on offer.
  const items = useMemo(() => data.menu || [], [data.menu]);

  const windowsLabel = (m) =>
    (m.available_windows || [])
      .map((w) => `${w.start}–${w.end}`)
      .join(", ");

  // Tabs only earn their space once there is more than one section to switch between.
  const categories = useMemo(() => {
    const seen = [];
    items.forEach((m) => {
      const c = m.category || "other";
      if (!seen.includes(c)) seen.push(c);
    });
    return seen;
  }, [items]);

  const shown = tab === ALL ? items : items.filter((m) => (m.category || "other") === tab);

  const bump = (id, d) =>
    setCart((c) => {
      const q = Math.max(0, (c[id] || 0) + d);
      const next = { ...c };
      if (q === 0) delete next[id];
      else next[id] = q;
      return next;
    });

  // Totalled over the WHOLE menu, not the visible tab — switching sections must not appear
  // to empty the cart.
  const count = Object.values(cart).reduce((a, b) => a + b, 0);
  const orderableItems = items.filter((m) => m.orderable_now);
  const total = orderableItems.reduce((s, m) => s + (cart[m.id] || 0) * m.price, 0);

  const order = async () => {
    if (count === 0) return;
    setBusy(true);
    try {
      await apiRequest(`/portal/${token}/room-service`, {
        method: "POST",
        body: JSON.stringify({
          items: Object.entries(cart).map(([id, qty]) => ({ menu_item_id: Number(id), qty })),
          note: note || null,
          client_ref: `rs-${token}-${Date.now()}`,
        }),
      });
      setCart({});
      setNote("");
      flash("Order placed — our team will bring it up and add it to your bill.");
      await reload();
      navigate(`${base}/requests`);
    } catch (e) {
      flash(e.message || "Could not place the order.");
    } finally {
      setBusy(false);
    }
  };

  if (items.length === 0) {
    return (
      <Card icon="🍽️" title="Room service">
        <p className="text-sm text-slate-500">
          The menu is being updated — please call the desk to order.
        </p>
      </Card>
    );
  }

  return (
    <>
      {/* Bottom padding clears the sticky cart bar so the last dish is never hidden. */}
      <div className={count > 0 ? "pb-32" : ""}>
        {categories.length > 1 && (
          <div className="flex gap-2 overflow-x-auto pb-3 -mx-1 px-1">
            {[ALL, ...categories].map((c) => (
              <button
                key={c}
                onClick={() => setTab(c)}
                className={`shrink-0 px-3 py-1.5 rounded-full text-sm font-semibold border transition ${
                  tab === c
                    ? "bg-gradient-to-r from-[#D4AF37] to-[#B8860B] text-white border-transparent"
                    : "bg-white text-slate-600 border-slate-300"
                }`}
              >
                {c === ALL ? "All" : prettyCategory(c)}
              </button>
            ))}
          </div>
        )}

        <Card icon="🍽️" title={tab === ALL ? "Room service" : prettyCategory(tab)}>
          <div className="divide-y divide-slate-100">
            {shown.map((m) => {
              const orderable = m.orderable_now;
              const times = windowsLabel(m);
              return (
                <div
                  key={m.id}
                  className={`py-2 flex items-center justify-between gap-3 ${orderable ? "" : "opacity-60"}`}
                >
                  <div className="min-w-0">
                    <div className="text-slate-800 text-sm font-medium">{m.name}</div>
                    {m.description && (
                      <div className="text-xs text-slate-500 truncate">{m.description}</div>
                    )}
                    <div className="text-xs text-[#B8860B] font-semibold">{money(m.price)}</div>
                    {!orderable && (
                      <div className="text-xs text-rose-500 font-medium mt-0.5">
                        {m.is_available && times ? `Available ${times}` : "Not available"}
                      </div>
                    )}
                  </div>
                  {orderable ? (
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() => bump(m.id, -1)}
                        aria-label={`Remove one ${m.name}`}
                        className="w-8 h-8 rounded-full bg-slate-100 text-slate-700"
                      >
                        −
                      </button>
                      <span className="w-5 text-center text-sm">{cart[m.id] || 0}</span>
                      <button
                        onClick={() => bump(m.id, 1)}
                        aria-label={`Add one ${m.name}`}
                        className="w-8 h-8 rounded-full bg-[#D4AF37] text-white"
                      >
                        +
                      </button>
                    </div>
                  ) : (
                    <span className="shrink-0 text-xs text-slate-400 font-medium px-2">Unavailable</span>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      </div>

      {count > 0 && (
        <div className="fixed bottom-0 inset-x-0 z-40 bg-white border-t border-slate-200 shadow-[0_-4px_16px_rgba(0,0,0,0.08)]">
          <div className="max-w-lg mx-auto p-3 space-y-2">
            <input
              className={inCls}
              value={note}
              placeholder="Any note (e.g. no onion)"
              onChange={(e) => setNote(e.target.value)}
            />
            <Btn onClick={order} disabled={busy}>
              {busy ? "Placing…" : `Order ${count} item${count === 1 ? "" : "s"} · ${money(total)}`}
            </Btn>
            <p className="text-xs text-slate-400 text-center">Charged to your room bill on delivery.</p>
          </div>
        </div>
      )}
    </>
  );
}
