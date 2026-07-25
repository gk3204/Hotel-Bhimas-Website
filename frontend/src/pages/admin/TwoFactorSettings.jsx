import React, { useEffect, useState } from "react";
import { FaShieldAlt, FaCheckCircle } from "react-icons/fa";
import { twofaStatus, twofaEnroll, twofaConfirm, twofaDisable } from "../../api/admin";

const inputCls =
  "px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition";

export default function TwoFactorSettings() {
  const [status, setStatus] = useState(null);
  const [enroll, setEnroll] = useState(null); // {secret, otpauth_uri, qr_data_uri}
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");

  const showToast = (m) => { setToast(m); setTimeout(() => setToast(""), 3500); };
  const loadStatus = () => twofaStatus().then(setStatus).catch((e) => setError(e.message));

  useEffect(() => { loadStatus(); }, []);

  const startEnroll = async () => {
    setError(""); setBusy(true);
    try { setEnroll(await twofaEnroll()); }
    catch (e) { setError(e.message || "Enrolment failed"); }
    finally { setBusy(false); }
  };

  const confirm = async () => {
    setError(""); setBusy(true);
    try {
      await twofaConfirm(code.trim());
      setEnroll(null); setCode("");
      showToast("2FA enabled");
      await loadStatus();
    } catch (e) { setError(e.message || "Invalid code"); }
    finally { setBusy(false); }
  };

  const disable = async () => {
    setError(""); setBusy(true);
    try {
      await twofaDisable(code.trim());
      setCode("");
      showToast("2FA disabled");
      await loadStatus();
    } catch (e) { setError(e.message || "Invalid code"); }
    finally { setBusy(false); }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-2xl mx-auto">
        {toast && (
          <div className="fixed top-6 right-6 z-50 bg-slate-800 border border-[#E5C07B]/40 text-white px-5 py-3 rounded-xl shadow-2xl">{toast}</div>
        )}

        <div className="mb-6">
          <h1 className="text-4xl font-bold mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent flex items-center gap-3">
            <FaShieldAlt /> Two-Factor Authentication
          </h1>
          <p className="text-slate-400">Protect your admin login with a time-based one-time code (Google Authenticator / Authy).</p>
        </div>

        {error && <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500 text-red-400 text-sm">{error}</div>}

        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl backdrop-blur p-6">
          {!status ? (
            <div className="p-8 flex justify-center"><div className="animate-spin"><div className="h-10 w-10 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full" /></div></div>
          ) : !status.available ? (
            <p className="text-amber-300">2FA is unavailable — the server is missing the <code>pyotp</code> dependency.</p>
          ) : status.enabled ? (
            <>
              <div className="flex items-center gap-2 text-green-400 font-semibold mb-4">
                <FaCheckCircle /> 2FA is enabled on your account.
              </div>
              <p className="text-slate-300 text-sm mb-4">Enter a current code to turn it off.</p>
              <input value={code} maxLength={6} inputMode="numeric" placeholder="123456"
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                className={`${inputCls} tracking-[0.4em] text-center w-48`} />
              <div className="mt-4">
                <button onClick={disable} disabled={busy || code.length < 6}
                  className="bg-red-600 hover:bg-red-500 text-white font-semibold px-6 py-2.5 rounded-lg transition disabled:opacity-50">
                  {busy ? "Working…" : "Disable 2FA"}
                </button>
              </div>
            </>
          ) : !enroll ? (
            <>
              {status.required && (
                <div className="mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/40 text-amber-300 text-sm">
                  Your hotel requires admins to enable 2FA. Please set it up now.
                </div>
              )}
              <p className="text-slate-300 mb-4">2FA is not yet enabled. Click below to generate a QR code and scan it with your authenticator app.</p>
              <button onClick={startEnroll} disabled={busy}
                className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition-all hover:scale-105 disabled:opacity-50">
                {busy ? "Generating…" : "Set up 2FA"}
              </button>
            </>
          ) : (
            <>
              <p className="text-slate-300 mb-4">1. Scan this QR with Google Authenticator / Authy:</p>
              {enroll.qr_data_uri
                ? <img src={enroll.qr_data_uri} alt="2FA QR" className="w-48 h-48 bg-white p-2 rounded-lg mb-4" />
                : <p className="text-amber-300 text-sm mb-2">QR unavailable — add this key manually.</p>}
              <p className="text-slate-400 text-xs mb-4">Or enter this key manually: <span className="font-mono text-slate-200 break-all">{enroll.secret}</span></p>
              <p className="text-slate-300 mb-2">2. Enter the 6-digit code it shows:</p>
              <input value={code} maxLength={6} inputMode="numeric" autoFocus placeholder="123456"
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                className={`${inputCls} tracking-[0.4em] text-center w-48`} />
              <div className="mt-4 flex gap-3">
                <button onClick={confirm} disabled={busy || code.length < 6}
                  className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-6 py-2.5 rounded-lg transition-all hover:scale-105 disabled:opacity-50">
                  {busy ? "Verifying…" : "Confirm & enable"}
                </button>
                <button onClick={() => { setEnroll(null); setCode(""); }}
                  className="bg-slate-700 hover:bg-slate-600 text-white font-semibold px-4 py-2.5 rounded-lg transition">
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
