const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

function getAuthHeader() {
  const token = localStorage.getItem("adminToken");

  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}
 // change if deployed

// Get all bookings (paginated + optional check-in date range and status filter — ALT-10)
export const getBookings = async (page = 1, fromDate = "", toDate = "", status = "") => {
  const limit = 15;
  const skip = (page - 1) * limit;

  let url = `${BASE_URL}/bookings/?skip=${skip}&limit=${limit}`;

  if (fromDate) url += `&from_date=${fromDate}`;
  if (toDate) url += `&to_date=${toDate}`;
  if (status) url += `&status=${encodeURIComponent(status)}`;

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


// Per-occupant KYC roster for a booking (FE-3). Masked IDs only; `has_scan` says whether
// an encrypted ID image is on file.
export const getBookingGuests = async (bookingId) => {
  const res = await fetch(`${BASE_URL}/reception/bookings/${bookingId}/guests`, {
    headers: getAuthHeader(),
  });
  if (!res.ok) throw new Error("Failed to fetch the occupant list");
  return await res.json();
};

// Fetch a decrypted ID scan as a blob URL. It is NEVER a public URL — the endpoint is
// authed and every view is written to the audit log — so it has to be fetched with the
// bearer token rather than dropped into an <img src>.
export const getGuestScanObjectUrl = async (bookingId, guestId) => {
  const res = await fetch(
    `${BASE_URL}/reception/bookings/${bookingId}/guests/${guestId}/scan`,
    { headers: { Authorization: getAuthHeader().Authorization } },
  );
  if (!res.ok) {
    const msg = res.status === 503
      ? "ID scans can't be decrypted — the encryption key isn't configured on the server."
      : "No ID scan on file for this guest.";
    throw new Error(msg);
  }
  const blob = await res.blob();
  return { url: URL.createObjectURL(blob), type: blob.type };
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

