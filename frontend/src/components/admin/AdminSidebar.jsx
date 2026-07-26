import React, { useMemo, useState } from "react";
import { NavLink, useNavigate, useLocation } from "react-router-dom";
import { FaSearch, FaSignOutAlt, FaBars, FaTimes, FaChevronDown } from "react-icons/fa";
import logo from "../../assets/logo-gold.svg";
import { NAV_GROUPS, NAV_ITEMS } from "./adminNav";
import NotificationsBell from "./NotificationsBell";

// Grouped, searchable admin sidebar. Fixed left column on md+, slide-in drawer on mobile.
// Replaces the old 29-link horizontal AdminHeader.
export default function AdminSidebar() {
  const [open, setOpen] = useState(false); // mobile drawer
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState({}); // group label -> hidden
  const navigate = useNavigate();
  const location = useLocation();

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    return NAV_ITEMS.filter(
      (it) => it.label.toLowerCase().includes(q) || it.group.toLowerCase().includes(q)
    );
  }, [query]);

  const logout = () => {
    localStorage.removeItem("adminToken");
    navigate("/admin-login");
  };

  const closeDrawer = () => setOpen(false);
  const toggleGroup = (label) => setCollapsed((c) => ({ ...c, [label]: !c[label] }));

  const linkCls = ({ isActive }) =>
    `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition group ${
      isActive
        ? "bg-gradient-to-r from-[#E5C07B]/20 to-transparent text-[#FCD34D] border-l-2 border-[#E5C07B]"
        : "text-slate-300 hover:bg-slate-800/70 hover:text-white border-l-2 border-transparent"
    }`;

  const NavItemLink = ({ item }) => {
    const Icon = item.icon;
    return (
      <NavLink to={item.to} end={item.end} className={linkCls} onClick={closeDrawer}>
        <Icon className="shrink-0 opacity-80 group-hover:opacity-100" size={15} />
        <span className="truncate">{item.label}</span>
      </NavLink>
    );
  };

  const sidebarBody = (
    <div className="flex flex-col h-full">
      {/* Brand */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-slate-800">
        <img src={logo} alt="Logo" className="h-9 w-auto" />
        <div>
          <div className="text-[#E5C07B] font-bold leading-tight">Hotel Bhimas</div>
          <div className="text-slate-500 text-xs">Admin Console</div>
        </div>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => window.dispatchEvent(new Event("admin:open-search"))}
            className="p-2 rounded-lg text-slate-300 hover:text-white hover:bg-slate-800/70 transition"
            aria-label="Search guests (Ctrl+K)"
            title="Search guests (Ctrl+K)"
          >
            <FaSearch size={15} />
          </button>
          <NotificationsBell />
          <button
            onClick={closeDrawer}
            className="md:hidden text-slate-400 hover:text-white p-2"
            aria-label="Close menu"
          >
            <FaTimes size={18} />
          </button>
        </div>
      </div>

      {/* Quick-jump search */}
      <div className="px-4 py-3 border-b border-slate-800">
        <div className="relative">
          <FaSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={12} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Jump to…"
            className="w-full pl-8 pr-3 py-2 text-sm bg-slate-900/70 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-[#E5C07B] focus:ring-2 focus:ring-[#E5C07B]/20"
          />
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-4">
        {filtered ? (
          <div className="space-y-1">
            {filtered.length === 0 ? (
              <p className="text-slate-500 text-sm px-3 py-4 text-center">No matching page.</p>
            ) : (
              filtered.map((item) => <NavItemLink key={item.to} item={item} />)
            )}
          </div>
        ) : (
          NAV_GROUPS.map((group) => (
            <div key={group.label}>
              <button
                onClick={() => toggleGroup(group.label)}
                className="w-full flex items-center justify-between px-3 mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500 hover:text-slate-300"
              >
                <span>{group.label}</span>
                <FaChevronDown
                  size={10}
                  className={`transition-transform ${collapsed[group.label] ? "-rotate-90" : ""}`}
                />
              </button>
              {!collapsed[group.label] && (
                <div className="space-y-0.5">
                  {group.items.map((item) => (
                    <NavItemLink key={item.to} item={item} />
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </nav>

      {/* Footer */}
      <div className="px-3 py-3 border-t border-slate-800">
        <button
          onClick={logout}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-red-300 hover:bg-red-500/10 transition"
        >
          <FaSignOutAlt size={15} /> Logout
        </button>
      </div>
    </div>
  );

  return (
    <>
      {/* Mobile top bar */}
      <div className="md:hidden sticky top-0 z-40 flex items-center gap-3 bg-[#0F172A] border-b border-slate-800 px-4 py-3">
        <button onClick={() => setOpen(true)} className="text-[#E5C07B]" aria-label="Open menu">
          <FaBars size={22} />
        </button>
        <img src={logo} alt="Logo" className="h-7 w-auto" />
        <span className="text-[#E5C07B] font-bold">Admin</span>
      </div>

      {/* Desktop fixed sidebar */}
      <aside className="hidden md:flex md:flex-col md:fixed md:inset-y-0 md:left-0 md:w-64 bg-[#0B1220] border-r border-slate-800 z-30">
        {sidebarBody}
      </aside>

      {/* Mobile drawer */}
      <div
        onClick={closeDrawer}
        className={`md:hidden fixed inset-0 bg-black/60 z-40 transition-opacity ${
          open ? "opacity-100 visible" : "opacity-0 invisible"
        }`}
      />
      <aside
        className={`md:hidden fixed inset-y-0 left-0 w-72 max-w-[85%] bg-[#0B1220] border-r border-slate-800 z-50 transform transition-transform ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
        key={location.pathname /* ensure drawer state resets on navigation */}
      >
        {sidebarBody}
      </aside>
    </>
  );
}
