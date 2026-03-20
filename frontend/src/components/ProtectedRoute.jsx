import { Navigate } from "react-router-dom";
import { jwtDecode } from "jwt-decode";

const ProtectedRoute = ({ children, allowedRoles }) => {
  const token = localStorage.getItem("adminToken"); // ✅ FIXED

  if (!token) return <Navigate to="/admin-login" />;

  try {
    const decoded = jwtDecode(token);

    if (allowedRoles && !allowedRoles.includes(decoded.role)) {
      return <Navigate to="/admin-login" />;
    }

    return children;
  } catch (err) {
    return <Navigate to="/admin-login" />;
  }
};

export default ProtectedRoute;
