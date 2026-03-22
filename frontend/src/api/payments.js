const BASE_URL = "http://127.0.0.1:8000";

function getAuthHeader() {
  const token = localStorage.getItem("adminToken");
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

// Get all payments
export const getAllPayments = async () => {
  const res = await fetch(`${BASE_URL}/payments/`, {
    headers: getAuthHeader(),
  });

  if (!res.ok) throw new Error("Failed to fetch payments");

  return await res.json();
};

// Get single payment
export const getPayment = async (paymentId) => {
  const res = await fetch(`${BASE_URL}/payments/${paymentId}`, {
    headers: getAuthHeader(),
  });

  if (!res.ok) throw new Error("Failed to fetch payment");

  return await res.json();
};
