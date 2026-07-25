import React, { useEffect, useState } from "react";
import { FaStar, FaSyncAlt, FaGoogle, FaCheck, FaBan, FaMagic, FaFlask } from "react-icons/fa";
import {
  listReviews,
  approveReview,
  skipReview,
  regenerateReply,
  getReviewConfig,
  updateReviewConfig,
  pollReviews,
  injectTestReview,
} from "../../api/reviews";

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

const statusChip = (s) => {
  const map = {
    pending_generation: "bg-slate-600/30 text-slate-300 border-slate-600/40",
    pending_approval: "bg-amber-500/20 text-amber-300 border-amber-500/30",
    auto_sent: "bg-green-500/20 text-green-300 border-green-500/30",
    approved_sent: "bg-green-500/20 text-green-300 border-green-500/30",
    skipped: "bg-slate-600/30 text-slate-400 border-slate-600/40",
    failed: "bg-red-500/20 text-red-300 border-red-500/30",
  };
  return map[s] || "bg-slate-600/30 text-slate-300 border-slate-600/40";
};

const Stars = ({ n }) => (
  <span className="inline-flex items-center gap-0.5 text-[#E5C07B]">
    {[1, 2, 3, 4, 5].map((i) => (
      <FaStar key={i} size={12} className={i <= n ? "" : "text-slate-600"} />
    ))}
    <span className="ml-1 text-slate-300 text-xs font-semibold">{n}/5</span>
  </span>
);

const cardCls =
  "bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl backdrop-blur";

