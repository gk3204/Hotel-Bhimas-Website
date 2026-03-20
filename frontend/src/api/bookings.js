const BASE_URL = "http://127.0.0.1:8000";

function getAuthHeader() {
  const token = localStorage.getItem("adminToken");

  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}
 // change if deployed

// Get all bookings
export const getBookings = async (page = 1, fromDate = "") => {
  const limit = 15;
  const skip = (page - 1) * limit;

  let url = `${BASE_URL}/bookings/?skip=${skip}&limit=${limit}`;

  if (fromDate) {
    url += `&from_date=${fromDate}`;
  }

  const res = await fetch(url, {
    headers: getAuthHeader(),
  });

  if (!res.ok) throw new Error("Failed to fetch bookings");

  return await res.json();
};


// Get booking by ID
export const getBookingById = async (id) => {
  const res = await fetch(`${BASE_URL}/bookings/${id}`, {
    headers: getAuthHeader(),
  });

  if (!res.ok) throw new Error("Failed to fetch booking");

  return await res.json();
};

// Cancel booking
export const cancelBooking = async (id) => {
  const res = await fetch(`${BASE_URL}/bookings/${id}/cancel`, {
    method: "PATCH",
    headers: getAuthHeader(),
  });

  if (!res.ok) throw new Error("Failed to cancel booking");

  return await res.json();
};


// Filter bookings by date
export const getBookingsByDate = async (from, to) => {
  const res = await fetch(
    `${BASE_URL}/bookings/filter/by-date?from_date=${from}&to_date=${to}`,
    {
      headers: getAuthHeader(),
    }
  );

  if (!res.ok) throw new Error("Failed to filter bookings");

  return await res.json();
};

