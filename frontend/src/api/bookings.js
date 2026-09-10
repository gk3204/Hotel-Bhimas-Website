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


// Per-occupant KYC roster for a booking (FE-3). Masked IDs only; `has_scan` / `has_scan_back`
// say whether an encrypted ID image is on file for each side of the card.
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
// `side` is "front" (default) or "back" — the desk flatbed has no duplex, so an ID is
// captured as two separate passes and each is audited on its own when viewed.
export const getGuestScanObjectUrl = async (bookingId, guestId, side = "front") => {
  const res = await fetch(
    `${BASE_URL}/reception/bookings/${bookingId}/guests/${guestId}/scan?side=${side}`,
    { headers: { Authorization: getAuthHeader().Authorization } },
  );
  if (!res.ok) {
    // 410 Gone is deliberate and means something different from 404: the scan DID exist and was
    // moved into the offline weekly archive. The server sends the archive date and the ref, which
    // is what locates the file inside the archive zip — so show that, not "no scan on file".
    let msg;
    if (res.status === 410) {
      msg = await res.json().then((b) => b?.detail).catch(() => null);
      msg = msg || `This ${side} ID scan was archived offline and is no longer on the server.`;
    } else if (res.status === 503) {
      msg = "ID scans can't be retrieved right now — the key or storage isn't available.";
    } else {
      msg = `No ${side} ID scan on file for this guest.`;
    }
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

