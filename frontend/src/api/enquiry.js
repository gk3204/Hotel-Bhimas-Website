// src/api/enquiry.js
const BASE_URL = "http://127.0.0.1:8000";

function getAuthHeader() {
  const token = localStorage.getItem("adminToken");
  return {
    "Content-Type": "application/json",
    ...(token && { Authorization: `Bearer ${token}` }),
  };
}

// Send new enquiry (public)
export async function sendEnquiry(data) {
  try {
    const response = await fetch(`${BASE_URL}/enquiry/send`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      throw new Error("Failed to send enquiry");
    }

    return await response.json();
  } catch (err) {
    console.error(err);
    throw err;
  }
}

// Get all enquiries (admin/reception only)
export async function getAllEnquiries() {
  try {
    const response = await fetch(`${BASE_URL}/enquiry/`, {
      headers: getAuthHeader(),
    });

    if (!response.ok) {
      throw new Error("Failed to fetch enquiries");
    }

    return await response.json();
  } catch (err) {
    console.error(err);
    throw err;
  }
}

// Get single enquiry
export async function getEnquiry(enquiryId) {
  try {
    const response = await fetch(`${BASE_URL}/enquiry/${enquiryId}`, {
      headers: getAuthHeader(),
    });

    if (!response.ok) {
      throw new Error("Failed to fetch enquiry");
    }

    return await response.json();
  } catch (err) {
    console.error(err);
    throw err;
  }
}

// Update enquiry status
export async function updateEnquiryStatus(enquiryId, status) {
  try {
    const response = await fetch(
      `${BASE_URL}/enquiry/${enquiryId}/status?status=${status}`,
      {
        method: "PATCH",
        headers: getAuthHeader(),
      }
    );

    if (!response.ok) {
      throw new Error("Failed to update enquiry");
    }

    return await response.json();
  } catch (err) {
    console.error(err);
    throw err;
  }
}
