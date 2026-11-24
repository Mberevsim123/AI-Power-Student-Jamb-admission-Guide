import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { api, ApiError, hasSession, setTokens } from "./api";
import type { Tokens, User } from "./types";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  loadUser: () => Promise<User>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  updateUser: (user: User) => void;
  clearSession: () => void;
}
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(hasSession);
  const accountVersion = useRef(0);
  const loadUser = useCallback(async () => {
    const version = accountVersion.current;
    const current = await api.get<User>("/auth/me");
    if (version !== accountVersion.current || !hasSession())
      throw new ApiError(
        "Your account session changed. Please sign in again.",
        401,
      );
    setUser(current);
    return current;
  }, []);
  const clearSession = useCallback(() => {
    accountVersion.current += 1;
    setTokens(null);
    setUser(null);
  }, []);
  useEffect(() => {
    let active = true;
    const version = accountVersion.current;
    const expired = clearSession;
    window.addEventListener("jamb:session-expired", expired);
    if (hasSession())
      api
        .get<User>("/auth/me")
        .then((value) => {
          if (active && accountVersion.current === version && hasSession())
            setUser(value);
        })
        .catch((error) => {
          if (error instanceof ApiError && error.status === 401) clearSession();
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    return () => {
      active = false;
      window.removeEventListener("jamb:session-expired", expired);
    };
  }, [clearSession]);
  const login = async (email: string, password: string) => {
    const version = ++accountVersion.current;
    const next = await api.post<Tokens>("/auth/login", { email, password });
    if (accountVersion.current !== version)
      throw new ApiError("Sign-in was cancelled. Please try again.");
    setTokens(next);
    await loadUser();
  };
  const logout = async () => {
    const version = ++accountVersion.current;
    try {
      await api.post("/auth/logout");
    } finally {
      if (accountVersion.current === version) clearSession();
    }
  };
  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        loadUser,
        login,
        logout,
        updateUser: setUser,
        clearSession,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("AuthProvider is required");
  return context;
}
