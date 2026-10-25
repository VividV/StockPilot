"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Field, Notice } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// What each choice switches on, in words the owner understands.
const WHAT_YOU_GET: Record<string, string> = {
  retail: "A till for counter sales (works offline), barcodes and sizes/colours.",
  ecommerce: "A till for counter sales, barcodes and product variants.",
  wholesale: "Barcodes, batches and shelf/bin locations.",
  manufacturing: "Raw materials, production and batches.",
  construction: "Site locations and transfer approval.",
  hospitality: "A till for counter sales, expiry dates and batches for perishables.",
  healthcare: "A till for counter sales, batch numbers, expiry dates and serial numbers.",
  agriculture: "Batches and expiry dates.",
  automotive: "Serial / part numbers and barcodes.",
  field_service: "Van and technician stock, serial numbers, approvals.",
  education: "Simple stock tracking per site.",
  office: "Simple stock tracking per site.",
  other: "Simple stock tracking. Switch on more any time in Settings.",
};

export default function SignupPage() {
  const { signUp } = useAuth();
  const router = useRouter();
  const { data: industries } = useApi<Record<string, string>>("/auth/industries");
  const [step, setStep] = useState(1);
  const [f, setF] = useState({ industry: "", organization_name: "", currency: "USD", full_name: "", email: "", password: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));

  if (step === 1) {
    return (
      <div className="auth-card stack">
        <div><p className="meta">Step 1 of 2</p><h1 style={{ marginTop: 4 }}>What type of business do you run?</h1>
          <p className="muted" style={{ marginTop: 6 }}>We’ll switch on the features that fit. You can change this later.</p></div>
        <div className="industry-grid" role="group" aria-label="Business type">
          {Object.entries(industries || {}).map(([k, label]) => (
            <button key={k} type="button" className="choice" aria-pressed={f.industry === k} onClick={() => set("industry", k)}>{label}</button>
          ))}
        </div>
        {f.industry && <Notice tone="info">{WHAT_YOU_GET[f.industry]}</Notice>}
        <button className="btn primary block" disabled={!f.industry} onClick={() => setStep(2)}>Continue</button>
        <p className="muted" style={{ textAlign: "center" }}>Already have an account? <Link href="/login">Sign in</Link></p>
      </div>
    );
  }

  return (
    <form className="auth-card stack" onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true); setErr(null);
      let timezone = "";
      try { timezone = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { /* old browser */ }
      try { await signUp({ ...f, timezone }); router.replace("/"); } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
    }}>
      <div><p className="meta">Step 2 of 2</p><h1 style={{ marginTop: 4 }}>Your business and login</h1></div>
      {err && <Notice tone="bad">{err}</Notice>}
      <Field label="Business name" htmlFor="org"><input id="org" className="input" required value={f.organization_name} onChange={(e) => set("organization_name", e.target.value)} /></Field>
      <Field label="Currency" htmlFor="cur" hint="Three-letter code, e.g. USD, NGN, GBP, KES">
        <input id="cur" className="input" required maxLength={3} value={f.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} />
      </Field>
      <Field label="Your name" htmlFor="name"><input id="name" className="input" autoComplete="name" required value={f.full_name} onChange={(e) => set("full_name", e.target.value)} /></Field>
      <Field label="Email" htmlFor="email"><input id="email" className="input" type="email" autoComplete="email" required value={f.email} onChange={(e) => set("email", e.target.value)} /></Field>
      <Field label="Password" htmlFor="pw" hint="At least 10 characters with upper- and lower-case letters and a number.">
        <input id="pw" className="input" type="password" autoComplete="new-password" required minLength={10} value={f.password} onChange={(e) => set("password", e.target.value)} />
      </Field>
      <button className="btn primary block" disabled={busy}>{busy ? "Setting up…" : "Create my workspace"}</button>
      <button type="button" className="btn link" onClick={() => setStep(1)}>Back</button>
    </form>
  );
}
