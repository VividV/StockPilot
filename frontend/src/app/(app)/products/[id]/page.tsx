"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { Attachments } from "@/components/attachments";
import { I } from "@/components/icons";
import { MovementTable } from "@/components/movements";
import { ProductPicker } from "@/components/pickers";
import { ProductForm } from "@/components/productform";
import { Can, Drawer, Empty, ExpiryBadge, Field, Loading, Notice, Pager, Qty, StockBadge, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, errMsg, money, qty } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Movement, ProductDetail, Unit } from "@/lib/types";

const daysTo = (d: string | null) => (d ? Math.round((new Date(d + "T00:00:00").getTime() - new Date().setHours(0, 0, 0, 0)) / 86400000) : null);

export default function ProductPage() {
  const { id } = useParams<{ id: string }>();
  const { can, me, mod } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const { data: d, error, reload } = useApi<ProductDetail>(`/products/${id}`);
  const [page, setPage] = useState(1);
  const hist = useApi<{ items: Movement[]; total: number }>(can("inventory.view") ? `/products/${id}/movements` : null, { page, page_size: 20 });
  const [editing, setEditing] = useState(false);
  const [unitsOpen, setUnitsOpen] = useState(false);
  const [moreVariants, setMoreVariants] = useState(false);

  if (error) return <Notice tone="bad">{error}</Notice>;
  if (!d) return <Loading />;
  const p = d.product;
  const cur = me?.organization.currency;
  const u = p.base_unit.abbreviation;

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <p className="meta"><Link href="/products">Products</Link>{d.parent
            ? <> / <Link href={`/products/${d.parent.id}`}>{d.parent.name}</Link></>
            : p.category ? ` / ${p.category.name}` : ""}</p>
          <h1 style={{ marginTop: 4 }}>{p.name}</h1>
          <div className="row" style={{ marginTop: 8, gap: 8 }}>
            <span className="badge">SKU {p.sku}</span>{p.barcode && <span className="badge num">{p.barcode}</span>}
            {p.has_variants && <span className="badge info">{d.variants.length} sizes/colours</span>}
            <StockBadge status={p.stock_status} />{!p.is_active && <span className="badge bad">Inactive</span>}
            {p.track_expiry && <span className="badge info">Expiry tracked</span>}
            {p.track_batches && !p.track_expiry && <span className="badge info">Batches</span>}
            {p.track_serials && <span className="badge info">Serial numbers</span>}
          </div>
        </div>
        <div className="actions">
          {!p.has_variants && <>
            <Can perm="inventory.receive"><Link className="act" href={`/receive?product=${p.id}`}><I.receive />Receive</Link></Can>
            <Can perm="inventory.issue"><Link className="act" href={`/issue?product=${p.id}`}><I.issue />Issue</Link></Can>
            <Can perm="inventory.transfer"><Link className="act secondary" href={`/transfer?product=${p.id}`}><I.transfers />Transfer</Link></Can>
            <Can perm="inventory.adjust"><Link className="act secondary" href={`/adjust?product=${p.id}`}><I.adjust />Adjust</Link></Can>
          </>}
          <Link className="btn" href={`/labels?product=${p.id}`}><I.label />Labels</Link>
          {!p.has_variants && !p.barcode && <Can perm="products.edit"><button className="btn" onClick={async () => {
            try { await api("/products/barcodes/generate", { body: { product_ids: [p.id] } }); toast("Barcode created"); reload(); } catch (e) { toast(errMsg(e), "bad"); }
          }}>Create barcode</button></Can>}
          <Can perm="products.edit"><button className="btn" onClick={() => setEditing(true)}>Edit</button></Can>
        </div>
      </div>

      {p.has_variants && (
        <section className="panel">
          <div className="panel-head"><h2>Sizes and colours</h2>
            <Can perm="products.create"><button className="btn small" onClick={() => setMoreVariants(true)}>Add more</button></Can></div>
          <div className="table-wrap"><table className="t">
            <thead><tr>{Object.keys(p.variant_options).map((k) => <th key={k}>{k}</th>)}<th className="hide-mobile">SKU</th><th className="hide-mobile">Barcode</th><th className="r">In stock</th><th className="r hide-mobile">Price</th></tr></thead>
            <tbody>{d.variants.map((v) => (
              <tr key={v.id} className="click" onClick={() => router.push(`/products/${v.id}`)}>
                {Object.keys(p.variant_options).map((k) => <td key={k}>{v.variant_attributes[k]}</td>)}
                <td className="hide-mobile"><Link href={`/products/${v.id}`} onClick={(e) => e.stopPropagation()}>{v.sku}</Link>{!v.is_active && <span className="sub">inactive</span>}</td>
                <td className="hide-mobile num">{v.barcode || <span className="meta">—</span>}</td>
                <td className="r"><Qty v={v.on_hand} unit={u} /><span className="sub"><StockBadge status={v.stock_status} /></span></td>
                <td className="r num hide-mobile">{money(v.selling_price, cur)}</td>
              </tr>))}</tbody>
          </table></div>
        </section>
      )}

      <div className="grid two" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1.3fr)", alignItems: "start" }}>
        <section className="panel">
          <div className="panel-head"><h2>{p.has_variants ? "All sizes/colours together" : "In stock"}</h2></div>
          <div className="panel-pad">
            <div className="binlabel"><Qty v={d.on_hand} unit={u} /></div>
            {Number(p.reorder_level) > 0 && <p className="meta" style={{ marginTop: 6 }}>Reorder at {qty(p.reorder_level)} {u}{Number(p.reorder_qty) ? `, usually order ${qty(p.reorder_qty)}` : ""}{p.preferred_supplier ? ` from ${p.preferred_supplier.name}` : ""}</p>}
            <div style={{ marginTop: 14 }}>
              {p.has_variants ? <p className="meta">Open a size/colour above to receive, sell or move it.</p>
                : d.locations.length === 0 ? <p className="muted">Nothing in stock anywhere.</p> : d.locations.map((l) => (
                <div key={l.location_id} style={{ borderTop: "1px solid var(--rule)", padding: "10px 0" }}>
                  <div className="spread"><b>{l.location_name}</b><Qty v={l.quantity} unit={u} /></div>
                  {l.batches.map((b) => (
                    <div key={b.batch_id} className="spread meta" style={{ padding: "4px 0 0 12px" }}>
                      <span>Batch {b.batch_number}{b.expires_on ? ` · ${date(b.expires_on)}` : ""} {b.expires_on && <ExpiryBadge days={daysTo(b.expires_on)} />}</span>
                      <span className="num">{qty(b.quantity)}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
            {p.track_serials && d.serials_in_stock.length > 0 && (
              <details style={{ marginTop: 10 }}><summary className="label" style={{ cursor: "pointer" }}>{d.serials_in_stock.length} serial numbers in stock</summary>
                <p className="num meta" style={{ marginTop: 6 }}>{d.serials_in_stock.map((s) => s.serial).join(", ")}</p></details>
            )}
          </div>
        </section>

        <section className="panel">
          <div className="panel-head"><h2>Details</h2></div>
          <div className="panel-pad">
            <dl className="grid two" style={{ margin: 0, gap: 12 }}>
              <div><dt className="meta">Selling price</dt><dd style={{ margin: 0 }} className="num">{money(p.selling_price, cur)}</dd></div>
              <div><dt className="meta">Cost price</dt><dd style={{ margin: 0 }} className="num">{money(p.cost_price, cur)}</dd></div>
              <div><dt className="meta">Average cost (for valuation)</dt><dd style={{ margin: 0 }} className="num">{money(p.average_cost, cur)}</dd></div>
              <div><dt className="meta">Stock value</dt><dd style={{ margin: 0 }} className="num">{money(Number(d.on_hand) * Number(p.average_cost), cur)}</dd></div>
              <div><dt className="meta">Tax</dt><dd style={{ margin: 0 }}>{qty(p.tax_rate)}%</dd></div>
              <div><dt className="meta">Brand</dt><dd style={{ margin: 0 }}>{p.brand || "—"}</dd></div>
              <div><dt className="meta">Counted in</dt><dd style={{ margin: 0 }}>{p.base_unit.name}
                {d.conversions.map((c) => <span key={c.id} className="sub meta">1 {c.unit.abbreviation} = {qty(c.factor)} {u}</span>)}
                <Can perm="products.edit"><span className="sub"><button className="btn link" onClick={() => setUnitsOpen(true)}>Add cartons, packs…</button></span></Can></dd></div>
              <div><dt className="meta">Supplier</dt><dd style={{ margin: 0 }}>{p.preferred_supplier?.name || "—"}</dd></div>
            </dl>
            {p.description && <p className="muted" style={{ marginTop: 14 }}>{p.description}</p>}
          </div>
        </section>
      </div>

      {mod("production") && !p.has_variants && <Materials product={p} />}

      <Attachments entityType="product" entityId={p.id} title="Files and photos" />

      <section className="panel">
        <div className="panel-head"><h2>History</h2><span className="meta">Every change, who made it and when</span></div>
        {hist.data && hist.data.items.length === 0 ? <Empty title="No stock changes yet" /> : hist.data ? (
          <><MovementTable rows={hist.data.items} showProduct={false} /><Pager page={page} pageSize={20} total={hist.data.total} onPage={setPage} /></>
        ) : <Loading />}
      </section>

      <Drawer title={`Edit ${p.name}`} open={editing} onClose={() => setEditing(false)}>
        <ProductForm mode={{ kind: "edit", product: p, hasHistory: (hist.data?.total || 0) > 0 }} onSaved={() => { setEditing(false); toast("Saved"); reload(); }} />
        <Can perm="products.delete">
          <div className="divider" style={{ margin: "20px 0" }} />
          <button className="btn danger" onClick={async () => {
            if (!confirm(`Delete ${p.name}? Products with stock history are deactivated instead.`)) return;
            try { const r = await api("/products/" + p.id, { method: "DELETE" }); toast(r.message); router.push("/products"); } catch (e) { toast(errMsg(e), "bad"); }
          }}>Delete product</button>
        </Can>
      </Drawer>
      <UnitsDrawer open={unitsOpen} onClose={() => setUnitsOpen(false)} detail={d} onChange={reload} />
      {moreVariants && <MoreVariants detail={d} onClose={() => setMoreVariants(false)} onDone={(m) => { setMoreVariants(false); toast(m); reload(); }} />}
    </div>
  );
}

function UnitsDrawer({ open, onClose, detail, onChange }: { open: boolean; onClose: () => void; detail: ProductDetail; onChange: () => void }) {
  const { data: units } = useApi<Unit[]>(open ? "/units" : null);
  const [unit, setUnit] = useState("");
  const [factor, setFactor] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const base = detail.product.base_unit;
  return (
    <Drawer title="Pack sizes" open={open} onClose={onClose}>
      <div className="stack">
        <p className="muted">Receive and issue in bigger units. Stock is always kept in {base.name.toLowerCase()}s.</p>
        {detail.conversions.map((c) => (
          <div key={c.id} className="locline"><span>1 {c.unit.name} = <b>{qty(c.factor)}</b> {base.abbreviation}</span>
            <button className="btn small danger" onClick={async () => { await api(`/products/${detail.product.id}/conversions/${c.id}`, { method: "DELETE" }); onChange(); }}>Remove</button></div>
        ))}
        {err && <Notice tone="bad">{err}</Notice>}
        <form className="grid two" onSubmit={async (e) => {
          e.preventDefault(); setErr(null);
          try { await api(`/products/${detail.product.id}/conversions`, { body: { unit_id: unit, factor } }); setUnit(""); setFactor(""); onChange(); } catch (e) { setErr(errMsg(e)); }
        }}>
          <Field label="1 of this unit" htmlFor="cu"><select id="cu" className="input" required value={unit} onChange={(e) => setUnit(e.target.value)}>
            <option value="">Choose…</option>{(units || []).filter((x) => x.id !== base.id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
          <Field label={`equals how many ${base.abbreviation}?`} htmlFor="cf"><input id="cf" className="input num" inputMode="decimal" required value={factor} onChange={(e) => setFactor(e.target.value)} /></Field>
          <button className="btn primary">Add pack size</button>
        </form>
      </div>
    </Drawer>
  );
}

function MoreVariants({ detail, onClose, onDone }: { detail: ProductDetail; onClose: () => void; onDone: (msg: string) => void }) {
  const opts = detail.product.variant_options;
  const [vals, setVals] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  return (
    <Drawer title="Add sizes/colours" open onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" onClick={async () => {
        const options = Object.fromEntries(Object.keys(opts).map((k) => [k, [...opts[k], ...(vals[k] || "").split(",").map((v) => v.trim()).filter(Boolean)]]));
        try { const r = await api(`/products/${detail.product.id}/variants`, { body: { options } }); onDone(r.message); } catch (e) { setErr(errMsg(e)); }
      }}>Add</button></>}>
      <div className="stack">
        {err && <Notice tone="bad">{err}</Notice>}
        <p className="muted">Type new values to add. Every new combination is created; existing ones stay as they are.</p>
        {Object.entries(opts).map(([k, v]) => (
          <Field key={k} label={`New ${k.toLowerCase()} values`} htmlFor={`mv-${k}`} hint={`Already: ${v.join(", ")}`}>
            <input id={`mv-${k}`} className="input" placeholder="e.g. XL, XXL" value={vals[k] || ""} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
          </Field>))}
      </div>
    </Drawer>
  );
}

type Comp = { component_id: string; name: string; sku: string; unit: string; quantity: string; unit_cost: string };

function Materials({ product }: { product: ProductDetail["product"] }) {
  const { can, me } = useAuth();
  const toast = useToast();
  const { data, setData } = useApi<Comp[]>(`/products/${product.id}/components`);
  const [edit, setEdit] = useState<Comp[] | null>(null);
  const [picking, setPicking] = useState<ProductDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (!data) return null;
  const cost = data.reduce((n, c) => n + Number(c.quantity) * Number(c.unit_cost), 0);
  return (
    <section className="panel">
      <div className="panel-head"><h2>Made from</h2>
        <span className="row">{data.length > 0 && <span className="meta">≈ {money(cost, me?.organization.currency)} in materials each</span>}
          {can("products.edit") && !edit && <button className="btn small" onClick={() => setEdit(data)}>{data.length ? "Change" : "Add materials"}</button>}</span></div>
      {edit ? (
        <div className="panel-pad stack">
          {err && <Notice tone="bad">{err}</Notice>}
          {edit.map((c, i) => (
            <div key={c.component_id} className="spread">
              <span>{c.name}<span className="sub meta">{c.sku}</span></span>
              <span className="row" style={{ gap: 6 }}><input className="input num" style={{ width: 100 }} inputMode="decimal" aria-label={`Amount of ${c.name}`} value={c.quantity}
                onChange={(e) => setEdit(edit.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} /><span className="meta">{c.unit}</span>
                <button className="btn small" onClick={() => setEdit(edit.filter((_, j) => j !== i))}>Remove</button></span>
            </div>))}
          <ProductPicker label={`Add a material used to make one ${product.base_unit.name.toLowerCase()}`} value={picking} onChange={(d) => {
            if (d && d.product.id !== product.id && !edit.some((c) => c.component_id === d.product.id))
              setEdit([...edit, { component_id: d.product.id, name: d.product.name, sku: d.product.sku, unit: d.product.base_unit.abbreviation, quantity: "1", unit_cost: d.product.average_cost }]);
            setPicking(null);
          }} />
          <div className="row"><button className="btn primary" onClick={async () => {
            setErr(null);
            try { setData(await api(`/products/${product.id}/components`, { method: "PUT", body: { components: edit.map((c) => ({ component_id: c.component_id, quantity: c.quantity })) } })); setEdit(null); toast("Materials saved"); }
            catch (e) { setErr(errMsg(e)); }
          }}>Save materials</button><button className="btn" onClick={() => setEdit(null)}>Cancel</button></div>
        </div>
      ) : data.length === 0 ? <p className="panel-pad meta">List the materials used to make one {product.base_unit.name.toLowerCase()}, then record production from the Production page.</p> : (
        <div className="panel-pad">{data.map((c) => (
          <div key={c.component_id} className="locline"><Link href={`/products/${c.component_id}`}>{c.name}</Link><span className="num">{qty(c.quantity)} {c.unit}</span></div>))}</div>
      )}
    </section>
  );
}
