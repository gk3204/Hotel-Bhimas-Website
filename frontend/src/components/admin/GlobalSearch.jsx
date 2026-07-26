import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FaSearch, FaUser } from "react-icons/fa";
import { listGuests } from "../../api/crm";

// Cmd/Ctrl+K command palette to find a returning guest by name / phone / email.
// Opens on the shortcut or the custom "admin:open-search" window event (fired by the sidebar button).
// Mount ONCE (in AdminLayout). Selecting a guest deep-links to the Guest Directory filtered to them.
export default function GlobalSearch() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef(null);
  const navigate = useNavigate();

  // Open triggers: Cmd/Ctrl+K and the sidebar button's custom event.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("admin:open-search", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("admin:open-search", onOpen);
    };
  }, []);

  // Reset + focus when opened.
  useEffect(() => {
    if (open) {
      setQ("");
      setResults([]);
      setTimeout(() => inputRef.current?.focus(), 40);
    }
  }, [open]);

  // Debounced guest search.
  useEffect(() => {
    if (!open) return undefined;
    const term = q.trim();
    if (term.length < 2) {
      setResults([]);
      return undefined;
    }
    setLoading(true);
    const id = setTimeout(async () => {
      try {
        const data = await listGuests({ q: term });
        setResults((data?.data || []).slice(0, 8));
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => clearTimeout(id);
  }, [q, open]);

  if (!open) return null;

  const goGuest = (g) => {
    setOpen(false);
    navigate(`/admin/guests?q=${encodeURIComponent(g.phone || g.name || "")}`);
  };

  const goSearchAll = () => {
    const term = q.trim();
    if (!term) return;
    setOpen(false);
    navigate(`/admin/guests?q=${encodeURIComponent(term)}`);
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center p-4 pt-24">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setOpen(false)} />
      <div className="relative w-full max-w-xl bg-slate-800 border border-slate-700 rounded-2xl shadow-2xl overflow-hidden">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-700">
          <FaSearch className="text-slate-500" size={14} />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && goSearchAll()}
            placeholder="Search guests by name, phone or email…"
            className="flex-1 bg-transparent text-white placeholder-slate-500 focus:outline-none text-sm"
          />
          <kbd className="text-[10px] text-slate-500 border border-slate-600 rounded px-1.5 py-0.5">esc</kbd>
        </div>

        <div className="max-h-80 overflow-y-auto">
          {q.trim().length < 2 ? (
            <p className="px-4 py-6 text-center text-slate-500 text-sm">Type at least 2 characters.</p>
          ) : loading ? (
            <p className="px-4 py-6 text-center text-slate-400 text-sm">Searching…</p>
          ) : results.length === 0 ? (
            <p className="px-4 py-6 text-center text-slate-400 text-sm">No matching guest.</p>
          ) : (
            <ul className="py-1">
              {results.map((g) => (
                <li key={g.guest_id}>
                  <button
                    onClick={() => goGuest(g)}
                    className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-slate-700/60 transition"
                  >
                    <FaUser className="text-slate-500 shrink-0" size={13} />
                    <div className="min-w-0 flex-1">
                      <div className="text-slate-100 text-sm truncate">{g.name}</div>
                      <div className="text-slate-500 text-xs truncate">
                        {g.phone}{g.stay_count ? ` · ${g.stay_count} stay(s)` : ""}
                      </div>
                    </div>
                    {g.profile?.vip && (
                      <span className="text-[10px] font-bold text-amber-300 border border-amber-500/40 rounded-full px-2 py-0.5">★ VIP</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {q.trim().length >= 2 && (
          <button
            onClick={goSearchAll}
            className="w-full px-4 py-2.5 border-t border-slate-700 text-sm text-[#E5C07B] hover:bg-slate-700/40 transition text-left"
          >
            Open Guest Directory for “{q.trim()}” →
          </button>
        )}
      </div>
    </div>
  );
}
