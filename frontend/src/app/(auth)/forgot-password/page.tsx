"use client";
import Link from "next/link";
import { useState } from "react";
import { Field, Notice } from "@/components/ui";
import { api } from "@/lib/api";
import { errMsg } from "@/lib/format";

export default function Forgot() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form className="auth-card stack" onSubmit={async (e) => {
      e.preventDefault(); setBusy(true); setErr(null);
      try { setDone((await api("/auth/forgot-password", { body: { email } })).message); } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
    }}>
      <div><h1>Reset your password</h1><p className="muted" style={{ marginTop: 6 }}>We’ll email you a link to choose a new one.</p></div>
      {err && <Notice tone="bad">{err}</Notice>}
      {done ? <Notice tone="ok">{done}</Notice> : <>
        <Field label="Email" htmlFor="email"><input id="email" className="input" type="email" autoComplete="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <button className="btn primary block" disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button>
      </>}
      <p className="muted">No access to that inbox? Ask your StockPilot owner or administrator to set a new password for you.</p>
      <Link href="/login">Back to sign in</Link>
    </form>
  );
}
