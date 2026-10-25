"use client";
import Link from "next/link";
import { useState } from "react";
import { LocationSelect, ProductPicker, UnitChoice, useLocations } from "@/components/pickers";
import { usePreselect, AvailableLine, BatchSelect, Done, SerialPicker } from "@/components/stockform";
import { Field, Notice, NoAccess, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";
import type { ProductDetail } from "@/lib/types";

export default function TransferPage() {
  const { can, mod } = useAuth();
  const { data: locs } = useLocations();
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  usePreselect(setDetail);
  const [f, setF] = useState({ from: "", to: "", quantity: "", unit_id: "", batch_id: "", notes: "" });
  const [serials, setSerials] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<any>(null);
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));
  if (!can("inventory.transfer")) return <NoAccess />;
  const p = detail?.product;

  if (locs && locs.length < 2) return (
    <div className="form-panel"><PageHead title="Transfer stock" />
      <div className="panel panel-pad stack"><p>You need at least two places to move stock between.</p>
        <Can2 /></div></div>
  );

  if (done) return (
    <div className="form-panel"><PageHead title="Transfer stock" />
      <Done title={done.message} result={null} productId={p?.id} againLabel="Move something else" onAgain={() => {
        setDone(null); setDetail(null); setSerials([]); setF((x) => ({ ...x, quantity: "", unit_id: "", batch_id: "", notes: "" }));
      }} />
      <p style={{ marginTop: 12 }}><Link href={`/transfers/${done.transfer.id}`}>Open {done.transfer.number}</Link></p>
    </div>
  );

  return (
    <div className="form-panel">
      <PageHead title="Transfer stock" sub={mod("transfer_approval") ? "A manager approves the move before stock leaves." : "Move stock between locations in one step."}>
        <Link href="/transfers/new" className="btn small">Several products</Link>
      </PageHead>
      <form className="panel panel-pad stack" onSubmit={async (e) => {
        e.preventDefault();
        if (!p) return setErr("Choose a product first.");
        setBusy(true); setErr(null);
        try {
          setDone(await api("/inventory/transfer", { body: { from_location_id: f.from, to_location_id: f.to, product_id: p.id,
            quantity: p.track_serials ? serials.length : f.quantity, unit_id: f.unit_id || undefined, batch_id: f.batch_id || undefined,
            serials: p.track_serials ? serials : undefined, notes: f.notes || undefined } }));
        } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
      }}>
        {err && <Notice tone="bad">{err}</Notice>}
        <ProductPicker value={detail} onChange={(d) => { setDetail(d); setSerials([]); }} autoFocus />
        <div className="grid two">
          <LocationSelect label="From" value={f.from} onChange={(v) => { set("from", v); if (v === f.to) set("to", ""); setSerials([]); }} />
          <LocationSelect label="To" value={f.to} exclude={f.from} onChange={(v) => set("to", v)} />
        </div>
        <AvailableLine detail={detail} locationId={f.from} />
        {p?.track_serials ? <SerialPicker detail={detail!} locationId={f.from} value={serials} onChange={setSerials} need={serials.length} /> : (
          <div className="grid two">
            <Field label="How many?" htmlFor="q"><input id="q" className="input num" inputMode="decimal" required value={f.quantity} onChange={(e) => set("quantity", e.target.value)} /></Field>
            {detail && <UnitChoice detail={detail} value={f.unit_id} onChange={(v) => set("unit_id", v)} />}
          </div>
        )}
        {p?.track_batches && !p.track_serials && <BatchSelect detail={detail!} locationId={f.from} value={f.batch_id} onChange={(v) => set("batch_id", v)} />}
        <Field label="Note" htmlFor="n"><input id="n" className="input" value={f.notes} onChange={(e) => set("notes", e.target.value)} /></Field>
        <button className="btn go block" disabled={busy || !p || !f.to}>{busy ? "Saving…" : mod("transfer_approval") ? "Request transfer" : "Move stock"}</button>
      </form>
    </div>
  );
}

function Can2() {
  const { can } = useAuth();
  return can("locations.manage") ? <Link className="btn primary" href="/warehouses">Add a location</Link> : <p className="meta">Ask a manager to add one.</p>;
}
