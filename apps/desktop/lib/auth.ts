/**
 * Desktop authentication helpers — Community edition.
 *
 * Auth is disabled in Community edition. All functions return safe defaults.
 * Token storage helpers exist so api-client can inject Authorization headers
 * when a token is present (e.g. after a future Pro upgrade).
 */

const TOKEN_KEY = "selenespace_access_token";
const REFRESH_KEY = "selenespace_refresh_token";

export function isAuthEnabled(): boolean {
  return false;
}

// -- token 存储(getter/setter 成对) ------------------------------

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setAccessToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearAccessToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_KEY);
}

export function setRefreshToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(REFRESH_KEY, token);
}

export function getTokenPayload(): Record<string, unknown> | null {
  const token = getAccessToken();
  if (!token) return null;
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const payload = parts[1];
    const decoded = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(decoded);
  } catch {
    return null;
  }
}

/** Community 版固定以 admin 身份运行。 */
export function getUserRole(): string {
  return "admin";
}

/** Community 版固定为 local 用户。 */
export function getUserId(): string {
  return "local";
}

export async function login(_provider: string): Promise<void> {}

export async function refreshToken(): Promise<boolean> {
  return false;
}

export function logout(): void {
  clearAccessToken();
  if (typeof window !== "undefined") {
    window.location.href = "/";
  }
}

export async function authFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  return fetch(input, init);
}

/** 授权回调结果三态(Pro 版才真正使用; Community 恒 error)。 */
export type OAuthCallbackResult =
  | { status: "ok" }
  | { status: "totp"; partialToken: string }
  | { status: "error" };

export async function handleCallback(
  _provider: string,
  _code: string,
  _state?: string,
): Promise<OAuthCallbackResult> {
  return { status: "error" };
}
