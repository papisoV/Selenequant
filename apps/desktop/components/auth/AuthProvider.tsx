"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  clearAccessToken,
  getAccessToken,
  getTokenPayload,
  getUserId,
  getUserRole,
  isAuthEnabled,
  refreshToken,
  logout as doLogout,
} from "@/lib/auth";

interface AuthState {
  isReady: boolean;
  isAuthenticated: boolean;
  authEnabled: boolean;
  userId: string;
  role: string;
  displayName: string;
  avatarUrl: string;
  logout: () => void;
  syncAuth: () => void;
}

const AuthContext = createContext<AuthState>({
  isReady: false,
  isAuthenticated: false,
  authEnabled: false,
  userId: "local",
  role: "admin",
  displayName: "",
  avatarUrl: "",
  logout: () => {},
  syncAuth: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

export function isTokenExpired(): boolean {
  const payload = getTokenPayload();
  if (!payload) return true;
  const exp = payload.exp as number;
  if (!exp) return false;
  return exp * 1000 < Date.now();
}

/** 提前刷新窗口(秒): exp 距现在不足这个数也要刷。 */
const REFRESH_LEAD_SEC = 300;

/** token 是否该刷新(已过期, 或临近 300s 到期)。纯函数。 */
export function needsTokenRefresh(): boolean {
  if (isTokenExpired()) return true;
  const exp = (getTokenPayload()?.exp as number) ?? 0;
  const nowSec = Math.floor(Date.now() / 1000);
  return exp > 0 && exp - nowSec < REFRESH_LEAD_SEC;
}

/** 每 30s 检查一次, 临近过期就刷新; 刷新失败且已过期则登出。 */
const REFRESH_INTERVAL_MS = 30 * 1000;

function useTokenRefreshLoop(
  onRefreshed: () => void,
  onExpired: () => void,
): void {
  const refreshRef = useRef(onRefreshed);
  const expiredRef = useRef(onExpired);
  refreshRef.current = onRefreshed;
  expiredRef.current = onExpired;

  useEffect(() => {
    const interval = setInterval(() => {
      if (!needsTokenRefresh()) return;
      refreshToken().then((ok) => {
        if (ok) {
          refreshRef.current();
        } else if (isTokenExpired()) {
          expiredRef.current();
        }
      });
    }, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isReady, setIsReady] = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [userId, setUserId] = useState("local");
  const [role, setRole] = useState("admin");
  const [displayName, setDisplayName] = useState("");
  const [avatarUrl, setAvatarUrl] = useState("");

  const updateFromToken = useCallback(() => {
    const token = getAccessToken();
    if (token && !isTokenExpired()) {
      setIsAuthenticated(true);
      const payload = getTokenPayload();
      setUserId(getUserId());
      setRole(getUserRole());
      setDisplayName((payload?.name as string) ?? (payload?.display_name as string) ?? "");
      setAvatarUrl((payload?.avatar_url as string) ?? "");
    } else {
      if (token && isTokenExpired()) {
        clearAccessToken();
      }
      setIsAuthenticated(false);
      setUserId("local");
      setRole("admin");
      setDisplayName("");
      setAvatarUrl("");
    }
  }, []);

  useEffect(() => {
    if (!isAuthEnabled()) {
      setIsReady(true);
      setIsAuthenticated(true);
      setUserId("local");
      setRole("admin");
      return;
    }

    updateFromToken();

    if (!getAccessToken()) {
      refreshToken().then((ok) => {
        if (ok) updateFromToken();
        setIsReady(true);
      });
    } else {
      setIsReady(true);
    }
  }, [updateFromToken]);

  // 周期性刷新: 临近过期就刷, 失败且过期则登出。
  useTokenRefreshLoop(updateFromToken, () => {
    clearAccessToken();
    setIsAuthenticated(false);
  });

  const syncAuth = useCallback(() => {
    updateFromToken();
    if (!isReady) setIsReady(true);
  }, [updateFromToken, isReady]);

  const logout = useCallback(() => {
    doLogout();
    setIsAuthenticated(false);
    setDisplayName("");
    setAvatarUrl("");
  }, []);

  return (
    <AuthContext.Provider
      value={{
        isReady,
        isAuthenticated,
        authEnabled: isAuthEnabled(),
        userId,
        role,
        displayName,
        avatarUrl,
        logout,
        syncAuth,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
