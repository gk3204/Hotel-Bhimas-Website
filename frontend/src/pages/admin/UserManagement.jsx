import React, { useEffect, useState } from "react";
import { PageShell } from "../../components/admin/BackofficeUI";
import {
  getUsers,
  createUser,
  updateUser,
  deleteUser,
} from "../../api/users";
import { FaPlus, FaEdit, FaTrash } from "react-icons/fa";
import { useConfirm } from "../../components/ConfirmDialog";

// Role chip styles + labels (matches the backend role vocab: admin|reception|housekeeper|maintenance).
const ROLE_META = {
  admin: { label: "⚙️ Admin", chip: "bg-red-500/20 text-red-300 border-red-500/30" },
  reception: { label: "🏨 Reception", chip: "bg-blue-500/20 text-blue-300 border-blue-500/30" },
  housekeeper: { label: "🧹 Housekeeper", chip: "bg-amber-500/20 text-amber-300 border-amber-500/30" },
  maintenance: { label: "🔧 Maintenance", chip: "bg-cyan-500/20 text-cyan-300 border-cyan-500/30" },
};

const UserManagement = () => {
  const [users, setUsers] = useState([]);
  const [editingId, setEditingId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);

  const [form, setForm] = useState({
    username: "",
    password: "",
    role: "reception",
  });

  useEffect(() => {
    loadUsers();
  }, []);

  const loadUsers = async () => {
    setLoading(true);
    try {
      const data = await getUsers();
      setUsers(data.sort((a, b) => a.user_id - b.user_id));
    } catch (err) {
      showToast("Failed to load users", "error");
    }
    setLoading(false);
  };

  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
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
      showToast("Username & password required", "error");
      return;
    }

    try {
      await createUser(form);
      resetForm();
      loadUsers();
      showToast("User created successfully");
    } catch (err) {
      showToast(err.message || "Failed to create user", "error");
    }
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
    try {
      await updateUser(editingId, {
        password: form.password,
        role: form.role,
      });
      resetForm();
      loadUsers();
      showToast("User updated successfully");
    } catch (err) {
      showToast(err.message || "Failed to update user", "error");
    }
  };

  const { confirm } = useConfirm();

  const handleDelete = async (id) => {
    if (await confirm({
      title: "Delete this user?",
      message: "The user will lose access immediately.",
      confirmText: "Delete", tone: "danger",
    })) {
      try {
        await deleteUser(id);
        loadUsers();
        showToast("User deleted successfully");
      } catch (err) {
        showToast("Failed to delete user", "error");
      }
    }
  };

  return (
    <PageShell
      icon="👤"
      title="User Management"
      subtitle="Create and manage staff accounts — admin, reception, housekeeper, maintenance"
    >

        {/* CREATE FORM */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 p-8 rounded-2xl shadow-xl mb-10 backdrop-blur">
          <h2 className="text-2xl font-bold mb-6 text-[#E5C07B] flex items-center gap-2">
            <FaPlus size={20} /> {editingId ? "✏️ Edit User" : "Add New User"}
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
            {/* Username */}
            <InputField
              label="Username"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              disabled={editingId !== null}
              placeholder="e.g., john_admin"
            />

            {/* Password */}
            <InputField
              label={editingId ? "New Password (optional)" : "Password"}
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              placeholder="Min 8 characters"
            />

            {/* Role */}
            <div className="flex flex-col">
              <label className="mb-2 text-sm font-semibold text-slate-300">Role</label>
              <select
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
                className="px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition"
              >
                <option value="reception">🏨 Reception</option>
                <option value="admin">⚙️ Admin</option>
                <option value="housekeeper">🧹 Housekeeper</option>
                <option value="maintenance">🔧 Maintenance</option>
              </select>
            </div>
          </div>

          <div className="flex gap-4">
            {editingId ? (
              <>
                <button
                  onClick={handleUpdate}
                  disabled={loading}
                  className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-lg text-slate-900 font-bold px-8 py-3 rounded-lg transition-all duration-300 hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center gap-2"
                >
                  {loading ? "⏳ Updating..." : "Save Changes"}
                </button>
                <button
                  onClick={resetForm}
                  disabled={loading}
                  className="bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed px-8 py-3 rounded-lg font-medium transition"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                onClick={handleCreate}
                disabled={loading}
                className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] hover:shadow-lg text-slate-900 font-bold px-8 py-3 rounded-lg transition-all duration-300 hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center gap-2"
              >
                <FaPlus size={16} /> {loading ? "Creating..." : "Create User"}
              </button>
            )}
          </div>
        </div>

        {/* USERS LIST */}
        <div className="bg-gradient-to-r from-slate-800/50 to-slate-700/50 border border-slate-700 rounded-2xl shadow-xl overflow-hidden backdrop-blur">
          <div className="p-6 border-b border-slate-700">
            <h2 className="text-2xl font-bold">📊 Existing Users</h2>
          </div>

          {loading ? (
            <div className="p-12 flex items-center justify-center">
              <div className="text-center">
                <div className="animate-spin mb-4 mx-auto inline-block">
                  <div className="h-12 w-12 border-4 border-[#E5C07B] border-t-[#D4AF37] rounded-full"></div>
                </div>
                <p className="text-slate-400 font-medium">Loading users...</p>
              </div>
            </div>
          ) : users.length === 0 ? (
            <div className="p-12 text-center text-slate-400">
              <div className="text-5xl mb-4">👥</div>
              <p>No users yet. Create one above!</p>
            </div>
          ) : (
            <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
              <table className="w-full text-left">
                <thead className="bg-slate-900/80 sticky top-0 border-b border-slate-700">
                  <tr>
                    <th className="px-6 py-4 font-semibold text-sm">ID</th>
                    <th className="px-6 py-4 font-semibold text-sm">Username</th>
                    <th className="px-6 py-4 font-semibold text-sm">Role</th>
                    <th className="px-6 py-4 font-semibold text-sm">Actions</th>
                  </tr>
                </thead>

                <tbody className="divide-y divide-slate-700">
                  {users.map((user) => (
                    <tr
                      key={user.user_id}
                      className="hover:bg-slate-700/30 transition"
                    >
                      <td className="px-6 py-4 font-semibold text-[#FCD34D]">#{user.user_id}</td>
                      <td className="px-6 py-4 font-medium">{user.username}</td>
                      <td className="px-6 py-4">
                        <span
                          className={`px-3 py-1 rounded-full text-xs font-bold border ${
                            ROLE_META[user.role]?.chip ||
                            "bg-slate-500/20 text-slate-300 border-slate-500/30"
                          }`}
                        >
                          {ROLE_META[user.role]?.label || user.role}
                        </span>
                      </td>
                      <td className="px-6 py-4 space-x-2">
                        <button
                          onClick={() => handleEdit(user)}
                          title="Edit User"
                          className="bg-blue-600 hover:bg-blue-700 px-3 py-2 rounded-lg text-white font-medium transition inline-flex items-center gap-1"
                        >
                          <FaEdit size={14} /> Edit
                        </button>

                        <button
                          onClick={() => handleDelete(user.user_id)}
                          title="Delete User"
                          className="bg-red-600 hover:bg-red-700 px-3 py-2 rounded-lg text-white font-medium transition inline-flex items-center gap-1"
                        >
                          <FaTrash size={14} /> Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* TOAST */}
        {toast && (
          <div className="fixed top-6 right-6 z-50 animate-slide-in">
            <div
              className={`px-6 py-4 rounded-xl shadow-2xl font-medium backdrop-blur ${
                toast.type === "error"
                  ? "bg-red-600/90 text-white"
                  : "bg-green-600/90 text-white"
              }`}
            >
              {toast.type === "error" ? "❌" : "✅"} {toast.message}
            </div>
          </div>
        )}
    </PageShell>
  );
};

/* Reusable Input Field */
const InputField = ({ label, type = "text", disabled = false, ...props }) => (
  <div className="flex flex-col">
    <label className="mb-2 text-sm font-semibold text-slate-300">{label}</label>
    <input
      type={type}
      disabled={disabled}
      {...props}
      className="px-4 py-2 bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20 transition disabled:opacity-50 disabled:cursor-not-allowed"
    />
  </div>
);

export default UserManagement;
