"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { LinesEditor, linesPayload, type OrderLine } from "@/components/orderlines";
import { LocationSelect, PartnerSelect } from "@/components/pickers";
import { Field, Notice, NoAccess, PageHead, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";

export default function NewPurchase() {
  const { can, me } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const [f, setF] = useState({ supplier_id: "", destination_location_id: "", expected_date: "", supplier_reference: "", notes: "" });
  const [lines, setLines] = useState<OrderLine[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!can("purchases.create")) return <NoAccess />;
  const save = async (confirm: boolean) => {
    if (!f.supplier_id) return setErr("Choose a supplier.");
    if (!lines.length) return setErr("Add at least one product.");
    setBusy(true); setErr(null);
    try {
      const po = await api("/purchase-orders", { body: { ...f, expected_date: f.expected_date || undefined, supplier_reference: f.supplier_reference || undefined,
        notes: f.notes || undefined, lines: linesPayload(lines, "purchase"), confirm } });
      toast(`${po.number} ${confirm ? "confirmed" : "saved as draft"}`);
      router.push(`/purchases/${po.id}`);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <div style={{ maxWidth: 960 }}>
      <PageHead title="New purchase order" sub="Ordering doesn’t change your stock. Stock goes up when you receive the delivery." />
      <div className="panel panel-pad stack">
        {err && <Notice tone="bad">{err}</Notice>}
        <div className="grid two">
          <PartnerSelect kind="suppliers" label="Supplier" optionalText="Choose a supplier" value={f.supplier_id} onChange={(v) => setF({ ...f, supplier_id: v })} />
          <LocationSelect label="Deliver to" value={f.destination_location_id} onChange={(v) => setF({ ...f, destination_location_id: v })} />
        </div>
        <div className="grid two">
          <Field label="Expected delivery" htmlFor="ed"><input id="ed" type="date" className="input" value={f.expected_date} onChange={(e) => setF({ ...f, expected_date: e.target.value })} /></Field>
          <Field label="Supplier’s quote / reference" htmlFor="sr"><input id="sr" className="input" value={f.supplier_reference} onChange={(e) => setF({ ...f, supplier_reference: e.target.value })} /></Field>
        </div>
        <LinesEditor kind="purchase" lines={lines} onChange={setLines} currency={me?.organization.currency} />
        <Field label="Notes" htmlFor="pn"><textarea id="pn" className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        <div className="row end">
          <button className="btn" disabled={busy} onClick={() => save(false)}>Save as draft</button>
          <button className="btn primary" disabled={busy} onClick={() => save(true)}>Confirm order</button>
        </div>
      </div>
    </div>
  );
}
