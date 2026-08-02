import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { adminLogin, verifyLogin2fa } from "../../api/admin";
import logo from "../../assets/logo-gold.svg";
import { jwtDecode } from "jwt-decode";

const AdminLogin = () => {
  const navigate = useNavigate();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  // 2FA challenge state (prompt 18)
  const [challenge, setChallenge] = useState("");
  const [code, setCode] = useState("");

  const routeByRole = (token) => {
    localStorage.setItem("adminToken", token);
    const decoded = jwtDecode(token);
    if (decoded.role === "admin") navigate("/admin");
    else if (decoded.role === "supervisor") navigate("/admin/housekeeping");
    else if (decoded.role === "reception") navigate("/reception");
    // roomservice is a tablet role and lives in the same installable staff shell (TBC-4).
    else if (["housekeeper", "maintenance", "roomservice"].includes(decoded.role)) navigate("/staff");
    else setError("Unauthorized role");
  };

  const handleLogin = async () => {
    setError("");
    setLoading(true);
    try {
      const data = await adminLogin(username, password);
      if (data.twofa_required) {
        setChallenge(data.challenge);   // move to the code-entry step
        return;
      }
      if (!data.access_token) throw new Error("No token received");
      routeByRole(data.access_token);
    } catch (err) {
      setError(err?.message || "Invalid username or password");
    } finally {
      setLoading(false);
    }
  };

  const handleVerify = async () => {
    setError("");
    setLoading(true);
    try {
      const data = await verifyLogin2fa(challenge, code.trim());
      if (!data.access_token) throw new Error("No token received");
      routeByRole(data.access_token);
    } catch (err) {
      setError(err?.message || "Invalid authentication code");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0F172A] px-4">
      <div className="w-full max-w-md bg-[#111827] rounded-2xl shadow-2xl p-8 border border-[#E5C07B]/20">
        <img src={logo} alt="Hotel Bhimas" className="h-16 mx-auto mb-4" />
        <h2 className="text-2xl font-bold text-center text-[#E5C07B] mb-6 tracking-wide">
          Hotel Bhimas Admin
        </h2>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500 text-red-400 text-sm text-center">
            {error}
          </div>
        )}

        {!challenge ? (
          <>
            <input
              type="text"
              placeholder="Username"
              className="w-full mb-4 p-3 rounded-lg bg-[#1F2937] text-white border border-[#E5C07B]/30 focus:outline-none focus:border-[#FCD34D]"
              onChange={(e) => setUsername(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleLogin()}
            />
            <input
              type="password"
              placeholder="Password"
              className="w-full mb-6 p-3 rounded-lg bg-[#1F2937] text-white border border-[#E5C07B]/30 focus:outline-none focus:border-[#FCD34D]"
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleLogin()}
            />
            <button
              onClick={handleLogin}
              disabled={loading}
              className="w-full bg-[#E5C07B] text-[#0F172A] font-semibold py-3 rounded-lg hover:bg-[#FCD34D] transition duration-300 disabled:opacity-50"
            >
              {loading ? "Logging in..." : "Login"}
            </button>
          </>
        ) : (
          <>
            <p className="text-slate-300 text-sm text-center mb-4">
              Enter the 6-digit code from your authenticator app.
            </p>
            <input
              type="text"
              inputMode="numeric"
              autoFocus
              maxLength={6}
              placeholder="123456"
              className="w-full mb-6 p-3 tracking-[0.4em] text-center text-lg rounded-lg bg-[#1F2937] text-white border border-[#E5C07B]/30 focus:outline-none focus:border-[#FCD34D]"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              onKeyDown={(e) => e.key === "Enter" && handleVerify()}
            />
            <button
              onClick={handleVerify}
              disabled={loading || code.length < 6}
              className="w-full bg-[#E5C07B] text-[#0F172A] font-semibold py-3 rounded-lg hover:bg-[#FCD34D] transition duration-300 disabled:opacity-50"
            >
              {loading ? "Verifying..." : "Verify"}
            </button>
            <button
              onClick={() => { setChallenge(""); setCode(""); setError(""); }}
              className="w-full mt-3 text-slate-400 text-sm hover:text-[#FCD34D]"
            >
              ← Back to login
            </button>
          </>
        )}
      </div>
    </div>
  );
};

export default AdminLogin;
