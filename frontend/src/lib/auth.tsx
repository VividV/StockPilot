"use client";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, onSessionExpired, refreshSession, refreshWasUnreachable, setToken } from "./api";
import type { Me } from "./types";

type AuthState = {
  me: Me | null;
  loading: boolean;
  /** Signed in from this device's copy because the server can't be reached. Only the till works like this. */
  offline: boolean;
  can: (perm: string) => boolean;
  mod: (module: string) => boolean;
  /** Resolves to a challenge token when a two-step code is still needed. */
  signIn: (email: string, password: string) => Promise<string | null>;
  finishTwoFactor: (challenge: string, code: string) => Promise<void>;
  signUp: (body: Record<string, string>) => Promise<void>;
  signOut: () => Promise<void>;
  reload: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);
const ME_KEY = "sp_me";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const router = useRouter();

  const reload = useCallback(async () => {
    const m = await api<Me>("/auth/me");
    setMe(m);
    setOffline(false);
    try { localStorage.setItem(ME_KEY, JSON.stringify(m)); } catch { /* storage full or blocked */ }
  }, []);

  useEffect(() => {
    (async () => {
      if (await refreshSession()) {
        try { await reload(); } catch { /* signed out */ }
      } else if (refreshWasUnreachable()) {
        // No internet: let the till keep working with who was signed in last on this device.
        try {
          const cached = localStorage.getItem(ME_KEY);
          if (cached) { setMe(JSON.parse(cached)); setOffline(true); }
        } catch { /* ignore */ }
      }
      setLoading(false);
    })();
    return onSessionExpired(() => {
      setToken(null);
      setMe(null);
      try { localStorage.removeItem(ME_KEY); } catch { /* ignore */ }
      router.replace("/login");
    });
  }, [reload, router]);

  const signIn = async (email: string, password: string) => {
    const r = await api<{ access_token: string | null; two_factor_required: boolean; challenge_token: string | null }>("/auth/login", { body: { email, password } });
    if (r.two_factor_required) return r.challenge_token;
    setToken(r.access_token);
    await reload();
    return null;
  };
  const finishTwoFactor = async (challenge: string, code: string) => {
    const r = await api<{ access_token: string }>("/auth/login/2fa", { body: { challenge_token: challenge, code } });
    setToken(r.access_token);
    await reload();
  };
  const signUp = async (body: Record<string, string>) => {
    const r = await api<{ access_token: string }>("/auth/signup", { body });
    setToken(r.access_token);
    await reload();
  };
  const signOut = async () => {
    try { await api("/auth/logout", { method: "POST" }); } catch { /* ignore */ }
    setToken(null);
    setMe(null);
    try { localStorage.removeItem(ME_KEY); } catch { /* ignore */ }
    router.replace("/login");
  };
  const can = (perm: string) => !!me?.permissions.includes(perm);
  const mod = (m: string) => !!me?.organization.settings.modules?.[m];

  return <Ctx.Provider value={{ me, loading, offline, can, mod, signIn, finishTwoFactor, signUp, signOut, reload }}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}
