"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { LinesEditor, linesPayload, type OrderLine } from "@/components/orderlines";
import { LocationSelect, PartnerSelect } from "@/components/pickers";
import { Field, Notice, NoAccess, PageHead, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";

export default function NewSale() {
  const { can, me } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const [f, setF] = useState({ customer_id: "", source_location_id: "", customer_reference: "", notes: "" });
  const [lines, setLines] = useState<OrderLine[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!can("sales.create")) return <NoAccess />;
  const save = async (mode: "draft" | "confirm" | "ship") => {
    if (!lines.length) return setErr("Add at least one product.");
    setBusy(true); setErr(null);
    try {
      const so = await api("/sales-orders", { body: { ...f, customer_id: f.customer_id || undefined, customer_reference: f.customer_reference || undefined,
        notes: f.notes || undefined, lines: linesPayload(lines, "sale"), confirm: mode !== "draft" } });
      if (mode === "ship") {
        try { await api(`/sales-orders/${so.id}/fulfil`, { method: "POST" }); toast(`${so.number} created and sent`); }
        catch (e) { toast(`${so.number} saved, but not sent: ${errMsg(e)}`, "bad"); }
      } else toast(`${so.number} ${mode === "draft" ? "saved as draft" : "confirmed"}`);
      router.push(`/sales/${so.id}`);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  const simple = lines.length > 0 && !lines.some((l) => l.on_hand === "");
  return (
    <div style={{ maxWidth: 960 }}>
      <PageHead title="New sales order" sub="Stock goes down when the goods are sent, not when the order is taken." />
      <div className="panel panel-pad stack">
        {err && <Notice tone="bad">{err}</Notice>}
        <div className="grid two">
          <PartnerSelect kind="customers" label="Customer" optionalText="Walk-in / not recorded" value={f.customer_id} onChange={(v) => setF({ ...f, customer_id: v })} />
          <LocationSelect label="Send from" value={f.source_location_id} onChange={(v) => setF({ ...f, source_location_id: v })} />
        </div>
        <Field label="Customer’s order number" htmlFor="cr"><input id="cr" className="input" value={f.customer_reference} onChange={(e) => setF({ ...f, customer_reference: e.target.value })} /></Field>
        <LinesEditor kind="sale" lines={lines} onChange={setLines} currency={me?.organization.currency} />
        <Field label="Notes" htmlFor="sn"><textarea id="sn" className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        <div className="row end">
          <button className="btn" disabled={busy} onClick={() => save("draft")}>Save as draft</button>
          <button className="btn primary" disabled={busy} onClick={() => save("confirm")}>Confirm order</button>
          {simple && can("sales.fulfill") && can("inventory.issue") && <button className="btn go" disabled={busy} onClick={() => save("ship")}>Confirm and send now</button>}
        </div>
      </div>
    </div>
  );
}
