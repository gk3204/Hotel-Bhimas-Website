import { useState } from "react";
import { useNavigate } from "react-router-dom";

const UserSecurityCheck = () => {
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();

    try {
      const response = await fetch(
        "http://127.0.0.1:8000/admin-security/validate-secret",
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
      setError("Invalid Secret Password");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white p-8 shadow-lg rounded-lg w-96">
        <h2 className="text-xl font-bold mb-4 text-center">
          User Management Security
        </h2>

        <form onSubmit={handleSubmit}>
          <input
            type="password"
            placeholder="Enter Secret Password"
            className="w-full border p-2 rounded mb-4"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            required
          />

          {error && <p className="text-red-500 mb-2">{error}</p>}

          <button
            type="submit"
            className="w-full bg-black text-white py-2 rounded"
          >
            Verify
          </button>
        </form>
      </div>
    </div>
  );
};

export default UserSecurityCheck;
