import { createContext, useContext, useCallback } from "react";
import type { ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getMe, login as loginApi, logout as logoutApi } from "@/api/auth";
import type { UserInfo, LoginRequest } from "@/types/auth";

interface AuthContextType {
  user: UserInfo | null;
  loading: boolean;
  login: (data: LoginRequest) => Promise<UserInfo>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();

  const { data: user, isLoading: loading } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: getMe,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  const login = useCallback(
    async (data: LoginRequest): Promise<UserInfo> => {
      await loginApi(data);
      const userInfo = await getMe();
      queryClient.setQueryData(["auth", "me"], userInfo);
      return userInfo;
    },
    [queryClient]
  );

  const logout = useCallback(async () => {
    await logoutApi();
    queryClient.setQueryData(["auth", "me"], null);
    queryClient.clear();
  }, [queryClient]);

  const refreshUser = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
  }, [queryClient]);

  return (
    <AuthContext.Provider
      value={{ user: user ?? null, loading, login, logout, refreshUser }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
