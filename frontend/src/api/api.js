const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

export async function apiRequest(endpoint, options = {}) {
  const response = await fetch(`${BASE_URL}${endpoint}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  // If backend returns error
  if (!response.ok) {
    let errorMessage = "Something went wrong";
    try {
      const errorData = await response.json();
      
      // Handle Pydantic validation errors (422)
      if (Array.isArray(errorData.detail)) {
        const validationErrors = errorData.detail.map(err => {
          const field = err.loc?.[1] || 'field';
          return `${field}: ${err.msg || err.type}`;
        }).join('. ');
        errorMessage = validationErrors || "Validation error";
      } else if (typeof errorData.detail === 'string') {
        errorMessage = errorData.detail;
      } else if (errorData.message) {
        errorMessage = errorData.message;
      }
      console.log("API Error:", errorMessage);
    } catch {
      console.log("API Error (parse failed):", response.statusText);
    }
    throw new Error(errorMessage);
  }

  return response.json();
}