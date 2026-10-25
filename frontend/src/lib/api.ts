// Thin fetch wrapper. The access token lives in memory only; the refresh token
// is an httpOnly cookie the browser sends to /api/auth/refresh.

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
let lastRefreshUnreachable = false;
/** True when the last session refresh failed because the server couldn't be reached (offline), not because it said no. */
export function refreshWasUnreachable() {
  return lastRefreshUnreachable;
}
const listeners = new Set<() => void>();

export function setToken(t: string | null) {
  accessToken = t;
}
export function getToken() {
  return accessToken;
}
export function onSessionExpired(fn: () => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export class ApiError extends Error {
  status: number;
  code?: string;
  data: unknown;
  constructor(status: number, message: string, data?: unknown) {
    super(message);
    this.status = status;
    this.data = data;
    this.code = (data as { code?: string })?.code;
  }
}

export async function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch("/api/auth/refresh", { method: "POST", headers: { "X-Requested-With": "StockPilot" }, credentials: "same-origin" })
      .then(async (r) => {
        lastRefreshUnreachable = r.status >= 500;
        if (!r.ok) return false;
        setToken((await r.json()).access_token);
        return true;
      })
      .catch(() => { lastRefreshUnreachable = true; return false; })
      .finally(() => setTimeout(() => (refreshing = null), 0));
  }
  return refreshing;
}

type Opts = { method?: string; body?: unknown; query?: Record<string, unknown>; raw?: boolean };

export function qs(query?: Record<string, unknown>) {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function api<T = any>(path: string, opts: Opts = {}, retried = false): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "StockPilot" };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}${qs(opts.query)}`, {
    method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401 && !retried && !path.startsWith("/auth/")) {
    if (await refreshSession()) return api<T>(path, opts, true);
    listeners.forEach((fn) => fn());
  }
  if (!res.ok) {
    let data: any = null;
    try { data = await res.json(); } catch { /* not json */ }
    const msg = typeof data?.detail === "string" ? data.detail : res.status === 403 ? "You don't have permission to do this." : `Something went wrong (${res.status}). Try again.`;
    throw new ApiError(res.status, msg, data);
  }
  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return res.json();
}

export async function download(path: string, query?: Record<string, unknown>) {
  const res = await api<Response>(path, { query, raw: true });
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const name = /filename="([^"]+)"/.exec(cd)?.[1] || "export";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
