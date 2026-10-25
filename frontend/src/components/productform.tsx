"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Page, Partner, Product, Ref, Unit } from "@/lib/types";
import { LocationSelect } from "./pickers";
import { Field, Notice } from "./ui";

type Mode = { kind: "create" } | { kind: "edit"; product: Product; hasHistory: boolean };

export function ProductForm({ mode, onSaved }: { mode: Mode; onSaved: (p: Product) => void }) {
  const { mod, can } = useAuth();
  const p = mode.kind === "edit" ? mode.product : null;
  const { data: units } = useApi<Unit[]>("/units");
  const { data: cats, reload: reloadCats } = useApi<Ref[]>("/categories");
  const { data: sups } = useApi<Page<Partner>>("/suppliers", { page_size: 200 });
  const [f, setF] = useState({
    name: p?.name || "", sku: p?.sku || "", barcode: p?.barcode || "", description: p?.description || "",
    category_id: p?.category?.id || "", brand: p?.brand || "", base_unit_id: p?.base_unit.id || "",
    cost_price: p?.cost_price ?? "", selling_price: p?.selling_price ?? "", tax_rate: p?.tax_rate ?? "",
    reorder_level: p?.reorder_level ?? "", reorder_qty: p?.reorder_qty ?? "", min_stock: p?.min_stock ?? "",
    preferred_supplier_id: p?.preferred_supplier?.id || "", image_url: p?.image_url || "",
    track_expiry: p ? p.track_expiry : !!mod("expiry"), track_batches: p ? p.track_batches : !!(mod("batches") && mod("expiry")),
    track_serials: p?.track_serials || false, is_active: p ? p.is_active : true,
    opening_quantity: "", opening_location_id: "",
  });
  const [newCat, setNewCat] = useState("");
  const [withVariants, setWithVariants] = useState(false);
  const [opts, setOpts] = useState<{ name: string; values: string }[]>([{ name: "Size", values: "" }, { name: "Colour", values: "" }]);
  const [cascade, setCascade] = useState(true);
  const isGroup = mode.kind === "edit" && mode.product.has_variants;
  const optionMap = () => Object.fromEntries(opts.filter((o) => o.name.trim() && o.values.trim())
    .map((o) => [o.name.trim(), o.values.split(",").map((v) => v.trim()).filter(Boolean)]));
  const combos = Object.values(optionMap()).reduce((n, v) => n * v.length, 1);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const lockTracking = mode.kind === "edit" && mode.hasHistory;
  const num = (v: string) => (v === "" ? undefined : v);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      let category_id = f.category_id;
      if (category_id === "__new" && newCat.trim()) {
        category_id = (await api<Ref>("/categories", { body: { name: newCat.trim() } })).id;
        reloadCats();
      }
      const body: Record<string, unknown> = {
        name: f.name, sku: f.sku, barcode: f.barcode || null, description: f.description || null,
        category_id: category_id && category_id !== "__new" ? category_id : null, brand: f.brand || null,
        cost_price: num(String(f.cost_price)) ?? 0, selling_price: num(String(f.selling_price)) ?? 0, tax_rate: num(String(f.tax_rate)) ?? 0,
        reorder_level: num(String(f.reorder_level)) ?? 0, reorder_qty: num(String(f.reorder_qty)) ?? 0, min_stock: num(String(f.min_stock)) ?? 0,
        preferred_supplier_id: f.preferred_supplier_id || null, image_url: f.image_url || null,
        track_expiry: f.track_expiry, track_batches: f.track_batches || f.track_expiry, track_serials: f.track_serials, is_active: f.is_active,
      };
      let saved: Product;
      if (mode.kind === "create") {
        body.base_unit_id = f.base_unit_id || undefined;
        if (withVariants) {
          const m = optionMap();
          if (!Object.keys(m).length) throw new Error("Type the sizes/colours, separated by commas");
          body.variant_options = m;
        }
        if (f.opening_quantity && !f.track_batches && !f.track_expiry && !f.track_serials) {
          body.opening_quantity = f.opening_quantity;
          body.opening_location_id = f.opening_location_id;
        }
        saved = await api<Product>("/products", { body });
      } else {
        if (isGroup) body.apply_to_variants = cascade;
        saved = await api<Product>(`/products/${mode.product.id}`, { method: "PUT", body });
      }
      onSaved(saved);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };

  const simpleTracking = !f.track_batches && !f.track_expiry && !f.track_serials && !withVariants;
  return (
    <form className="stack" onSubmit={submit} id="product-form">
      {err && <Notice tone="bad">{err}</Notice>}
      <Field label="Name" htmlFor="pn"><input id="pn" className="input" required value={f.name} onChange={(e) => set("name", e.target.value)} autoFocus={mode.kind === "create"} /></Field>
      <div className="grid two">
        <Field label="SKU / item code" htmlFor="sku" hint="Your own code for this item. Must be unique."><input id="sku" className="input" required value={f.sku} onChange={(e) => set("sku", e.target.value)} /></Field>
        <Field label="Barcode" htmlFor="bc" hint="Scan it into this box"><input id="bc" className="input num" value={f.barcode} onChange={(e) => set("barcode", e.target.value)} /></Field>
      </div>
      <div className="grid two">
        <Field label="Counted in" htmlFor="u" hint={mode.kind === "edit" ? "The base unit can’t change after creation. Add cartons, packs etc. on the product page." : "The smallest unit you count, e.g. pieces or kg."}>
          <select id="u" className="input" disabled={mode.kind === "edit"} value={f.base_unit_id || (units?.find((u) => u.abbreviation === "pcs")?.id ?? "")} onChange={(e) => set("base_unit_id", e.target.value)}>
            {(units || []).map((u) => <option key={u.id} value={u.id}>{u.name} ({u.abbreviation})</option>)}
          </select>
        </Field>
        <Field label="Category" htmlFor="cat">
          <select id="cat" className="input" value={f.category_id} onChange={(e) => set("category_id", e.target.value)}>
            <option value="">None</option>{(cats || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            {can("products.create") && <option value="__new">+ New category…</option>}
          </select>
          {f.category_id === "__new" && <input className="input" placeholder="New category name" aria-label="New category name" value={newCat} onChange={(e) => setNewCat(e.target.value)} />}
        </Field>
      </div>
      <div className="grid three">
        <Field label="Cost price" htmlFor="cp"><input id="cp" className="input num" inputMode="decimal" value={f.cost_price} onChange={(e) => set("cost_price", e.target.value)} /></Field>
        <Field label="Selling price" htmlFor="sp"><input id="sp" className="input num" inputMode="decimal" value={f.selling_price} onChange={(e) => set("selling_price", e.target.value)} /></Field>
        <Field label="Tax %" htmlFor="tx"><input id="tx" className="input num" inputMode="decimal" value={f.tax_rate} onChange={(e) => set("tax_rate", e.target.value)} /></Field>
      </div>

      {mode.kind === "create" && mod("variants") && (
        <fieldset className="panel panel-pad stack" style={{ margin: 0 }}>
          <legend className="label" style={{ padding: "0 6px" }}>Sizes and colours</legend>
          <label className="check"><input type="checkbox" checked={withVariants} onChange={(e) => setWithVariants(e.target.checked)} />
            <span>This product comes in different sizes, colours or styles<span className="sub meta">Each one gets its own SKU, barcode and stock count.</span></span></label>
          {withVariants && <>
            {opts.map((o, i) => (
              <div key={i} className="grid two" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,2.4fr)" }}>
                <Field label="Option" htmlFor={`on${i}`}><input id={`on${i}`} className="input" value={o.name} onChange={(e) => setOpts(opts.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} /></Field>
                <Field label="Values, separated by commas" htmlFor={`ov${i}`}><input id={`ov${i}`} className="input" placeholder={i === 0 ? "S, M, L, XL" : i === 1 ? "Indigo, Rust, Black" : ""}
                  value={o.values} onChange={(e) => setOpts(opts.map((x, j) => (j === i ? { ...x, values: e.target.value } : x)))} /></Field>
              </div>))}
            <div className="spread">
              {opts.length < 3 ? <button type="button" className="btn small" onClick={() => setOpts([...opts, { name: "", values: "" }])}>Add another option</button> : <span />}
              <span className="meta">{Object.keys(optionMap()).length ? `${combos} variant${combos === 1 ? "" : "s"} will be created` : "Leave an option empty to skip it"}</span>
            </div>
          </>}
        </fieldset>
      )}
      {isGroup && (
        <label className="check panel panel-pad"><input type="checkbox" checked={cascade} onChange={(e) => setCascade(e.target.checked)} />
          <span>Apply price, category and reorder changes to all {mode.product.variant_count ?? ""} sizes/colours</span></label>
      )}

      <fieldset className="panel panel-pad stack" style={{ margin: 0 }}>
        <legend className="label" style={{ padding: "0 6px" }}>Reordering</legend>
        <div className="grid two">
          <Field label="Reorder when stock falls to" htmlFor="rl" hint="We’ll warn you at this level"><input id="rl" className="input num" inputMode="decimal" value={f.reorder_level} onChange={(e) => set("reorder_level", e.target.value)} /></Field>
          <Field label="Usually order" htmlFor="rq"><input id="rq" className="input num" inputMode="decimal" value={f.reorder_qty} onChange={(e) => set("reorder_qty", e.target.value)} /></Field>
        </div>
        <Field label="Preferred supplier" htmlFor="ps">
          <select id="ps" className="input" value={f.preferred_supplier_id} onChange={(e) => set("preferred_supplier_id", e.target.value)}>
            <option value="">None</option>{(sups?.items || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
      </fieldset>

      <fieldset className="panel panel-pad stack" style={{ margin: 0 }} disabled={lockTracking}>
        <legend className="label" style={{ padding: "0 6px" }}>Tracking</legend>
        {lockTracking && <p className="meta">Tracking can’t change once the product has stock history.</p>}
        <label className="check"><input type="checkbox" checked={f.track_expiry} onChange={(e) => { set("track_expiry", e.target.checked); if (e.target.checked) set("track_batches", true); }} />
          <span>Expiry dates<span className="sub meta">Medicines, food, chemicals. Oldest stock goes out first.</span></span></label>
        <label className="check"><input type="checkbox" checked={f.track_batches || f.track_expiry} disabled={f.track_expiry} onChange={(e) => set("track_batches", e.target.checked)} />
          <span>Batch / lot numbers</span></label>
        <label className="check"><input type="checkbox" checked={f.track_serials} onChange={(e) => set("track_serials", e.target.checked)} />
          <span>Serial numbers<span className="sub meta">Each unit is tracked on its own: laptops, phones, tools, equipment.</span></span></label>
      </fieldset>

      {mode.kind === "create" && simpleTracking && can("inventory.receive") && (
        <fieldset className="panel panel-pad stack" style={{ margin: 0 }}>
          <legend className="label" style={{ padding: "0 6px" }}>Stock you already have (optional)</legend>
          <div className="grid two">
            <Field label="Quantity on hand" htmlFor="oq"><input id="oq" className="input num" inputMode="decimal" value={f.opening_quantity} onChange={(e) => set("opening_quantity", e.target.value)} /></Field>
            <LocationSelect label="Where" value={f.opening_location_id} onChange={(v) => set("opening_location_id", v)} />
          </div>
        </fieldset>
      )}

      <details>
        <summary className="label" style={{ cursor: "pointer" }}>More (brand, description, image, minimum stock)</summary>
        <div className="stack" style={{ marginTop: 14 }}>
          <div className="grid two">
            <Field label="Brand" htmlFor="br"><input id="br" className="input" value={f.brand} onChange={(e) => set("brand", e.target.value)} /></Field>
            <Field label="Minimum stock" htmlFor="ms" hint="Never go below this"><input id="ms" className="input num" inputMode="decimal" value={f.min_stock} onChange={(e) => set("min_stock", e.target.value)} /></Field>
          </div>
          <Field label="Image address" htmlFor="img"><input id="img" className="input" type="url" placeholder="https://…" value={f.image_url} onChange={(e) => set("image_url", e.target.value)} /></Field>
          <Field label="Description" htmlFor="ds"><textarea id="ds" className="input" value={f.description} onChange={(e) => set("description", e.target.value)} /></Field>
          {mode.kind === "edit" && <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => set("is_active", e.target.checked)} /><span>Active — inactive products can’t be received or sold</span></label>}
        </div>
      </details>
      <button className="btn primary block" disabled={busy}>{busy ? "Saving…" : mode.kind === "create" ? "Add product" : "Save changes"}</button>
    </form>
  );
}
