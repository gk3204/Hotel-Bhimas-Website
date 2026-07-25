import React, { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiRequest } from "../../api/api";

const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

const ID_TYPES = [
  { value: "aadhaar", label: "Aadhaar" },
  { value: "passport", label: "Passport" },
  { value: "driving_licence", label: "Driving Licence" },
  { value: "voter_id", label: "Voter ID" },
  { value: "other", label: "Other" },
];

export default function PreArrival() {
  const { token } = useParams();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [booking, setBooking] = useState(null);
  const [done, setDone] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);

  const [form, setForm] = useState({
    name: "", phone: "", email: "", id_type: "aadhaar", id_number: "",
    id_photo_url: "", address: "", dob: "", gstin: "", marketing_optin: false,
  });

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => {
    (async () => {
      try {
        const data = await apiRequest(`/crm/pre-arrival/${token}`);
        setBooking(data.booking);
        if (data.status === "submitted" || data.status === "verified") {
          setDone(true);
        } else {
          setForm((f) => ({
            ...f,
            name: data.booking.guest_name || "",
            phone: data.booking.phone || "",
            email: data.booking.email || "",
          }));
        }
      } catch (e) {
        setError(e.message || "This link is not valid.");
      } finally {
        setLoading(false);
      }
    })();
  }, [token]);

  const handleUpload = async (file) => {
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${BASE_URL}/crm/pre-arrival/${token}/upload`, {
        method: "POST",
        body: fd,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Upload failed");
      }
      const data = await res.json();
      set("id_photo_url", data.url);
    } catch (e) {
      setError(e.message || "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const submit = async () => {
    if (!form.name || !form.phone) {
      setError("Please enter your name and phone number.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await apiRequest(`/crm/pre-arrival/${token}`, {
        method: "POST",
        body: JSON.stringify(form),
      });
      setDone(true);
    } catch (e) {
      setError(e.message || "Could not submit. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-slate-100 py-10 px-4">
      <div className="max-w-xl mx-auto">
        <div className="text-center mb-6">
          <h1 className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-[#B8860B] to-[#D4AF37]">
            Hotel Bhimas
          </h1>
          <p className="text-slate-600 mt-1">Pre-arrival registration</p>
        </div>

        <div className="bg-white rounded-2xl shadow-xl border border-slate-200 p-6">
          {loading ? (
            <div className="py-16 flex justify-center">
              <div className="animate-spin h-10 w-10 border-4 border-[#D4AF37] border-t-transparent rounded-full"></div>
            </div>
          ) : error && !booking ? (
            <div className="text-center py-10">
              <div className="text-4xl mb-3">🔒</div>
              <p className="text-slate-700">{error}</p>
            </div>
          ) : done ? (
            <div className="text-center py-10">
              <div className="text-5xl mb-3">✅</div>
              <h2 className="text-xl font-bold text-slate-800 mb-1">Thank you!</h2>
              <p className="text-slate-600">
                Your details are saved. Our front desk will verify them on arrival —
                check-in will be quick.
              </p>
            </div>
          ) : (
            <>
              {booking && (
                <div className="mb-5 p-3 rounded-lg bg-amber-50 border border-amber-200 text-sm text-slate-700">
                  <b>Your stay:</b> {booking.check_in} → {booking.check_out}
                  {booking.rooms?.length > 0 && (
                    <> · {booking.rooms.map((r) => `${r.quantity}× ${r.room_type}`).join(", ")}</>
                  )}
                </div>
              )}

              <div className="space-y-4">
                <L label="Full name">
                  <In value={form.name} onChange={(v) => set("name", v)} placeholder="As on your ID" />
                </L>
                <div className="grid grid-cols-2 gap-4">
                  <L label="Phone">
                    <In value={form.phone} onChange={(v) => set("phone", v)} placeholder="10-digit mobile" />
                  </L>
                  <L label="Email">
                    <In value={form.email} onChange={(v) => set("email", v)} placeholder="you@example.com" />
                  </L>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <L label="ID type">
                    <select
                      value={form.id_type}
                      onChange={(e) => set("id_type", e.target.value)}
                      className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:border-[#D4AF37] focus:ring-2 focus:ring-[#D4AF37]/20"
                    >
                      {ID_TYPES.map((t) => (
                        <option key={t.value} value={t.value}>{t.label}</option>
                      ))}
                    </select>
                  </L>
                  <L label="ID number">
                    <In value={form.id_number} onChange={(v) => set("id_number", v)} placeholder="Stored securely, masked" />
                  </L>
                </div>

                <L label="ID photo">
                  <input
                    type="file"
                    accept="image/*,application/pdf"
                    onChange={(e) => handleUpload(e.target.files?.[0])}
                    className="block w-full text-sm text-slate-600 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-[#D4AF37] file:text-white file:font-semibold hover:file:bg-[#B8860B]"
                  />
                  {uploading && <p className="text-xs text-slate-500 mt-1">Uploading…</p>}
                  {form.id_photo_url && !uploading && (
                    <p className="text-xs text-green-600 mt-1">✓ ID photo uploaded</p>
                  )}
                </L>

                <L label="Address">
                  <In value={form.address} onChange={(v) => set("address", v)} placeholder="Street, city, state" />
                </L>
                <div className="grid grid-cols-2 gap-4">
                  <L label="Date of birth">
                    <In type="date" value={form.dob} onChange={(v) => set("dob", v)} />
                  </L>
                  <L label="GSTIN (optional)">
                    <In value={form.gstin} onChange={(v) => set("gstin", v)} placeholder="For a GST invoice" />
                  </L>
                </div>

                <label className="flex items-center gap-2 text-sm text-slate-600">
                  <input
                    type="checkbox"
                    checked={form.marketing_optin}
                    onChange={(e) => set("marketing_optin", e.target.checked)}
                  />
                  Keep me posted on offers and updates
                </label>

                {error && <p className="text-sm text-red-600">{error}</p>}

                <button
                  onClick={submit}
                  disabled={submitting}
                  className="w-full bg-gradient-to-r from-[#D4AF37] to-[#B8860B] text-white font-bold py-3 rounded-lg hover:shadow-lg transition disabled:opacity-60"
                >
                  {submitting ? "Submitting…" : "Complete registration"}
                </button>
              </div>
            </>
          )}
        </div>
        <p className="text-center text-xs text-slate-400 mt-4">
          Your ID number is stored masked. We use these details only to speed up your check-in.
        </p>
      </div>
    </div>
  );
}

const L = ({ label, children }) => (
  <div className="flex flex-col">
    <label className="mb-1.5 text-sm font-semibold text-slate-700">{label}</label>
    {children}
  </div>
);

const In = ({ value, onChange, placeholder, type = "text" }) => (
  <input
    type={type}
    value={value}
    onChange={(e) => onChange(e.target.value)}
    placeholder={placeholder}
    className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:border-[#D4AF37] focus:ring-2 focus:ring-[#D4AF37]/20"
  />
);
