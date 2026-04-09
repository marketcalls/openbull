import axios from "axios";

const api = axios.create({
  baseURL: "",
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const currentPath = window.location.pathname;
      if (currentPath !== "/login" && currentPath !== "/setup") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export default api;
