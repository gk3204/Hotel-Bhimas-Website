// In-room guest portal (prompt 18d, slice 10). PUBLIC, token-scoped (no login) — the page behind
// the in-room QR. Light, self-contained brand like PreArrival.jsx; uses the no-bearer apiRequest.
import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiRequest } from "../../api/api";

const money = (v) => `₹${Number(v || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const STATUS_LABEL = {
  requested: "Received", acknowledged: "In progress", completed: "Done", dismissed: "Closed",
};
const TYPE_LABEL = {
  room_service: "Room service", wifi: "WiFi", wakeup: "Wake-up call", cab: "Cab", checkout: "Checkout",
};

export default function GuestPortal() {
  const { token } = useParams();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [data, setData] = useState(null);
  const [toast, setToast] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await apiRequest(`/portal/${token}`));
    } catch (e) {
      setError(e.message || "This link is not valid.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const flash = (m) => { setToast(m); setTimeout(() => setToast(""), 3500); };

  if (loading) {
    return (
      <Shell>
        <div className="py-16 flex justify-center">
          <div className="animate-spin h-10 w-10 border-4 border-[#D4AF37] border-t-transparent rounded-full" />
        </div>
      </Shell>
    );
  }
  if (error && !data) {
    return (
      <Shell>
        <div className="text-center py-10">
          <div className="text-4xl mb-3">🔒</div>
          <p className="text-slate-700">{error}</p>
        </div>
      </Shell>
    );
  }
  if (!data.active) {
    return (
      <Shell>
        <div className="text-center py-10">
          <div className="text-4xl mb-3">👋</div>
          <p className="text-slate-700">This stay is checked out. Thank you for staying with us!</p>
        </div>
      </Shell>
    );
  }

  const f = data.features;
  return (
    <Shell subtitle={`Room ${data.room_number || "-"} · Welcome${data.guest_name ? ", " + data.guest_name : ""}`}>
      {toast && (
        <div className="fixed top-4 inset-x-4 z-50 bg-slate-800 text-white text-sm px-4 py-3 rounded-xl shadow-lg text-center">
          {toast}
        </div>
      )}

      <div className="space-y-4">
        {f.room_service && <RoomService token={token} menu={data.menu} onOrdered={(m) => { flash(m); load(); }} setError={setError} />}
        {f.wifi && <Wifi token={token} wifi={data.wifi} onIssued={(m) => { flash(m); load(); }} />}
        {f.wakeup && <Wakeup token={token} onDone={(m) => { flash(m); load(); }} />}
        {f.cab && <Cab token={token} onDone={(m) => { flash(m); load(); }} />}
        {f.contactless_checkout && <Checkout token={token} balance={data.folio.balance} onDone={(m) => { flash(m); load(); }} />}
        <MyRequests requests={data.requests} />
      </div>
    </Shell>
  );
}

/* ---------- shell ---------- */
function Shell({ subtitle, children }) {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-amber-50 to-slate-100 py-8 px-4">
      <div className="max-w-lg mx-auto">
        <div className="text-center mb-5">
          <h1 className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-[#B8860B] to-[#D4AF37]">
            Hotel Bhimas
          </h1>
          {subtitle && <p className="text-slate-600 mt-1 text-sm">{subtitle}</p>}
        </div>
        {children}
        <p className="text-center text-xs text-slate-400 mt-5">
          Requests reach our front desk instantly. For anything urgent, dial 0 from your room phone.
        </p>
      </div>
    </div>
  );
}

function Card({ icon, title, children }) {
  return (
    <div className="bg-white rounded-2xl shadow-md border border-slate-200 p-5">
      <h2 className="font-bold text-slate-800 mb-3 flex items-center gap-2">
        <span className="text-xl">{icon}</span> {title}
      </h2>
      {children}
    </div>
  );
}

function Btn({ children, onClick, disabled, kind = "primary" }) {
  const cls = kind === "primary"
    ? "bg-gradient-to-r from-[#D4AF37] to-[#B8860B] text-white"
    : "bg-slate-100 text-slate-700 border border-slate-300";
  return (
    <button onClick={onClick} disabled={disabled}
      className={`${cls} font-semibold px-4 py-2.5 rounded-lg transition disabled:opacity-60 w-full`}>
      {children}
    </button>
  );
}

const inCls = "w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:border-[#D4AF37] focus:ring-2 focus:ring-[#D4AF37]/20";

/* ---------- room service ---------- */
function RoomService({ token, menu, onOrdered, setError }) {
  const [cart, setCart] = useState({});   // id -> qty
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const bump = (id, d) => setCart((c) => {
    const q = Math.max(0, (c[id] || 0) + d);
    const next = { ...c }; if (q === 0) delete next[id]; else next[id] = q; return next;
  });
  const items = (menu || []).filter((m) => m.is_available);
  const total = items.reduce((s, m) => s + (cart[m.id] || 0) * m.price, 0);
  const count = Object.values(cart).reduce((a, b) => a + b, 0);

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
      setCart({}); setNote("");
      onOrdered("Order placed — our team will bring it up and add it to your bill.");
    } catch (e) {
      setError && setError("");
      onOrdered(e.message || "Could not place the order.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card icon="🍽️" title="Room service">
      {items.length === 0 ? (
        <p className="text-sm text-slate-500">The menu is being updated — please call the desk to order.</p>
      ) : (
        <>
          <div className="divide-y divide-slate-100">
            {items.map((m) => (
              <div key={m.id} className="py-2 flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-slate-800 text-sm font-medium">{m.name}</div>
                  {m.description && <div className="text-xs text-slate-500 truncate">{m.description}</div>}
                  <div className="text-xs text-[#B8860B] font-semibold">{money(m.price)}</div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button onClick={() => bump(m.id, -1)} className="w-7 h-7 rounded-full bg-slate-100 text-slate-700">−</button>
                  <span className="w-5 text-center text-sm">{cart[m.id] || 0}</span>
                  <button onClick={() => bump(m.id, 1)} className="w-7 h-7 rounded-full bg-[#D4AF37] text-white">+</button>
                </div>
              </div>
            ))}
          </div>
          {count > 0 && (
            <div className="mt-3 space-y-2">
              <input className={inCls} value={note} placeholder="Any note (e.g. no onion)"
                onChange={(e) => setNote(e.target.value)} />
              <Btn onClick={order} disabled={busy}>
                {busy ? "Placing…" : `Order ${count} item(s) · ${money(total)}`}
              </Btn>
              <p className="text-xs text-slate-400 text-center">Charged to your room bill on delivery.</p>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

/* ---------- wifi ---------- */
function Wifi({ token, wifi, onIssued }) {
  const [code, setCode] = useState(wifi?.code || null);
  const [busy, setBusy] = useState(false);
  const getCode = async () => {
    setBusy(true);
    try {
      const res = await apiRequest(`/portal/${token}/wifi`, {
        method: "POST", body: JSON.stringify({ client_ref: `wifi-${token}` }),
      });
      if (res.code) { setCode(res.code); onIssued("Here's your WiFi code."); }
      else onIssued("Requested — the desk will send your WiFi code shortly.");
    } catch (e) {
      onIssued(e.message || "Could not get the WiFi code.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card icon="📶" title="WiFi">
      <div className="text-sm text-slate-700">Network: <b>{wifi?.ssid || "—"}</b></div>
      {code ? (
        <div className="mt-2 text-center bg-amber-50 border border-amber-200 rounded-lg py-3">
          <div className="text-xs text-slate-500">Password</div>
          <div className="text-lg font-bold tracking-widest text-[#B8860B]">{code}</div>
        </div>
      ) : (
        <div className="mt-2"><Btn onClick={getCode} disabled={busy}>{busy ? "Getting…" : "Get WiFi password"}</Btn></div>
      )}
    </Card>
  );
}

/* ---------- wake-up ---------- */
function Wakeup({ token, onDone }) {
  const [time, setTime] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!time) return;
    setBusy(true);
    try {
      await apiRequest(`/portal/${token}/wakeup`, {
        method: "POST", body: JSON.stringify({ time, client_ref: `wake-${token}-${time}` }),
      });
      onDone(`Wake-up call set for ${time}.`); setTime("");
    } catch (e) { onDone(e.message || "Could not set the wake-up call."); }
    finally { setBusy(false); }
  };
  return (
    <Card icon="⏰" title="Wake-up call">
      <div className="flex gap-2">
        <input type="time" className={inCls} value={time} onChange={(e) => setTime(e.target.value)} />
        <div className="w-32"><Btn onClick={submit} disabled={busy || !time}>{busy ? "…" : "Set"}</Btn></div>
      </div>
    </Card>
  );
}

/* ---------- cab ---------- */
function Cab({ token, onDone }) {
  const [dest, setDest] = useState("");
  const [time, setTime] = useState("");
  const [pax, setPax] = useState(1);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!dest || dest.trim().length < 2) return;
    setBusy(true);
    try {
      await apiRequest(`/portal/${token}/cab`, {
        method: "POST",
        body: JSON.stringify({ destination: dest.trim(), pickup_time: time || null, passengers: Number(pax) || 1,
          client_ref: `cab-${token}-${Date.now()}` }),
      });
      onDone("Cab requested — the desk will confirm."); setDest(""); setTime("");
    } catch (e) { onDone(e.message || "Could not request a cab."); }
    finally { setBusy(false); }
  };
  return (
    <Card icon="🚕" title="Cab / tour desk">
      <div className="space-y-2">
        <input className={inCls} value={dest} placeholder="Where to?" onChange={(e) => setDest(e.target.value)} />
        <div className="flex gap-2">
          <input type="time" className={inCls} value={time} onChange={(e) => setTime(e.target.value)} />
          <input type="number" min="1" className={inCls} value={pax} onChange={(e) => setPax(e.target.value)} />
        </div>
        <Btn onClick={submit} disabled={busy}>{busy ? "Requesting…" : "Request a cab"}</Btn>
      </div>
    </Card>
  );
}

/* ---------- contactless checkout ---------- */
function Checkout({ token, balance, onDone }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const submit = async () => {
    setBusy(true);
    try {
      const res = await apiRequest(`/portal/${token}/checkout`, {
        method: "POST", body: JSON.stringify({ note: note || null, client_ref: `co-${token}` }),
      });
      onDone(res.message || "Checkout requested — please leave your key card at the desk.");
    } catch (e) { onDone(e.message || "Could not request checkout."); }
    finally { setBusy(false); }
  };
  return (
    <Card icon="🧳" title="Contactless checkout">
      <p className="text-sm text-slate-600 mb-2">
        Your current bill: <b>{money(balance)}</b>. Tap below and drop your key card at the desk — we'll
        bring your final bill over and settle any balance.
      </p>
      <input className={inCls + " mb-2"} value={note} placeholder="Anything we should know? (optional)"
        onChange={(e) => setNote(e.target.value)} />
      <Btn onClick={submit} disabled={busy}>{busy ? "Sending…" : "Request checkout"}</Btn>
    </Card>
  );
}

/* ---------- my requests ---------- */
function MyRequests({ requests }) {
  if (!requests || requests.length === 0) return null;
  return (
    <Card icon="🧾" title="Your requests">
      <div className="divide-y divide-slate-100">
        {requests.map((r) => (
          <div key={r.id} className="py-2 flex items-center justify-between gap-3 text-sm">
            <div>
              <span className="text-slate-800">{TYPE_LABEL[r.type] || r.type}</span>
              {r.amount != null && <span className="text-slate-500"> · {money(r.amount)}</span>}
              {r.note && <div className="text-xs text-slate-500 truncate max-w-[220px]">{r.note}</div>}
            </div>
            <span className={`text-xs font-semibold px-2 py-1 rounded-full ${
              r.status === "completed" ? "bg-emerald-100 text-emerald-700"
                : r.status === "dismissed" ? "bg-slate-100 text-slate-500"
                : "bg-amber-100 text-amber-700"}`}>
              {STATUS_LABEL[r.status] || r.status}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}
