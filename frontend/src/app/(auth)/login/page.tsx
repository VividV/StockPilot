"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Field, Notice } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";

export default function LoginPage() {
  const { signIn, finishTwoFactor, me } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dest = () => {
    const n = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("next") : null;
    return n && n.startsWith("/") && !n.startsWith("//") ? n : "/";
  };
  useEffect(() => { if (me) router.replace(dest()); }, [me, router]);

  if (challenge) return (
    <form className="auth-card stack" onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true); setErr(null);
      try { await finishTwoFactor(challenge, code); router.replace(dest()); }
      catch (e) { setErr(errMsg(e)); if (/sign in again|too long/i.test(errMsg(e))) setChallenge(null); }
      finally { setBusy(false); }
    }}>
      <div><h1>Two-step verification</h1><p className="muted" style={{ marginTop: 6 }}>Open your authenticator app and type the 6-digit code for StockPilot.</p></div>
      {err && <Notice tone="bad">{err}</Notice>}
      <Field label="Code" htmlFor="code" hint="Lost your phone? Type one of your recovery codes instead.">
        <input id="code" className="input num" style={{ fontSize: 22, letterSpacing: 4 }} inputMode="numeric" autoComplete="one-time-code" autoFocus required value={code} onChange={(e) => setCode(e.target.value)} />
      </Field>
      <button className="btn primary block" disabled={busy}>{busy ? "Checking…" : "Sign in"}</button>
      <button type="button" className="btn link" onClick={() => { setChallenge(null); setCode(""); }}>Back</button>
    </form>
  );

  return (
    <form className="auth-card stack" onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true); setErr(null);
      try { const ch = await signIn(email, password); if (ch) setChallenge(ch); else router.replace(dest()); }
      catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
    }}>
      <div><h1>Sign in</h1><p className="muted" style={{ marginTop: 6 }}>Use the email your team invited you with.</p></div>
      {err && <Notice tone="bad">{err}</Notice>}
      <Field label="Email" htmlFor="email"><input id="email" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Password" htmlFor="pw"><input id="pw" className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
      <button className="btn primary block" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      <div className="spread">
        <Link href="/forgot-password">Forgot your password?</Link>
        <span className="muted">New? <Link href="/signup">Set up your business</Link></span>
      </div>
    </form>
  );
}
