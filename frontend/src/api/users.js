const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

function getAuthHeader() {
  const token = localStorage.getItem("adminToken");
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}


export async function getUsers() {
  const res = await fetch(`${BASE_URL}/users/`, {
    headers: getAuthHeader(),
  });

  if (!res.ok) throw new Error("Failed to fetch users");
  return res.json();
}


export async function createUser(data) {
  const res = await fetch(`${BASE_URL}/users/`, {
    method: "POST",
    headers: getAuthHeader(),
    body: JSON.stringify(data),
  });

  if (!res.ok) throw new Error("Create failed");
  return res.json();
}

export async function deleteUser(id) {
  const res = await fetch(`${BASE_URL}/users/${id}`, {
    method: "DELETE",
    headers: getAuthHeader(),
  });

  if (!res.ok) throw new Error("Delete failed");
  return res.json();
}


export async function updateUser(id, data) {
  const res = await fetch(`${BASE_URL}/users/${id}`, {
    method: "PUT",
    headers: getAuthHeader(),
    body: JSON.stringify(data),
  });

  if (!res.ok) throw new Error("Update failed");
  return res.json();
}

