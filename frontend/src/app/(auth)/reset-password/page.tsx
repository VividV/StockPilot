"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Field, Notice } from "@/components/ui";
import { api } from "@/lib/api";
import { errMsg } from "@/lib/format";

export default function Reset() {
  const [token, setToken] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setToken(new URLSearchParams(window.location.search).get("token") || ""); }, []);
  if (done) return (
    <div className="auth-card stack"><h1>Password changed</h1><Notice tone="ok">{done}</Notice><Link className="btn primary block" href="/login">Sign in</Link></div>
  );
  return (
    <form className="auth-card stack" onSubmit={async (e) => {
      e.preventDefault();
      if (pw !== pw2) return setErr("The two passwords are different.");
      setBusy(true); setErr(null);
      try { setDone((await api("/auth/reset-password", { body: { token, password: pw } })).message); } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
    }}>
      <h1>Choose a new password</h1>
      {!token && <Notice tone="warn">This page needs the link from your email. <Link href="/forgot-password">Ask for a new link</Link>.</Notice>}
      {err && <Notice tone="bad">{err}{/expired|used/.test(err) && <> <Link href="/forgot-password">Ask for a new link</Link>.</>}</Notice>}
      <Field label="New password" htmlFor="pw" hint="At least 10 characters with upper- and lower-case letters and a number.">
        <input id="pw" className="input" type="password" autoComplete="new-password" required minLength={10} autoFocus value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
      <Field label="Type it again" htmlFor="pw2"><input id="pw2" className="input" type="password" autoComplete="new-password" required value={pw2} onChange={(e) => setPw2(e.target.value)} /></Field>
      <button className="btn primary block" disabled={busy || !token}>{busy ? "Saving…" : "Save new password"}</button>
      <p className="meta">You’ll be signed out on every other device.</p>
    </form>
  );
}
