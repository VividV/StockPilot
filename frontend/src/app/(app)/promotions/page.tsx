"use client";
import { useEffect, useState } from "react";
import { ProductPicker } from "@/components/pickers";
import { Drawer, Empty, Field, Loading, NoAccess, Notice, PageHead, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, errMsg, money as fmtMoney, qty } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { ProductDetail } from "@/lib/types";

type Promo = { id: string; name: string; kind: "percent" | "amount"; value: number; scope: "all" | "category" | "products"; category_id: string | null;
  product_ids: string[]; min_qty: number; starts_on: string | null; ends_on: string | null; is_active: boolean; running: boolean };

export default function Promotions() {
  const { can, me } = useAuth();
  const { data, reload } = useApi<Promo[]>(can("promotions.manage") ? "/promotions" : null);
  const { data: cats } = useApi<{ id: string; name: string }[]>("/categories");
  const [edit, setEdit] = useState<Promo | "new" | null>(null);
  if (!can("promotions.manage")) return <NoAccess />;
  const cur = me!.organization.currency;
  const what = (p: Promo) => p.scope === "all" ? "Everything" : p.scope === "category" ? `Category: ${cats?.find((c) => c.id === p.category_id)?.name || "—"}` : `${p.product_ids.length} product${p.product_ids.length !== 1 ? "s" : ""}`;
  return (
    <>
      <PageHead title="Promotions" sub="Price cuts the till applies by itself. Cashiers don’t need discount permission for these.">
        <button className="act" onClick={() => setEdit("new")}>New promotion</button>
      </PageHead>
      {!data ? <Loading /> : data.length === 0 ? (
        <div className="panel"><Empty title="No promotions yet" text="For example: 10% off all clothing this week, or ₦200 off each drink when someone buys 6 or more." /></div>
      ) : (
        <div className="panel"><div className="table-wrap"><table className="t">
          <thead><tr><th>Promotion</th><th>Off</th><th className="hide-mobile">On</th><th className="hide-mobile">When</th><th /></tr></thead>
          <tbody>{data.map((p) => (
            <tr key={p.id} className="click" onClick={() => setEdit(p)}>
              <td><b>{p.name}</b>{Number(p.min_qty) > 1 && <span className="sub">when buying {qty(p.min_qty)} or more</span>}<span className="sub only-mobile">{what(p)}</span></td>
              <td className="num">{p.kind === "percent" ? `${qty(p.value)}%` : `${fmtMoney(p.value, cur)} each`}</td>
              <td className="hide-mobile">{what(p)}</td>
              <td className="hide-mobile">{p.starts_on || p.ends_on ? `${p.starts_on ? date(p.starts_on) : "Now"} – ${p.ends_on ? date(p.ends_on) : "no end"}` : "Always"}</td>
              <td>{p.running ? <span className="badge ok">Running</span> : <span className="badge out">{p.is_active ? "Not now" : "Off"}</span>}</td>
            </tr>))}</tbody>
        </table></div></div>
      )}
      {edit && <PromoForm p={edit === "new" ? null : edit} cats={cats || []} currency={cur} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
    </>
  );
}

function PromoForm({ p, cats, currency, onClose, onSaved }: { p: Promo | null; cats: { id: string; name: string }[]; currency: string; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({
    name: p?.name || "", kind: p?.kind || "percent", value: p ? String(Number(p.value)) : "", scope: p?.scope || "all",
    category_id: p?.category_id || "", min_qty: p ? String(Number(p.min_qty)) : "1", starts_on: p?.starts_on || "", ends_on: p?.ends_on || "",
    is_active: p?.is_active ?? true,
  });
  const [products, setProducts] = useState<{ id: string; name: string }[]>(p?.product_ids.map((id) => ({ id, name: "" })) || []);
  const [picker, setPicker] = useState<ProductDetail | null>(null);
  useEffect(() => {
    // show names for the products already on the promotion
    if (!p?.product_ids.length) return;
    Promise.all(p.product_ids.map((id) => api<ProductDetail>(`/products/${id}`).then((d) => ({ id, name: d.product.name })).catch(() => ({ id, name: "Product" }))))
      .then(setProducts);
  }, [p]);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: unknown) => setF({ ...f, [k]: v });
  const save = async () => {
    setErr(null);
    const body = { ...f, value: f.value, min_qty: f.min_qty || 1, category_id: f.scope === "category" ? f.category_id || null : null,
                   product_ids: f.scope === "products" ? products.map((x) => x.id) : [], starts_on: f.starts_on || null, ends_on: f.ends_on || null };
    try {
      await api(p ? `/promotions/${p.id}` : "/promotions", { method: p ? "PUT" : "POST", body });
      toast(p ? "Promotion saved" : "Promotion created");
      onSaved();
    } catch (e) { setErr(errMsg(e)); }
  };
  return (
    <Drawer title={p ? p.name : "New promotion"} open onClose={onClose} footer={<>
      {p && <button className="btn danger" style={{ marginRight: "auto" }} onClick={async () => { if (confirm("Delete this promotion?")) { await api(`/promotions/${p.id}`, { method: "DELETE" }); onSaved(); } }}>Delete</button>}
      <button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={save}>Save</button></>}>
      <div className="stack">
        {err && <Notice tone="bad">{err}</Notice>}
        <Field label="Name customers see on the receipt" htmlFor="pn"><input id="pn" className="input" value={f.name} placeholder="e.g. Easter week: 10% off" onChange={(e) => set("name", e.target.value)} /></Field>
        <div className="grid two">
          <Field label="Type" htmlFor="pk">
            <select id="pk" className="input" value={f.kind} onChange={(e) => set("kind", e.target.value)}>
              <option value="percent">Percentage off</option><option value="amount">Amount off each item</option>
            </select>
          </Field>
          <Field label={f.kind === "percent" ? "Percent off" : `${currency} off each`} htmlFor="pv"><input id="pv" className="input num" inputMode="decimal" value={f.value} onChange={(e) => set("value", e.target.value)} /></Field>
        </div>
        <Field label="Applies to" htmlFor="ps">
          <select id="ps" className="input" value={f.scope} onChange={(e) => set("scope", e.target.value)}>
            <option value="all">Everything</option><option value="category">One category</option><option value="products">Chosen products</option>
          </select>
        </Field>
        {f.scope === "category" && (
          <Field label="Category" htmlFor="pc">
            <select id="pc" className="input" value={f.category_id} onChange={(e) => set("category_id", e.target.value)}>
              <option value="">Choose…</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
        )}
        {f.scope === "products" && (
          <div className="stack tight">
            <ProductPicker value={picker} allowGroups label="Add a product (a product with sizes includes all its sizes)" onChange={(d) => {
              if (d && !products.some((x) => x.id === d.product.id)) setProducts([...products, { id: d.product.id, name: d.product.name }]);
              setPicker(null);
            }} />
            <div className="row">{products.map((x) => (
              <button key={x.id} type="button" className="chip" onClick={() => setProducts(products.filter((y) => y.id !== x.id))}>{x.name || "Product"} ✕</button>
            ))}</div>
          </div>
        )}
        <Field label="Only when buying at least" htmlFor="pm" hint="Leave at 1 for every sale. 3 means “buy 3 or more of it”.">
          <input id="pm" className="input num" inputMode="decimal" value={f.min_qty} onChange={(e) => set("min_qty", e.target.value)} />
        </Field>
        <div className="grid two">
          <Field label="Starts" htmlFor="pst"><input id="pst" type="date" className="input" value={f.starts_on} onChange={(e) => set("starts_on", e.target.value)} /></Field>
          <Field label="Ends" htmlFor="pe"><input id="pe" type="date" className="input" value={f.ends_on} onChange={(e) => set("ends_on", e.target.value)} /></Field>
        </div>
        <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => set("is_active", e.target.checked)} /><span>Switched on</span></label>
        <p className="meta">If two promotions fit the same item, the customer gets the bigger saving, never both.</p>
      </div>
    </Drawer>
  );
}
