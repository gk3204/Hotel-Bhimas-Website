import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { adminLogin } from "../../api/admin";
import logo from "../../assets/logo-gold.svg";
import { jwtDecode } from "jwt-decode";



const AdminLogin = () => {
  const navigate = useNavigate();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");   // ✅ error state
  const [loading, setLoading] = useState(false); // optional loading state

  const handleLogin = async () => {
  setError("");
  setLoading(true);

  try {
    const data = await adminLogin(username, password);

    const token = data.access_token;

    if (!token) {
      throw new Error("No token received");
    }

    localStorage.setItem("adminToken", token);

    const decoded = jwtDecode(token);
    console.log("DECODED TOKEN:", decoded);

    if (decoded.role === "admin") {
      navigate("/admin");
    } else if (decoded.role === "reception") {
      navigate("/reception");
    } else {
      setError("Unauthorized role");
    }

  } catch (err) {
    console.error(err);
    setError("Invalid username or password");
  } finally {
    setLoading(false);
  }
};


  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0F172A] px-4">
      <div className="w-full max-w-md bg-[#111827] rounded-2xl shadow-2xl p-8 border border-[#E5C07B]/20">

        <img
          src={logo}
          alt="Hotel Bhimas"
          className="h-16 mx-auto mb-4"
        />

        <h2 className="text-2xl font-bold text-center text-[#E5C07B] mb-6 tracking-wide">
          Hotel Bhimas Admin
        </h2>

        {/* ✅ Error Message Box */}
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500 text-red-400 text-sm text-center">
            {error}
          </div>
        )}

        <input
          type="text"
          placeholder="Username"
          className="w-full mb-4 p-3 rounded-lg bg-[#1F2937] text-white border border-[#E5C07B]/30 focus:outline-none focus:border-[#FCD34D]"
          onChange={(e) => setUsername(e.target.value)}
        />

        <input
          type="password"
          placeholder="Password"
          className="w-full mb-6 p-3 rounded-lg bg-[#1F2937] text-white border border-[#E5C07B]/30 focus:outline-none focus:border-[#FCD34D]"
          onChange={(e) => setPassword(e.target.value)}
        />

        <button
          onClick={handleLogin}
          disabled={loading}
          className="w-full bg-[#E5C07B] text-[#0F172A] font-semibold py-3 rounded-lg hover:bg-[#FCD34D] transition duration-300 disabled:opacity-50"
        >
          {loading ? "Logging in..." : "Login"}
        </button>

      </div>
    </div>
  );
};

export default AdminLogin;
