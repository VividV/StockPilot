"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Shell } from "@/components/shell";
import { useAuth } from "@/lib/auth";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  const router = useRouter();
  useEffect(() => { if (!loading && !me) router.replace("/login"); }, [loading, me, router]);
  if (!me) return <div className="empty" style={{ paddingTop: "30vh" }}><p className="muted">Loading StockPilot…</p></div>;
  return <Shell>{children}</Shell>;
}
