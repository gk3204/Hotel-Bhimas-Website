// src/api/enquiry.js
// Aftr deploytment --> http://localhost:8000/enquiry/send
export async function sendEnquiry(data) {
  try {
    const response = await fetch("http://127.0.0.1:8000/enquiry/send", {  // your FastAPI URL
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
