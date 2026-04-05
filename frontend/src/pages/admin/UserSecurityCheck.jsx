import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { FaLock, FaClock } from "react-icons/fa";

const UserSecurityCheck = () => {
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!secret) {
      setError("Please enter the secret password");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const response = await fetch(
        import.meta.env.VITE_API_URL || "http://127.0.0.1:8000" + "/admin-security/validate-secret",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ secret }),
        }
      );

      if (!response.ok) {
        throw new Error("Invalid secret");
      }

      navigate("/admin/users");
    } catch (err) {
      setError("❌ Invalid Secret Password. Please check the time-based code.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 px-4">
      <div className="w-full max-w-md">
        <div className="bg-gradient-to-br from-slate-800/80 to-slate-700/80 border border-slate-700 backdrop-blur rounded-3xl p-8 shadow-2xl">
          
          {/* Header Icon */}
          <div className="flex justify-center mb-6">
            <div className="bg-gradient-to-br from-[#E5C07B] to-[#D4AF37] p-4 rounded-2xl">
              <FaLock size={32} className="text-slate-900" />
            </div>
          </div>

          {/* Title */}
          <h1 className="text-3xl font-bold text-center mb-2 bg-gradient-to-r from-[#E5C07B] to-[#FCD34D] bg-clip-text text-transparent">
            Secure Access
          </h1>
          <p className="text-center text-slate-400 text-sm mb-8">
            Enter the time-based secret password to access user management
          </p>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Input Field */}
            <div>
              <label className="block text-sm font-semibold text-slate-300 mb-2">
                Secret Password
              </label>
              <input
                type="password"
                placeholder="Enter 4-digit code"
                autoFocus
                value={secret}
                onChange={(e) => {
                  setSecret(e.target.value);
                  setError("");
                }}
                className="w-full px-4 py-3 bg-slate-900/50 border border-slate-600 rounded-lg text-white text-center text-2xl tracking-widest font-mono focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition placeholder-slate-500"
              />
            </div>

            {/* Error Message */}
            {error && (
              <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 animate-pulse">
                <p className="text-red-300 text-sm font-medium">{error}</p>
              </div>
            )}

            {/* Submit Button */}
            <button
              type="submit"
              disabled={loading || !secret}
              className="w-full bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-xl text-slate-900 font-bold py-3 px-4 rounded-lg transition-all duration-300 hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 mt-6"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <div className="animate-spin">⏳</div>
                  Verifying...
                </span>
              ) : (
                "🔓 Verify Password"
              )}
            </button>
          </form>

          {/* Footer */}
          <p className="text-center text-slate-500 text-xs mt-6">
            For security purposes, this code changes every minute
          </p>
        </div>
      </div>
    </div>
  );
};

export default UserSecurityCheck;