export default function Reviews() {
  const [tab, setTab] = useState("inbox");
  const [reviews, setReviews] = useState([]);
  const [counts, setCounts] = useState({});
  const [filters, setFilters] = useState({ status: "", rating_max: "" });
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const [edits, setEdits] = useState({}); // id -> edited reply text
  const [cfg, setCfg] = useState(null);
  const [inject, setInject] = useState({ rating: 5, author_name: "", review_text: "" });

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  const load = async () => {
    setLoading(true);
    try {
      const params =
        tab === "queue"
          ? { pending_approval: true }
          : { status: filters.status || undefined, rating_max: filters.rating_max || undefined };
      const data = await listReviews(params);
      setReviews(data.data || []);
      setCounts(data.counts || {});
    } catch (e) {
      showToast(e.message, "error");
    }
    setLoading(false);
  };

  const loadConfig = async () => {
    try {
      setCfg(await getReviewConfig());
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  useEffect(() => {
    if (tab === "config") loadConfig();
    else load();
    // eslint-disable-next-line
  }, [tab, filters]);

  const doPoll = async () => {
    setBusy(true);
    try {
      const { result } = await pollReviews();
      showToast(`Sweep done — polled ${result.poll}, generated ${result.generate}, posted ${result.post}`);
      await load();
    } catch (e) {
      showToast(e.message, "error");
    }
    setBusy(false);
  };

  const doApprove = async (r) => {
    setBusy(true);
    try {
      await approveReview(r.id, edits[r.id] ?? r.reply_text);
      showToast(`Reply posted for review #${r.id}`);
      await load();
    } catch (e) {
      showToast(e.message, "error");
    }
    setBusy(false);
  };

  const doSkip = async (r) => {
    setBusy(true);
    try {
      await skipReview(r.id, "Skipped by admin");
      showToast(`Review #${r.id} skipped`);
      await load();
    } catch (e) {
      showToast(e.message, "error");
    }
    setBusy(false);
  };

  const doRegen = async (r) => {
    setBusy(true);
    try {
      const updated = await regenerateReply(r.id);
      setEdits({ ...edits, [r.id]: updated.reply_text });
      showToast(`Regenerated draft for review #${r.id}`);
      await load();
    } catch (e) {
      showToast(e.message, "error");
    }
    setBusy(false);
  };

  const doInject = async () => {
    setBusy(true);
    try {
      await injectTestReview({
        rating: Number(inject.rating),
        author_name: inject.author_name || undefined,
        review_text: inject.review_text || undefined,
      });
      showToast("Test review injected — run Poll to process it");
    } catch (e) {
      showToast(e.message, "error");
    }
    setBusy(false);
  };

  const saveConfig = async (patch) => {
    setBusy(true);
    try {
      setCfg(await updateReviewConfig(patch));
      showToast("Config saved");
    } catch (e) {
      showToast(e.message, "error");
    }
    setBusy(false);
  };

  const posted = (s) => s === "auto_sent" || s === "approved_sent";

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-6 flex items-start justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent flex items-center gap-3">
              <FaGoogle /> Reviews
            </h1>
            <p className="text-slate-400">
              Google reviews auto-reply. High ratings get an automatic thank-you; low ratings wait for
              your approval, with an owner alert and a follow-up ticket.
            </p>
          </div>
          <button
            onClick={doPoll}
            disabled={busy}
            className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition flex items-center gap-2 disabled:opacity-50"
          >
            <FaSyncAlt size={13} className={busy ? "animate-spin" : ""} /> Poll now
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-2 mb-6 flex-wrap">
          {[
            ["inbox", "Inbox"],
            ["queue", `Approval queue${counts.pending_approval ? ` (${counts.pending_approval})` : ""}`],
            ["config", "Config"],
          ].map(([k, label]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`px-5 py-2 rounded-lg font-semibold transition border ${
                tab === k
                  ? "bg-[#E5C07B]/20 text-[#E5C07B] border-[#E5C07B]/40"
                  : "bg-slate-800/50 text-slate-300 border-slate-700 hover:bg-slate-700/50"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === "inbox" && (
          <div className={`${cardCls} p-5 mb-6 flex flex-wrap items-end gap-4`}>
            <div className="flex flex-col">
              <label className="mb-1 text-sm font-semibold text-slate-300">Status</label>
              <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} className={inputCls}>
                {["", "pending_generation", "pending_approval", "auto_sent", "approved_sent", "skipped", "failed"].map((s) => (
                  <option key={s} value={s}>{s || "All"}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col">
              <label className="mb-1 text-sm font-semibold text-slate-300">Max rating</label>
              <select value={filters.rating_max} onChange={(e) => setFilters({ ...filters, rating_max: e.target.value })} className={inputCls}>
                {["", "1", "2", "3", "4", "5"].map((s) => (
                  <option key={s} value={s}>{s || "Any"}</option>
                ))}
              </select>
            </div>
            <button onClick={load} className="bg-slate-700 hover:bg-slate-600 font-semibold px-6 py-2.5 rounded-lg transition flex items-center gap-2">
              <FaSyncAlt size={13} /> Refresh
            </button>
          </div>
        )}

        {/* Config tab */}
        {tab === "config" && (
          <ConfigPanel cfg={cfg} busy={busy} onSave={saveConfig} inject={inject} setInject={setInject} onInject={doInject} />
        )}

        {/* Review list (inbox + queue) */}
        {tab !== "config" && (
          <div className={`${cardCls} overflow-hidden`}>
            {loading ? (
              <div className="p-12 flex items-center justify-center">
                <div className="animate-spin"><div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div></div>
              </div>
            ) : reviews.length === 0 ? (
              <div className="p-12 text-center text-slate-400">
                <div className="text-5xl mb-4">⭐</div>
                <p>{tab === "queue" ? "No drafts awaiting approval." : "No reviews yet. Inject a test review from the Config tab, then Poll."}</p>
              </div>
            ) : (
              <div className="divide-y divide-slate-700">
                {reviews.map((r) => (
                  <div key={r.id} className="p-5">
                    <div className="flex items-start justify-between gap-4 flex-wrap">
                      <div className="min-w-0">
                        <div className="flex items-center gap-3 flex-wrap">
                          <Stars n={r.rating} />
                          <span className="font-semibold text-slate-200">{r.author_name || "Anonymous"}</span>
                          <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold border ${statusChip(r.reply_status)}`}>
                            {r.reply_status}
                          </span>
                          {r.matched_guest && (
                            <span className="px-2.5 py-0.5 rounded-full text-xs bg-blue-500/15 text-blue-300 border border-blue-500/30">
                              matched: {r.matched_guest.name}
                            </span>
                          )}
                          {r.ticket && (
                            <span className="px-2.5 py-0.5 rounded-full text-xs bg-purple-500/15 text-purple-300 border border-purple-500/30">
                              ticket #{r.ticket.id} · {r.ticket.status}
                            </span>
                          )}
                        </div>
                        {r.review_text && <p className="text-slate-300 mt-2 whitespace-pre-wrap">{r.review_text}</p>}
                        {r.scheduled_post_at && !posted(r.reply_status) && (
                          <p className="text-xs text-slate-500 mt-1">Auto-post scheduled for {new Date(r.scheduled_post_at).toLocaleString()}</p>
                        )}
                        {r.replied_at && <p className="text-xs text-green-400/80 mt-1">Replied {new Date(r.replied_at).toLocaleString()}</p>}
                        {r.last_error && <p className="text-xs text-red-400 mt-1">Last error: {r.last_error}</p>}
                      </div>
                    </div>

                    {/* Reply block */}
                    <div className="mt-3 bg-slate-900/40 border border-slate-700 rounded-xl p-3">
                      <div className="text-xs font-semibold text-slate-400 mb-1">Our reply</div>
                      {posted(r.reply_status) ? (
                        <p className="text-slate-200 whitespace-pre-wrap">{r.reply_text || "—"}</p>
                      ) : (
                        <>
                          <textarea
                            className={`${inputCls} w-full min-h-[70px] resize-y`}
                            value={edits[r.id] ?? r.reply_text ?? ""}
                            onChange={(e) => setEdits({ ...edits, [r.id]: e.target.value })}
                            placeholder="Reply text..."
                          />
                          <div className="flex gap-2 mt-2 flex-wrap">
                            <button onClick={() => doApprove(r)} disabled={busy} className="bg-green-600/80 hover:bg-green-600 text-white font-semibold px-4 py-2 rounded-lg transition flex items-center gap-2 disabled:opacity-50">
                              <FaCheck size={12} /> Approve &amp; post
                            </button>
                            <button onClick={() => doRegen(r)} disabled={busy} className="bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2 rounded-lg transition flex items-center gap-2 disabled:opacity-50">
                              <FaMagic size={12} /> Regenerate
                            </button>
                            <button onClick={() => doSkip(r)} disabled={busy} className="bg-red-600/70 hover:bg-red-600 text-white font-semibold px-4 py-2 rounded-lg transition flex items-center gap-2 disabled:opacity-50">
                              <FaBan size={12} /> Skip
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {toast && (
        <div className={`fixed bottom-6 right-6 px-5 py-3 rounded-xl shadow-2xl font-semibold z-50 ${toast.type === "error" ? "bg-red-600" : "bg-green-600"}`}>
          {toast.message}
        </div>
      )}
    </div>
  );
}

function Field({ label, children, hint }) {
  return (
    <div className="flex flex-col">
      <label className="mb-1 text-sm font-semibold text-slate-300">{label}</label>
      {children}
      {hint && <span className="text-xs text-slate-500 mt-1">{hint}</span>}
    </div>
  );
}

function Toggle({ label, value, onChange, hint }) {
  return (
    <label className="flex items-center justify-between gap-3 bg-slate-900/40 border border-slate-700 rounded-lg px-4 py-3 cursor-pointer">
      <span>
        <span className="text-slate-200 font-semibold text-sm">{label}</span>
        {hint && <span className="block text-xs text-slate-500">{hint}</span>}
      </span>
      <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} className="w-5 h-5 accent-[#E5C07B]" />
    </label>
  );
}

function ConfigPanel({ cfg, busy, onSave, inject, setInject, onInject }) {
  const inputCls2 = inputCls;
  if (!cfg) {
    return (
      <div className="p-12 flex items-center justify-center">
        <div className="animate-spin"><div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div></div>
      </div>
    );
  }
  return (
    <div className="space-y-6">
      <div className={`${cardCls} p-6`}>
        <h2 className="text-xl font-bold text-slate-100 mb-4">Automation</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Toggle label="Auto-reply enabled" value={cfg.auto_reply_enabled} hint="Master switch for auto-posting thank-yous" onChange={(v) => onSave({ auto_reply_enabled: v })} />
          <Toggle label="Owner alerts on low reviews" value={cfg.owner_alerts_enabled} hint="WhatsApp the owner when a review is below threshold" onChange={(v) => onSave({ owner_alerts_enabled: v })} />
          <Toggle label="Auto-send low-rating drafts" value={cfg.low_auto_send} hint="Off = low ratings wait in the approval queue" onChange={(v) => onSave({ low_auto_send: v })} />
          <Toggle label="LLM generation (Claude)" value={cfg.llm_enabled} hint={cfg.llm_configured ? "ANTHROPIC_API_KEY is set" : "Set ANTHROPIC_API_KEY to use"} onChange={(v) => onSave({ llm_enabled: v })} />
          <Field label="Auto-reply threshold (≥ stars auto-post)">
            <select value={cfg.auto_reply_threshold} onChange={(e) => onSave({ auto_reply_threshold: Number(e.target.value) })} className={inputCls2}>
              {[3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </Field>
          <Field label="Low-band split (≤ uses the harsher apology)">
            <select value={cfg.low_band_split} onChange={(e) => onSave({ low_band_split: Number(e.target.value) })} className={inputCls2}>
              {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </Field>
          <Field label="Delay min (hours)" hint="Randomized posting delay">
            <input type="number" min="0" step="0.5" defaultValue={cfg.delay_min_hours} onBlur={(e) => onSave({ delay_min_hours: Number(e.target.value) })} className={inputCls2} />
          </Field>
          <Field label="Delay max (hours)">
            <input type="number" min="0" step="0.5" defaultValue={cfg.delay_max_hours} onBlur={(e) => onSave({ delay_max_hours: Number(e.target.value) })} className={inputCls2} />
          </Field>
          <Field label="Poll interval (minutes)">
            <input type="number" min="1" defaultValue={cfg.poll_interval_minutes} onBlur={(e) => onSave({ poll_interval_minutes: Number(e.target.value) })} className={inputCls2} />
          </Field>
        </div>
        <p className="text-xs text-slate-500 mt-4">
          Google Business Profile: {cfg.gbp_provider?.configured ? "connected ✓" : "not configured (replies are stub-logged until GBP_* env is set)"}.
        </p>
      </div>

      {/* Template pool (read-only) */}
      <div className={`${cardCls} p-6`}>
        <h2 className="text-xl font-bold text-slate-100 mb-3">Template pool <span className="text-sm font-normal text-slate-500">(read-only — editable pools are a follow-up)</span></h2>
        {[["thank_you", "Thank-you (≥ threshold)"], ["recovery_low", "Service recovery — 1-2★"], ["recovery_mid", "Service recovery — 3★"]].map(([k, label]) => (
          <div key={k} className="mb-4">
            <div className="text-sm font-semibold text-[#E5C07B] mb-1">{label}</div>
            <ul className="list-disc list-inside text-slate-400 text-sm space-y-1">
              {(cfg.templates?.[k] || []).map((t, i) => <li key={i}>{t.replace("{name}", "")}</li>)}
            </ul>
          </div>
        ))}
      </div>

      {/* Dev: inject test review */}
      {cfg.test_inject_enabled && (
        <div className={`${cardCls} p-6`}>
          <h2 className="text-xl font-bold text-slate-100 mb-1 flex items-center gap-2"><FaFlask /> Inject test review</h2>
          <p className="text-slate-500 text-sm mb-4">Dev only — drives the full pipeline without live Google. Disable in production with REVIEW_TEST_INJECT_ENABLED=false.</p>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
            <Field label="Rating">
              <select value={inject.rating} onChange={(e) => setInject({ ...inject, rating: e.target.value })} className={inputCls2}>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </Field>
            <Field label="Author name">
              <input value={inject.author_name} onChange={(e) => setInject({ ...inject, author_name: e.target.value })} className={inputCls2} placeholder="Test Reviewer" />
            </Field>
            <div className="md:col-span-2">
              <Field label="Review text">
                <input value={inject.review_text} onChange={(e) => setInject({ ...inject, review_text: e.target.value })} className={inputCls2} placeholder="Optional review body" />
              </Field>
            </div>
          </div>
          <button onClick={onInject} disabled={busy} className="mt-4 bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition disabled:opacity-50">
            Inject
          </button>
        </div>
      )}
    </div>
  );
}
