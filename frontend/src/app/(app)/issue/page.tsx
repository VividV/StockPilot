"use client";
import { useState } from "react";
import { LocationSelect, PartnerSelect, ProductPicker, UnitChoice } from "@/components/pickers";
import { usePreselect, AvailableLine, BatchSelect, Done, SerialPicker } from "@/components/stockform";
import { Field, Notice, NoAccess, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";
import type { ProductDetail } from "@/lib/types";

const TYPES = [
  { v: "sale", l: "Sold to a customer", verb: "Sell" },
  { v: "issue", l: "Given out / used (staff, job, department)", verb: "Issue" },
  { v: "production_consumption", l: "Used in production", verb: "Use" },
  { v: "damaged", l: "Damaged", verb: "Write off" },
  { v: "lost", l: "Lost or stolen", verb: "Write off" },
  { v: "expired", l: "Expired", verb: "Write off" },
];

export default function IssuePage() {
  const { can } = useAuth();
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  usePreselect(setDetail);
  const [f, setF] = useState({ location_id: "", quantity: "", unit_id: "", batch_id: "", type: "sale", customer_id: "", issued_to: "", reference_no: "", note: "" });
  const [serials, setSerials] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<any>(null);
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));
  if (!can("inventory.issue")) return <NoAccess />;
  const p = detail?.product;
  const t = TYPES.find((x) => x.v === f.type)!;

  if (done) return (
    <div className="form-panel"><PageHead title="Issue stock" />
      <Done title={done.message} result={done} productId={p?.id} againLabel="Issue another item" onAgain={() => {
        setDone(null); setDetail(null); setSerials([]); setF((x) => ({ ...x, quantity: "", unit_id: "", batch_id: "", note: "", reference_no: "" }));
      }} />
    </div>
  );

  return (
    <div className="form-panel">
      <PageHead title="Issue stock" sub="Record stock leaving: sales, use, damage or loss." />
      <form className="panel panel-pad stack" onSubmit={async (e) => {
        e.preventDefault();
        if (!p) return setErr("Choose a product first.");
        setBusy(true); setErr(null);
        try {
          setDone(await api("/inventory/issue", { body: {
            product_id: p.id, location_id: f.location_id, quantity: p.track_serials ? serials.length : f.quantity, type: f.type,
            unit_id: f.unit_id || undefined, batch_id: f.batch_id || undefined, serials: p.track_serials ? serials : undefined,
            customer_id: f.type === "sale" && f.customer_id ? f.customer_id : undefined,
            issued_to: f.issued_to || undefined, reference_no: f.reference_no || undefined, note: f.note || undefined } }));
        } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
      }}>
        {err && <Notice tone="bad">{err}</Notice>}
        <Field label="What’s happening?" htmlFor="ty">
          <select id="ty" className="input" value={f.type} onChange={(e) => set("type", e.target.value)}>
            {TYPES.map((x) => <option key={x.v} value={x.v}>{x.l}</option>)}
          </select>
        </Field>
        <ProductPicker value={detail} onChange={(d) => { setDetail(d); setSerials([]); set("batch_id", ""); }} autoFocus />
        <LocationSelect label="Take it from" value={f.location_id} onChange={(v) => { set("location_id", v); setSerials([]); }} />
        <AvailableLine detail={detail} locationId={f.location_id} />
        {p?.track_serials ? (
          <SerialPicker detail={detail!} locationId={f.location_id} value={serials} onChange={setSerials} need={serials.length} />
        ) : (
          <div className="grid two">
            <Field label="How many?" htmlFor="q"><input id="q" className="input num" inputMode="decimal" required value={f.quantity} onChange={(e) => set("quantity", e.target.value)} /></Field>
            {detail && <UnitChoice detail={detail} value={f.unit_id} onChange={(v) => set("unit_id", v)} />}
          </div>
        )}
        {p?.track_batches && !p.track_serials && <BatchSelect detail={detail!} locationId={f.location_id} value={f.batch_id} onChange={(v) => set("batch_id", v)} />}
        {f.type === "sale" ? (
          <PartnerSelect kind="customers" label="Customer" optionalText="Walk-in / not recorded" value={f.customer_id} onChange={(v) => set("customer_id", v)} />
        ) : ["issue", "production_consumption"].includes(f.type) ? (
          <Field label="Given to" htmlFor="to" hint="Person, job, vehicle or department"><input id="to" className="input" value={f.issued_to} onChange={(e) => set("issued_to", e.target.value)} /></Field>
        ) : null}
        <div className="grid two">
          <Field label="Receipt / job number" htmlFor="ref"><input id="ref" className="input" value={f.reference_no} onChange={(e) => set("reference_no", e.target.value)} /></Field>
          <Field label="Note" htmlFor="n"><input id="n" className="input" value={f.note} onChange={(e) => set("note", e.target.value)} /></Field>
        </div>
        <button className="btn go block" disabled={busy || !p || (p.track_serials && serials.length === 0)}>{busy ? "Saving…" : `${t.verb} stock`}</button>
      </form>
    </div>
  );
}
