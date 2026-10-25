"use client";
import { useEffect, useState } from "react";
import { Field, Loading, Notice, PageHead, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, dateTime, errMsg, money as fmtMoney } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type Plan = { key: string; name: string; blurb: string; users: number | null; locations: number | null; monthly: number; yearly: number; currency: string };
type Billing = {
  enabled: boolean; configured?: boolean; plan?: string; plan_name?: string; status?: string; interval?: string; trial_ends_at?: string | null;
  current_period_end?: string | null; days_left?: number | null; auto_renew?: boolean; locked?: boolean; billing_email?: string | null;
  card?: { brand: string; last4: string; exp: string } | null; limits?: { users: number | null; locations: number | null };
  usage?: { users: number; locations: number }; plans?: Plan[]; grace_ends_at?: string | null;
  payments?: { reference: string; plan: string; interval: string; amount: number; currency: string; status: string; kind: string; channel: string | null; paid_at: string | null; created_at: string; failure_reason: string | null }[];
};

const STATUS: Record<string, { label: string; tone: string }> = {
  trialing: { label: "Free trial", tone: "info" }, active: { label: "Active", tone: "ok" }, past_due: { label: "Payment due", tone: "warn" },
  expired: { label: "Ended", tone: "bad" }, cancelled: { label: "Cancelled", tone: "bad" },
};

