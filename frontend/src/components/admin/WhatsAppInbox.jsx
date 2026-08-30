// WhatsApp staff inbox (two-way conversations) — used inside the admin Messaging page.
import { useEffect, useRef, useState } from "react";
import { FaPlus } from "react-icons/fa";
import {
  getConversations, getThread, markConversationRead, sendReply, getTemplates,
} from "../../api/whatsapp";

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

export default function WhatsAppInbox({ showToast }) {
  const [convos, setConvos] = useState([]);
  const [active, setActive] = useState(null);   // selected phone
  const [thread, setThread] = useState(null);   // { messages, within_window, guest_name, ... }
  const [text, setText] = useState("");
  const [templates, setTemplates] = useState([]);
  const [tpl, setTpl] = useState("");
  const [tplParams, setTplParams] = useState({});
  const [compose, setCompose] = useState(false);
  const [composePhone, setComposePhone] = useState("");
  const [sending, setSending] = useState(false);
  const activeRef = useRef(active);
  activeRef.current = active;

  const loadConvos = async () => {
    try { setConvos((await getConversations()).data || []); } catch { /* transient */ }
  };
  const loadThread = async (phone, markRead = true) => {
    try {
      const t = await getThread(phone);
      setThread(t);
      if (markRead) { await markConversationRead(phone); loadConvos(); }
    } catch (e) { showToast(e.message, "error"); }
  };

  useEffect(() => {
    loadConvos();
    getTemplates().then((r) => setTemplates(r.templates || [])).catch(() => {});
    const id = setInterval(async () => {
      await loadConvos();
      const p = activeRef.current;
      if (p) {
        const t = await getThread(p).catch(() => null);
        if (t) { setThread(t); markConversationRead(p).catch(() => {}); }
      }
    }, 15000);
    return () => clearInterval(id);
  }, []); // eslint-disable-line

  const openConv = (phone) => { setActive(phone); setCompose(false); setText(""); loadThread(phone); };
  const tplObj = templates.find((t) => t.name === tpl);
  const canFreeText = thread?.within_window && !compose;

  const doSendText = async () => {
    if (!active || !text.trim()) return;
    setSending(true);
    try { await sendReply(active, { text: text.trim() }); setText(""); await loadThread(active); }
    catch (e) { showToast(e.message, "error"); }
    setSending(false);
  };
  const doSendTemplate = async () => {
    const phone = compose ? composePhone.trim() : active;
    if (!phone) { showToast("Enter a phone number", "error"); return; }
    if (!tpl) { showToast("Pick a template", "error"); return; }
    setSending(true);
    try {
      const msg = await sendReply(phone, { template: tpl, params: tplParams });
      showToast("Template sent");
      setCompose(false); setTpl(""); setTplParams({});
      await loadConvos();
      setActive(msg.to || phone);
      await loadThread(msg.to || phone);
    } catch (e) { showToast(e.message, "error"); }
    setSending(false);
  };

  const TemplatePicker = () => (
    <div className="space-y-2">
      <select className={inputCls + " w-full"} value={tpl} onChange={(e) => { setTpl(e.target.value); setTplParams({}); }}>
        <option value="">Choose an approved template…</option>
        {templates.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
      </select>
      {tplObj?.body_preview && <p className="text-xs text-slate-500">{tplObj.body_preview}</p>}
      {(tplObj?.param_order || []).map((p) => (
        <div key={p} className="flex items-center gap-2">
          <span className="text-xs text-slate-400 w-32 shrink-0">{p}</span>
          <input className={inputCls + " flex-1"} value={tplParams[p] || ""}
            onChange={(e) => setTplParams((s) => ({ ...s, [p]: e.target.value }))} />
        </div>
      ))}
    </div>
  );

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {/* conversation list */}
      <div className="bg-slate-800/40 border border-slate-700 rounded-2xl p-2 md:h-[70vh] overflow-y-auto">
        <div className="flex justify-between items-center px-2 py-2">
          <span className="text-slate-400 text-sm">{convos.length} conversation(s)</span>
          <button onClick={() => { setCompose(true); setActive(null); setThread(null); setTpl(""); setTplParams({}); }}
            className="text-[#E5C07B] hover:underline text-sm flex items-center gap-1"><FaPlus size={11} /> New</button>
        </div>
        {convos.length === 0 && <p className="text-slate-500 text-sm px-2 py-3">No conversations yet — guest replies appear here.</p>}
        {convos.map((c) => (
          <button key={c.phone} onClick={() => openConv(c.phone)}
            className={`w-full text-left px-3 py-2.5 rounded-lg mb-1 transition ${active === c.phone ? "bg-slate-700/70" : "hover:bg-slate-700/40"}`}>
            <div className="flex justify-between items-start gap-2">
              <span className="font-semibold text-slate-100 truncate">{c.guest_name || c.phone}</span>
              {c.unread_count > 0 && <span className="bg-[#D4AF37] text-slate-900 text-xs font-bold rounded-full px-2 py-0.5">{c.unread_count}</span>}
            </div>
            <div className="text-xs text-slate-400 truncate">{c.last_direction === "out" ? "You: " : ""}{c.last_body}</div>
          </button>
        ))}
      </div>

      {/* thread + reply */}
      <div className="md:col-span-2 bg-slate-800/40 border border-slate-700 rounded-2xl flex flex-col md:h-[70vh]">
        {compose ? (
          <div className="p-5 space-y-3">
            <h3 className="text-lg font-semibold text-white">New WhatsApp message</h3>
            <p className="text-xs text-slate-400">A new chat must start with an approved template (WhatsApp blocks cold free-text). The guest can then reply and you can chat freely for 24 hours.</p>
            <input className={inputCls + " w-full"} placeholder="Phone number (e.g. 9876543210)"
              value={composePhone} onChange={(e) => setComposePhone(e.target.value)} />
            <TemplatePicker />
            <div className="flex gap-2">
              <button disabled={sending} onClick={doSendTemplate}
                className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-semibold px-5 py-2 rounded-lg disabled:opacity-50">
                {sending ? "Sending…" : "Send"}
              </button>
              <button onClick={() => setCompose(false)} className="px-4 py-2 rounded-lg border border-slate-600 text-slate-300">Cancel</button>
            </div>
          </div>
        ) : !active ? (
          <div className="flex-1 flex items-center justify-center text-slate-500 text-sm p-6">
            Select a conversation to read it and reply, or start a new message.
          </div>
        ) : (
          <>
            <div className="px-5 py-3 border-b border-slate-700 font-semibold text-white">
              {thread?.guest_name || active}
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-2">
              {(thread?.messages || []).map((m) => (
                <div key={m.id} className={`flex ${m.direction === "in" ? "justify-start" : "justify-end"}`}>
                  <div className={`max-w-[75%] rounded-xl px-3 py-2 ${m.direction === "in" ? "bg-slate-700/60" : "bg-[#3b3320]"}`}>
                    <div className="text-sm text-slate-100 whitespace-pre-wrap break-words">{m.body}</div>
                    <div className="text-[10px] text-slate-400 text-right mt-1">
                      {(m.created_at || "").replace("T", " ").slice(0, 16)}
                      {m.direction !== "in" && ` · ${m.status}`}
                    </div>
                  </div>
                </div>
              ))}
            </div>
            <div className="p-4 border-t border-slate-700">
              {canFreeText ? (
                <div className="flex gap-2">
                  <input className={inputCls + " flex-1"} placeholder="Type a reply…" value={text}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") doSendText(); }} />
                  <button disabled={sending} onClick={doSendText}
                    className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-semibold px-5 py-2 rounded-lg disabled:opacity-50">Send</button>
                </div>
              ) : (
                <div className="space-y-2">
                  <p className="text-xs text-[#E5C07B]">The 24-hour reply window has closed — send an approved template to reopen the chat.</p>
                  <TemplatePicker />
                  <button disabled={sending} onClick={doSendTemplate}
                    className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-semibold px-5 py-2 rounded-lg disabled:opacity-50">
                    {sending ? "Sending…" : "Send template"}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
