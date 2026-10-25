"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth";

/** The till runs full screen, without the main menu, and keeps working offline. */
export default function TillLayout({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  const router = useRouter();
  useEffect(() => { if (!loading && !me) router.replace("/login?next=/pos"); }, [loading, me, router]);
  useEffect(() => {
    if ("serviceWorker" in navigator && window.isSecureContext && process.env.NODE_ENV === "production") {
      navigator.serviceWorker.register("/sw.js").catch(() => { /* offline support is a bonus, never a blocker */ });
    }
  }, []);
  if (!me) return <div className="empty" style={{ paddingTop: "30vh" }}><p className="muted">Opening the till…</p></div>;
  return <div className="till-app">{children}</div>;
}