export default function BillingPage() {
  const { can } = useAuth();
  const toast = useToast();
  const { data: b, setData, reload } = useApi<Billing>("/billing");
  const [interval, setPeriod] = useState<"monthly" | "yearly">("monthly");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const manage = can("billing.manage");

  // back from the Paystack payment page
  useEffect(() => {
    const ref = new URLSearchParams(window.location.search).get("reference") || new URLSearchParams(window.location.search).get("trxref");
    if (!ref || !manage) return;
    window.history.replaceState(null, "", "/billing");
    setBusy("verify");
    api<{ status: string; billing: Billing }>("/billing/verify", { body: { reference: ref } })
      .then((r) => {
        setData(r.billing);
        if (r.status === "success") toast("Payment received. Thank you!");
        else if (r.status === "failed") setErr("The payment didn’t go through. You haven’t been charged; try again or use another card or bank.");
        else setErr("We’re still waiting for Paystack to confirm the payment. Refresh this page in a minute.");
      })
      .catch((e) => setErr(errMsg(e)))
      .finally(() => setBusy(null));
  }, [manage, setData, toast]);

  if (!b) return <Loading />;
  if (!b.enabled) {
    return (
      <>
        <PageHead title="Subscription" />
        <Notice tone="info">Subscriptions are switched off on this server, so every feature is available with no limits. That’s right when you run StockPilot for your own business.
          To sell StockPilot to other businesses, set <code>BILLING_ENABLED=true</code> and your Paystack key (see the deployment guide).</Notice>
      </>
    );
  }
  const st = STATUS[b.status || ""] || { label: b.status, tone: "" };
  const end = b.status === "trialing" ? b.trial_ends_at : b.current_period_end;
  const checkout = async (plan: string) => {
    setBusy(plan); setErr(null);
    try {
      const r = await api<{ authorization_url: string }>("/billing/checkout", { body: { plan, interval } });
      window.location.href = r.authorization_url;
    } catch (e) { setErr(errMsg(e)); setBusy(null); }
  };
  return (
    <>
      <PageHead title="Subscription" sub="Your StockPilot plan and payments." />
      <div className="stack" style={{ gap: 20 }}>
        {err && <Notice tone="bad">{err}</Notice>}
        {busy === "verify" && <Notice tone="info">Checking your payment with Paystack…</Notice>}
        {b.locked && <Notice tone="bad"><b>Your subscription has ended.</b> Everything is still here and you can look at it, but nothing can be changed until you choose a plan below.</Notice>}
        {b.status === "past_due" && <Notice tone="warn">We couldn’t take the last payment. Pay below before {date(b.grace_ends_at || null)} to keep making changes.</Notice>}
        <div className="stats">
          <div className="stat"><div className="k">Plan</div><div className="v">{b.plan_name} {b.status !== "trialing" && <span className={`badge ${st.tone}`} style={{ verticalAlign: "middle" }}>{st.label}</span>}</div>
            <div className="meta">{end ? `${b.status === "trialing" ? "Trial ends" : b.auto_renew ? "Renews" : "Ends"} ${date(end)}${b.days_left !== null && b.days_left !== undefined ? ` · ${b.days_left} day${b.days_left !== 1 ? "s" : ""} left` : ""}` : ""}</div></div>
          {b.usage && b.limits && <>
            <div className="stat"><div className="k">Team members</div><div className="v qty">{b.usage.users}<span className="u">/ {b.limits.users ?? "∞"}</span></div></div>
            <div className="stat"><div className="k">Locations</div><div className="v qty">{b.usage.locations}<span className="u">/ {b.limits.locations ?? "∞"}</span></div></div>
          </>}
        </div>

        {manage && b.plans && <>
          {!b.configured && <Notice tone="warn">Payments aren’t set up on this server yet (the Paystack key is missing), so plans can’t be bought.</Notice>}
          <div className="spread">
            <h2>{b.status === "active" ? "Change or renew your plan" : "Choose a plan"}</h2>
            <div className="seg" role="group" aria-label="How often to pay">
              <button type="button" aria-pressed={interval === "monthly"} onClick={() => setPeriod("monthly")}>Monthly</button>
              <button type="button" aria-pressed={interval === "yearly"} onClick={() => setPeriod("yearly")}>Yearly (2 months free)</button>
            </div>
          </div>
          <div className="plans">
            {b.plans.map((p) => {
              const current = b.plan === p.key && b.status === "active";
              return (
                <div key={p.key} className={`panel panel-pad stack plan ${current ? "current" : ""}`}>
                  <div><h3>{p.name}</h3><p className="meta">{p.blurb}</p></div>
                  <div><span className="qty plan-price">{fmtMoney(interval === "yearly" ? p.yearly : p.monthly, p.currency)}</span><span className="meta"> / {interval === "yearly" ? "year" : "month"}</span></div>
                  <ul className="plan-list">
                    <li>{p.users === null ? "Unlimited team members" : `Up to ${p.users} team members`}</li>
                    <li>{p.locations === null ? "Unlimited shops and warehouses" : p.locations === 1 ? "1 shop or warehouse" : `Up to ${p.locations} shops or warehouses`}</li>
                    <li>Till, offline selling, receipts</li><li>Stock, orders, reports and alerts</li>
                  </ul>
                  <button className={`btn ${current ? "" : "go"} block`} disabled={!!busy || !b.configured} onClick={() => checkout(p.key)}>
                    {busy === p.key ? "Opening Paystack…" : current ? "Pay for another period" : `Choose ${p.name}`}</button>
                </div>
              );
            })}
          </div>
          <p className="meta">You pay securely on Paystack by card, bank transfer or USSD. With a card, the plan renews automatically; otherwise we remind you to pay again.</p>

          <div className="grid two">
            <div className="panel panel-pad stack">
              <h2>Payment details</h2>
              {b.card ? <p>Card: <b>{b.card.brand?.toUpperCase()} •••• {b.card.last4}</b> <span className="meta">expires {b.card.exp}</span></p> : <p className="meta">No saved card. Pay with a card once to renew automatically.</p>}
              {b.status === "active" && (b.auto_renew
                ? <button className="btn small" style={{ alignSelf: "flex-start" }} onClick={async () => { if (confirm("Stop automatic renewal? Your plan keeps working until the end of the period you paid for.")) { setData(await api("/billing/cancel", { method: "POST" })); toast("Automatic renewal is off"); } }}>Turn off automatic renewal</button>
                : <button className="btn small" style={{ alignSelf: "flex-start" }} onClick={async () => { setData(await api("/billing/resume", { method: "POST" })); toast("Automatic renewal is on"); }}>Turn automatic renewal back on</button>)}
            </div>
            <form className="panel panel-pad stack" onSubmit={async (e) => { e.preventDefault(); try { setData(await api("/billing/email", { method: "PUT", body: { billing_email: email } })); toast("Saved"); setEmail(""); } catch (x) { setErr(errMsg(x)); } }}>
              <h2>Receipts go to</h2>
              <Field label="Billing email" htmlFor="be"><input id="be" type="email" className="input" placeholder={b.billing_email || "Same as the owner’s email"} value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
              <button className="btn small" style={{ alignSelf: "flex-start" }} disabled={!email}>Save</button>
            </form>
          </div>

          <section className="panel">
            <div className="panel-head"><h2>Payments</h2><button className="btn small" onClick={reload}>Refresh</button></div>
            {!b.payments?.length ? <p className="panel-pad meta">No payments yet.</p> : (
              <div className="table-wrap"><table className="t">
                <thead><tr><th>Date</th><th>Plan</th><th className="hide-mobile">Reference</th><th className="r">Amount</th><th>Status</th></tr></thead>
                <tbody>{b.payments.map((x) => (
                  <tr key={x.reference}>
                    <td className="num">{dateTime(x.paid_at || x.created_at)}</td>
                    <td>{x.plan[0].toUpperCase() + x.plan.slice(1)} · {x.interval}{x.kind === "renewal" ? " · automatic" : ""}{x.failure_reason && <span className="sub bad-text">{x.failure_reason}</span>}</td>
                    <td className="hide-mobile num meta">{x.reference}</td>
                    <td className="r num">{fmtMoney(x.amount, x.currency)}</td>
                    <td>{x.status === "success" ? <span className="badge ok">Paid</span> : x.status === "failed" ? <span className="badge bad">Failed</span> : <span className="badge">Not finished</span>}</td>
                  </tr>))}</tbody>
              </table></div>
            )}
          </section>
        </>}
        {!manage && <p className="meta">Only the business owner can change the plan.</p>}
      </div>
    </>
  );
}
