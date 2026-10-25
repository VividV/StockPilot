"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Notice } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";

export default function Verify() {
  const { me, reload } = useAuth();
  const [state, setState] = useState<{ ok: boolean; msg: string } | null>(null);
  const once = useRef(false);
  useEffect(() => {
    if (once.current) return;
    once.current = true;
    const token = new URLSearchParams(window.location.search).get("token") || "";
    api("/auth/verify-email", { body: { token } })
      .then((r) => { setState({ ok: true, msg: r.message }); reload().catch(() => {}); })
      .catch((e) => setState({ ok: false, msg: errMsg(e) }));
  }, [reload]);
  return (
    <div className="auth-card stack">
      <h1>Confirm your email</h1>
      {!state ? <p className="muted">Checking your link…</p> : <Notice tone={state.ok ? "ok" : "bad"}>{state.msg}</Notice>}
      <Link className="btn primary block" href={me ? "/" : "/login"}>{me ? "Go to StockPilot" : "Sign in"}</Link>
    </div>
  );
}
