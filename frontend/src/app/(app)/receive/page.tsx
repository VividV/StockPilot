"use client";
import { useState } from "react";
import { LocationSelect, PartnerSelect, ProductPicker, UnitChoice } from "@/components/pickers";
import { Done, SerialEntry, parseSerials, usePreselect } from "@/components/stockform";
import { Field, Notice, NoAccess, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";
import type { ProductDetail } from "@/lib/types";

const REASONS = [
  { v: "purchase_receipt", l: "Delivery from a supplier" },
  { v: "return", l: "Customer return" },
  { v: "production_output", l: "Made in-house (production)" },
  { v: "adjustment_in", l: "Found / other" },
];

export default function ReceivePage() {
  const { can } = useAuth();
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  usePreselect(setDetail);
  const [f, setF] = useState({ location_id: "", quantity: "", unit_id: "", unit_cost: "", batch_number: "", expires_on: "",
    manufactured_on: "", serials: "", supplier_id: "", reference_no: "", note: "", type: "purchase_receipt" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<any>(null);
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));
  if (!can("inventory.receive")) return <NoAccess />;
  const p = detail?.product;

  if (done) return (
    <div className="form-panel"><PageHead title="Receive stock" />
      <Done title={done.message} result={done} productId={p?.id} againLabel="Receive another item" onAgain={() => {
        setDone(null); setDetail(null); setF((x) => ({ ...x, quantity: "", unit_id: "", unit_cost: "", batch_number: "", expires_on: "", manufactured_on: "", serials: "", note: "" }));
      }} />
    </div>
  );

  return (
    <div className="form-panel">
      <PageHead title="Receive stock" sub="Record stock arriving: deliveries, returns, or things you made." />
      <form className="panel panel-pad stack" onSubmit={async (e) => {
        e.preventDefault();
        if (!p) return setErr("Choose a product first.");
        setBusy(true); setErr(null);
        try {
          const body: Record<string, unknown> = { product_id: p.id, location_id: f.location_id, quantity: f.quantity, type: f.type,
            unit_id: f.unit_id || undefined, unit_cost: f.unit_cost || undefined, supplier_id: f.supplier_id || undefined,
            reference_no: f.reference_no || undefined, note: f.note || undefined };
          if (p.track_batches) Object.assign(body, { batch_number: f.batch_number, expires_on: f.expires_on || undefined, manufactured_on: f.manufactured_on || undefined });
          if (p.track_serials) body.serials = parseSerials(f.serials);
          setDone(await api("/inventory/receive", { body }));
        } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
      }}>
        {err && <Notice tone="bad">{err}</Notice>}
        <ProductPicker value={detail} onChange={(d) => { setDetail(d); set("unit_id", ""); if (d) set("unit_cost", ""); }} autoFocus />
        <div className="grid two">
          <Field label="How many?" htmlFor="q">
            <input id="q" className="input num" inputMode="decimal" required value={f.quantity} onChange={(e) => set("quantity", e.target.value)}
                   placeholder={p ? `in ${f.unit_id ? detail?.conversions.find((c) => c.unit.id === f.unit_id)?.unit.abbreviation : p.base_unit.abbreviation}` : ""} />
          </Field>
          {detail && <UnitChoice detail={detail} value={f.unit_id} onChange={(v) => set("unit_id", v)} />}
        </div>
        <LocationSelect label="Put it in" value={f.location_id} onChange={(v) => set("location_id", v)} />

        {p?.track_batches && (
          <div className="grid three">
            <Field label="Batch / lot number" htmlFor="bn"><input id="bn" className="input" required value={f.batch_number} onChange={(e) => set("batch_number", e.target.value)} /></Field>
            <Field label={p.track_expiry ? "Expiry date" : "Expiry date (optional)"} htmlFor="ex"><input id="ex" type="date" className="input" required={p.track_expiry} value={f.expires_on} onChange={(e) => set("expires_on", e.target.value)} /></Field>
            <Field label="Made on (optional)" htmlFor="mf"><input id="mf" type="date" className="input" value={f.manufactured_on} onChange={(e) => set("manufactured_on", e.target.value)} /></Field>
          </div>
        )}
        {p?.track_serials && <SerialEntry value={f.serials} onChange={(v) => set("serials", v)} need={Number(f.quantity) || 0} />}

        <details>
          <summary className="label" style={{ cursor: "pointer" }}>More details (supplier, cost, reference)</summary>
          <div className="stack" style={{ marginTop: 14 }}>
            <Field label="Why is it coming in?" htmlFor="ty">
              <select id="ty" className="input" value={f.type} onChange={(e) => set("type", e.target.value)}>
                {REASONS.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}
              </select>
            </Field>
            <div className="grid two">
              <PartnerSelect kind="suppliers" label="Supplier" value={f.supplier_id} onChange={(v) => set("supplier_id", v)} />
              <Field label="Cost per unit" htmlFor="c" hint={p ? `Last cost ${p.cost_price}. Leave empty to keep the current average.` : undefined}>
                <input id="c" className="input num" inputMode="decimal" value={f.unit_cost} onChange={(e) => set("unit_cost", e.target.value)} />
              </Field>
            </div>
            <Field label="Delivery note / invoice number" htmlFor="ref"><input id="ref" className="input" value={f.reference_no} onChange={(e) => set("reference_no", e.target.value)} /></Field>
            <Field label="Note" htmlFor="n"><input id="n" className="input" value={f.note} onChange={(e) => set("note", e.target.value)} /></Field>
          </div>
        </details>
        <button className="btn go block" disabled={busy || !p}>{busy ? "Saving…" : "Receive stock"}</button>
      </form>
    </div>
  );
}
