"use client";
import { useEffect, useId, useRef, useState } from "react";
import { api } from "@/lib/api";
import { qty } from "@/lib/format";
import { useApi, useDebounced } from "@/lib/hooks";
import type { LocationOption, Page, Partner, Product, ProductDetail } from "@/lib/types";
import { ScanButton } from "./camerascanner";
import { StockBadge } from "./ui";

/**
 * Find a product by typing a name, or by scanning: a USB barcode scanner types
 * the code and presses Enter, which we resolve with the lookup endpoint.
 */
export function ProductPicker({ value, onChange, autoFocus, label = "Product", allowGroups = false }: {
  value: ProductDetail | null; onChange: (p: ProductDetail | null) => void; autoFocus?: boolean; label?: string;
  /** true = a size/colour group itself may be chosen (e.g. for labels); default asks for the exact variant */
  allowGroups?: boolean;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const q = useDebounced(text, 200);
  const [items, setItems] = useState<Product[]>([]);
  const [group, setGroup] = useState<ProductDetail | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!q.trim()) { setItems([]); return; }
    let live = true;
    api<Page<Product>>("/products", { query: { q, page_size: 8 } }).then((r) => live && setItems(r.items)).catch(() => {});
    return () => { live = false; };
  }, [q]);

  const choose = async (code: string, byId = false) => {
    setErr(null);
    try {
      const d = byId ? await api<ProductDetail>(`/products/${code}`) : await api<ProductDetail>("/products/lookup", { query: { code } });
      setText("");
      setOpen(false);
      if (d.product.has_variants && !allowGroups) { setGroup(d); return; }  // ask which size/colour
      setGroup(null);
      onChange(d);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Not found");
    }
  };

  if (value) {
    const p = value.product;
    return (
      <div className="field">
        <span className="label">{label}</span>
        <div className="picked">
          <div>
            <div style={{ fontWeight: 650 }}>{p.name}</div>
            <div className="meta">SKU {p.sku} · {qty(value.on_hand)} {p.base_unit.abbreviation} in stock</div>
          </div>
          <button type="button" className="btn small" onClick={() => { onChange(null); setTimeout(() => inputRef.current?.focus(), 0); }}>Change</button>
        </div>
      </div>
    );
  }

  if (group) {
    return (
      <div className="field">
        <span className="label">{group.product.name}: which one?</span>
        <div className="row" style={{ gap: 6 }}>
          {group.variants.filter((v) => v.is_active).map((v) => (
            <button key={v.id} type="button" className="btn" onClick={() => choose(v.id, true)}>
              {Object.values(v.variant_attributes).join(" / ")}<span className="meta num" style={{ marginLeft: 6 }}>{qty(v.on_hand)}</span>
            </button>))}
          <button type="button" className="btn link" onClick={() => { setGroup(null); setTimeout(() => inputRef.current?.focus(), 0); }}>Choose another product</button>
        </div>
        {err ? <span className="hint" style={{ color: "var(--bad)" }}>{err}</span> : null}
      </div>
    );
  }

  return (
    <div className="field picker">
      <label htmlFor={id}>{label}</label>
      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
      <input
        style={{ flex: 1, minWidth: 0 }}
        id={id} ref={inputRef} className="input" autoFocus={autoFocus} autoComplete="off" value={text}
        placeholder="Type a name, or scan / type a barcode or SKU"
        role="combobox" aria-expanded={open && items.length > 0} aria-controls={`${id}-list`} aria-invalid={!!err}
        onChange={(e) => { setText(e.target.value); setOpen(true); setActive(0); setErr(null); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          if (e.key === "Enter") {
            e.preventDefault();
            const exact = text.trim();
            const match = items.find((i) => i.barcode === exact || i.sku.toLowerCase() === exact.toLowerCase());
            if (match) choose(match.id, true);
            else if (open && items[active] && q === text) choose(items[active].id, true);
            else if (exact) choose(exact);
          }
        }}
      />
      <ScanButton onCode={(c) => choose(c)} />
      </div>
      {err ? <span className="hint" style={{ color: "var(--bad)" }}>{err}</span> : null}
      {open && items.length > 0 ? (
        <ul className="picker-list" id={`${id}-list`} role="listbox">
          {items.map((p, i) => (
            <li key={p.id} role="option" aria-selected={i === active} onMouseDown={(e) => { e.preventDefault(); choose(p.id, true); }}>
              <div className="spread">
                <span><b>{p.name}</b><span className="sub meta">{p.has_variants ? `${p.variant_count ?? ""} sizes/colours` : `SKU ${p.sku}`}{p.barcode ? ` · ${p.barcode}` : ""}</span></span>
                <span className="row" style={{ gap: 8 }}><span className="num">{qty(p.on_hand)} {p.base_unit.abbreviation}</span><StockBadge status={p.stock_status} /></span>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

let locCache: LocationOption[] | null = null;
export function useLocations() {
  const r = useApi<LocationOption[]>("/locations");
  useEffect(() => { if (r.data) locCache = r.data; }, [r.data]);
  return { ...r, data: r.data || locCache };
}

export function LocationSelect({ value, onChange, label = "Location", exclude, id, hint }: {
  value: string; onChange: (v: string) => void; label?: string; exclude?: string; id?: string; hint?: string;
}) {
  const auto = useId();
  const { data } = useLocations();
  const opts = (data || []).filter((l) => l.id !== exclude);
  // A one-location business never has to choose; everyone else starts where they last worked.
  useEffect(() => {
    if (value || !opts.length) return;
    let last: string | null = null;
    try { last = localStorage.getItem(`sp:loc:${label}`); } catch { /* storage blocked */ }
    const rank = (o: LocationOption) => ["warehouse", "store", "branch", "production", "site", "technician", "vehicle"].indexOf(o.warehouse_type) + (o.kind === "default" ? 0 : 0.5);
    const pick = opts.find((o) => o.id === last) || [...opts].sort((a, b) => rank(a) - rank(b))[0];
    onChange(pick.id);
  }, [value, opts.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const choose = (v: string) => {
    try { localStorage.setItem(`sp:loc:${label}`, v); } catch { /* storage blocked */ }
    onChange(v);
  };
  return (
    <div className="field">
      <label htmlFor={id || auto}>{label}</label>
      <select id={id || auto} className="input" value={value} onChange={(e) => choose(e.target.value)}>
        {opts.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
      </select>
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

export function PartnerSelect({ kind, value, onChange, label, optionalText = "— None —" }: {
  kind: "suppliers" | "customers"; value: string; onChange: (v: string) => void; label: string; optionalText?: string;
}) {
  const id = useId();
  const { data } = useApi<Page<Partner>>(`/${kind}`, { page_size: 200 });
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{optionalText}</option>
        {(data?.items || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
    </div>
  );
}

export function UnitChoice({ detail, value, onChange }: { detail: ProductDetail; value: string; onChange: (v: string) => void }) {
  const id = useId();
  if (!detail.conversions.length) return null;
  const base = detail.product.base_unit;
  return (
    <div className="field">
      <label htmlFor={id}>Counted in</label>
      <select id={id} className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{base.name} ({base.abbreviation})</option>
        {detail.conversions.map((c) => <option key={c.id} value={c.unit.id}>{c.unit.name} — {qty(c.factor)} {base.abbreviation} each</option>)}
      </select>
    </div>
  );
}
