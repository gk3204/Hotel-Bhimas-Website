import React, { useEffect, useState } from "react";
import {
  getUsers,
  createUser,
  updateUser,
  deleteUser,
} from "../../api/users";

const UserManagement = () => {
  const [users, setUsers] = useState([]);
  const [editingId, setEditingId] = useState(null);

  const [form, setForm] = useState({
    username: "",
    password: "",
    role: "reception",
  });

  useEffect(() => {
    loadUsers();
  }, []);

  const loadUsers = async () => {
    const data = await getUsers();
    setUsers(data);
  };

  const resetForm = () => {
    setForm({
      username: "",
      password: "",
      role: "reception",
    });
    setEditingId(null);
  };

  const handleCreate = async () => {
    if (!form.username || !form.password) {
      alert("Username & password required");
      return;
    }

    await createUser(form);
    resetForm();
    loadUsers();
  };

  const handleEdit = (user) => {
    setEditingId(user.user_id);
    setForm({
      username: user.username,
      password: "",
      role: user.role,
    });
  };

  const handleUpdate = async () => {
    await updateUser(editingId, {
      password: form.password,
      role: form.role,
    });

    resetForm();
    loadUsers();
  };

  const handleDelete = async (id) => {
    if (window.confirm("Delete this user?")) {
      await deleteUser(id);
      loadUsers();
    }
  };

  return (
    <div className="min-h-screen bg-[#0F172A] text-[#E5C07B] p-6">
      <div className="max-w-6xl mx-auto">

        <h1 className="text-3xl font-bold mb-8">
          User Management
        </h1>

        {/* Form Section */}
        <div className="bg-[#111827] rounded-2xl p-6 shadow-lg mb-10">
          <h2 className="text-xl font-semibold mb-6">
            {editingId ? "Edit User" : "Create New User"}
          </h2>

          <div className="grid md:grid-cols-3 gap-6">

            {/* Username */}
            <div className="flex flex-col">
              <label className="mb-2 text-sm">Username</label>
              <input
                disabled={editingId !== null}
                className="p-3 bg-[#1F2937] rounded-lg focus:outline-none focus:ring-2 focus:ring-[#E5C07B]"
                value={form.username}
                onChange={(e) =>
                  setForm({ ...form, username: e.target.value })
                }
              />
            </div>

            {/* Password */}
            <div className="flex flex-col">
              <label className="mb-2 text-sm">
                {editingId ? "New Password (optional)" : "Password"}
              </label>
              <input
                type="password"
                className="p-3 bg-[#1F2937] rounded-lg focus:outline-none focus:ring-2 focus:ring-[#E5C07B]"
                value={form.password}
                onChange={(e) =>
                  setForm({ ...form, password: e.target.value })
                }
              />
            </div>

            {/* Role */}
            <div className="flex flex-col">
              <label className="mb-2 text-sm">Role</label>
              <select
                className="p-3 bg-[#1F2937] rounded-lg focus:outline-none focus:ring-2 focus:ring-[#E5C07B]"
                value={form.role}
                onChange={(e) =>
                  setForm({ ...form, role: e.target.value })
                }
              >
                <option value="reception">Reception</option>
                <option value="admin">Admin</option>
              </select>
            </div>
          </div>

          <div className="mt-6 flex gap-4">
            {editingId ? (
              <>
                <button
                  onClick={handleUpdate}
                  className="bg-blue-600 px-6 py-2 rounded-lg text-white hover:bg-blue-700"
                >
                  Update
                </button>
                <button
                  onClick={resetForm}
                  className="bg-gray-600 px-6 py-2 rounded-lg text-white hover:bg-gray-700"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                onClick={handleCreate}
                className="bg-[#E5C07B] text-black px-6 py-2 rounded-lg hover:opacity-90"
              >
                Create User
              </button>
            )}
          </div>
        </div>

        {/* Users Table */}
        <div className="bg-[#111827] rounded-2xl p-6 shadow-lg overflow-x-auto">
          <h2 className="text-xl font-semibold mb-6">
            Existing Users
          </h2>

          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-[#E5C07B]/30">
                <th className="py-3">Username</th>
                <th>Role</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>

            <tbody>
              {users.map((user) => (
                <tr
                  key={user.user_id}
                  className="border-b border-[#E5C07B]/10 hover:bg-[#1F2937]"
                >
                  <td className="py-3">{user.username}</td>
                  <td>{user.role}</td>
                  <td className="text-right space-x-3">
                    <button
                      onClick={() => handleEdit(user)}
                      className="bg-yellow-600 px-4 py-1 rounded text-white hover:bg-yellow-700"
                    >
                      Edit
                    </button>

                    <button
                      onClick={() => handleDelete(user.user_id)}
                      className="bg-red-600 px-4 py-1 rounded text-white hover:bg-red-700"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}

              {users.length === 0 && (
                <tr>
                  <td colSpan="3" className="text-center py-6 opacity-50">
                    No users found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

      </div>
    </div>
  );
};

export default UserManagement;
