"use client";
import Link from "next/link";
import { useState } from "react";
import { LocationSelect, ProductPicker } from "@/components/pickers";
import { SerialEntry, parseSerials } from "@/components/stockform";
import { Field, Notice, NoAccess, PageHead, Qty } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";
import type { ProductDetail } from "@/lib/types";

type Line = { detail: ProductDetail; location_id: string; location_label: string; quantity: string; unit_cost: string; batch_number: string; expires_on: string; serials: string };

/** Guided entry of the stock a business already has on day one. */
export default function OpeningStock() {
  const { can } = useAuth();
  const [lines, setLines] = useState<Line[]>([]);
  const [cur, setCur] = useState<ProductDetail | null>(null);
  const [f, setF] = useState({ location_id: "", quantity: "", unit_cost: "", batch_number: "", expires_on: "", serials: "" });
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!can("inventory.adjust")) return <NoAccess />;
  const p = cur?.product;

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    if (!cur) return;
    const label = (document.querySelector("#ol option:checked") as HTMLOptionElement | null)?.text || "";
    setLines((ls) => [...ls, { detail: cur, location_label: label, ...f, quantity: p?.track_serials ? String(parseSerials(f.serials).length) : f.quantity }]);
    setCur(null);
    setF((x) => ({ ...x, quantity: "", unit_cost: "", batch_number: "", expires_on: "", serials: "" }));
  };

  if (done) return (
    <div className="form-panel"><PageHead title="Opening stock" />
      <Notice tone="ok">{done}</Notice>
      <div className="row" style={{ marginTop: 12 }}><Link className="btn primary" href="/inventory">See your stock</Link><button className="btn" onClick={() => { setDone(null); setLines([]); }}>Enter more</button></div>
    </div>
  );

  return (
    <div className="form-panel" style={{ maxWidth: 820 }}>
      <PageHead title="Opening stock" sub="Count what you have today, add it line by line, then save once. Each line becomes an opening-stock entry in the history.">
        <Link className="btn" href="/imports?type=opening-stock">Import from Excel instead</Link>
      </PageHead>
      <div className="stack">
        {err && <Notice tone="bad">{err}</Notice>}
        <form className="panel panel-pad stack" onSubmit={add}>
          <ProductPicker value={cur} onChange={setCur} autoFocus />
          {p && (
            <>
              <div className="grid three">
                <LocationSelect id="ol" value={f.location_id} onChange={(v) => setF({ ...f, location_id: v })} />
                {!p.track_serials && <Field label={`Quantity (${p.base_unit.abbreviation})`} htmlFor="oq"><input id="oq" className="input num" inputMode="decimal" required value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} /></Field>}
                <Field label="Cost per unit" htmlFor="oc"><input id="oc" className="input num" inputMode="decimal" value={f.unit_cost} placeholder={p.cost_price} onChange={(e) => setF({ ...f, unit_cost: e.target.value })} /></Field>
              </div>
              {p.track_batches && <div className="grid two">
                <Field label="Batch number" htmlFor="ob"><input id="ob" className="input" required value={f.batch_number} onChange={(e) => setF({ ...f, batch_number: e.target.value })} /></Field>
                <Field label="Expiry date" htmlFor="oe"><input id="oe" type="date" className="input" required={p.track_expiry} value={f.expires_on} onChange={(e) => setF({ ...f, expires_on: e.target.value })} /></Field>
              </div>}
              {p.track_serials && <SerialEntry value={f.serials} onChange={(v) => setF({ ...f, serials: v })} need={parseSerials(f.serials).length} />}
              <div><button className="btn">Add line</button></div>
            </>
          )}
        </form>
        {lines.length > 0 && (
          <div className="panel">
            <table className="t"><thead><tr><th>Product</th><th>Where</th><th className="hide-mobile">Batch</th><th className="r">Quantity</th><th /></tr></thead>
              <tbody>{lines.map((l, i) => (
                <tr key={i}><td>{l.detail.product.name}</td><td>{l.location_label}</td><td className="hide-mobile">{l.batch_number || "—"}</td>
                  <td className="r"><Qty v={l.quantity} unit={l.detail.product.base_unit.abbreviation} /></td>
                  <td className="r"><button className="btn small" onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}>Remove</button></td></tr>))}</tbody></table>
            <div className="pager"><span>{lines.length} line{lines.length > 1 ? "s" : ""}</span>
              <button className="btn go" disabled={busy} onClick={async () => {
                setBusy(true); setErr(null);
                try {
                  const r = await api("/inventory/opening-stock", { body: { lines: lines.map((l) => ({
                    product_id: l.detail.product.id, location_id: l.location_id, quantity: l.quantity, unit_cost: l.unit_cost || l.detail.product.cost_price,
                    batch_number: l.batch_number || undefined, expires_on: l.expires_on || undefined,
                    serials: l.detail.product.track_serials ? parseSerials(l.serials) : undefined })) } });
                  setDone(r.message);
                } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
              }}>Save opening stock</button></div>
          </div>
        )}
      </div>
    </div>
  );
}
