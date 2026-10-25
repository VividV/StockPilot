"use client";
import Link from "next/link";
import { useState } from "react";
import { MovementTable } from "@/components/movements";
import { LocationSelect, ProductPicker } from "@/components/pickers";
import { Empty, Field, Loading, Notice, NoAccess, PageHead, Pager, Qty, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, errMsg, money, qty } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { ProductDetail } from "@/lib/types";

type Avail = { max_quantity: string; estimated_unit_cost: string; lines: { component_id: string; name: string; unit: string; per_unit: string; needed: string | null; available: string; short: string | null }[] };

export default function Production() {
  const { can, mod, me } = useAuth();
  const toast = useToast();
  const [page, setPage] = useState(1);
  const runs = useApi<any>(can("inventory.view") ? "/production-runs" : null, { page, page_size: 20 });
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [loc, setLoc] = useState("");
  const [q, setQ] = useState("");
  const [batch, setBatch] = useState("");
  const [expires, setExpires] = useState("");
  const [notes, setNotes] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<any>(null);
  const avail = useApi<Avail>(detail && loc ? "/production/availability" : null, { product_id: detail?.product.id, location_id: loc, quantity: Number(q) > 0 ? q : undefined });
  if (!mod("production")) return <Empty title="Production is switched off" text="Turn it on in Settings → Business → Features to record making products from materials." action={can("settings.manage") ? <Link className="btn primary" href="/settings">Open settings</Link> : undefined} />;
  if (!can("production.run") && !can("inventory.view")) return <NoAccess />;
  const cur = me?.organization.currency;
  const p = detail?.product;
  const short = avail.data?.lines.some((l) => Number(l.short) > 0);

  return (
    <div className="stack" style={{ gap: 24, maxWidth: 1000 }}>
      <PageHead title="Production" sub="Record making products from materials. Materials go down, finished products go up, and the cost carries over." />
      {can("production.run") && (done ? (
        <div className="stack">
          <Notice tone="ok"><b>{done.message}</b> · cost {money(done.unit_cost, cur)} each</Notice>
          <div className="panel"><MovementTable rows={done.movements} compact /></div>
          <div className="row"><button className="btn go" onClick={() => { setDone(null); setQ(""); setBatch(""); setNotes(""); }}>Record more</button>
            <Link className="btn" href={`/products/${done.product_id}`}>View product</Link></div>
        </div>
      ) : (
        <form className="panel panel-pad stack" onSubmit={async (e) => {
          e.preventDefault(); if (!p) return;
          setBusy(true); setErr(null);
          try {
            const r = await api("/production-runs", { body: { product_id: p.id, location_id: loc, quantity: q, batch_number: batch || undefined, expires_on: expires || undefined, notes: notes || undefined } });
            setDone(r); toast(r.message); runs.reload();
          } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
        }}>
          <h2>Record production</h2>
          {err && <Notice tone="bad">{err}</Notice>}
          <ProductPicker label="What did you make?" value={detail} onChange={setDetail} />
          <div className="grid two">
            <Field label="How many?" htmlFor="pq"><input id="pq" className="input num" inputMode="decimal" required value={q} onChange={(e) => setQ(e.target.value)} /></Field>
            <LocationSelect label="Where (materials taken from and products put)" value={loc} onChange={setLoc} />
          </div>
          {p?.track_batches && <div className="grid two">
            <Field label="Batch number" htmlFor="pb" hint="Leave empty to use the production number"><input id="pb" className="input" value={batch} onChange={(e) => setBatch(e.target.value)} /></Field>
            {p.track_expiry && <Field label="Expiry date" htmlFor="pe"><input id="pe" type="date" className="input" required value={expires} onChange={(e) => setExpires(e.target.value)} /></Field>}
          </div>}
          {p && avail.data && (avail.data.lines.length === 0 ? (
            <Notice tone="warn">{p.name} has no materials yet. <Link href={`/products/${p.id}`}>Add them on the product page</Link>.</Notice>
          ) : (
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Material</th><th className="r">Per unit</th><th className="r">Needed</th><th className="r">In stock here</th></tr></thead>
              <tbody>{avail.data.lines.map((l) => (
                <tr key={l.component_id}><td>{l.name}</td><td className="r num">{qty(l.per_unit)} {l.unit}</td>
                  <td className="r">{l.needed !== null ? <Qty v={l.needed} unit={l.unit} /> : "—"}</td>
                  <td className="r"><span className={Number(l.short) > 0 ? "chg-out qty" : "qty"}>{qty(l.available)}</span>{Number(l.short) > 0 && <span className="sub" style={{ color: "var(--bad)" }}>{qty(l.short)} short</span>}</td></tr>))}</tbody>
            </table>
            <p className="meta" style={{ padding: "8px 14px" }}>You can make up to <b>{qty(avail.data.max_quantity)}</b> here · about {money(avail.data.estimated_unit_cost, cur)} each in materials</p></div>
          ))}
          <Field label="Note" htmlFor="pn" hint="e.g. waste, who made it"><input id="pn" className="input" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
          <button className="btn go block" disabled={busy || !p || !q || !!short || !avail.data?.lines.length}>{busy ? "Saving…" : "Record production"}</button>
        </form>
      ))}
      <section className="panel">
        <div className="panel-head"><h2>Recent production</h2></div>
        {!runs.data ? <Loading /> : runs.data.items.length === 0 ? <Empty title="Nothing made yet" /> : (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>No.</th><th>Product</th><th className="r">Made</th><th className="r hide-mobile">Cost each</th><th className="hide-mobile">Where</th><th>When</th></tr></thead>
              <tbody>{runs.data.items.map((r: any) => (
                <tr key={r.id}><td className="num">{r.number}</td><td><Link href={`/products/${r.product_id}`}>{r.product_name}</Link>{r.notes && <span className="sub">{r.notes}</span>}</td>
                  <td className="r"><Qty v={r.quantity} unit={r.unit} /></td><td className="r num hide-mobile">{money(r.unit_cost, cur)}</td>
                  <td className="hide-mobile">{r.location}</td><td>{dateTime(r.created_at)}</td></tr>))}</tbody>
            </table></div>
            <Pager page={page} pageSize={20} total={runs.data.total} onPage={setPage} />
          </>
        )}
      </section>
    </div>
  );
}
