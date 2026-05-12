import axios from "axios";

const api = axios.create({
  baseURL: "",
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

// Routes that are publicly viewable — a 401 on these paths must NOT bounce
// the user to /login. Anywhere else, a 401 (e.g. session expired mid-session)
// snaps back to the login screen so the user is never stuck on a half-loaded
// authenticated page.
const PUBLIC_PATHS = new Set(["/", "/login", "/setup"]);

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const currentPath = window.location.pathname;
      if (!PUBLIC_PATHS.has(currentPath)) {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export default api;
