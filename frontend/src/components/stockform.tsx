"use client";
import Link from "next/link";
import { useEffect, useId } from "react";
import { api } from "@/lib/api";
import { qty } from "@/lib/format";
import type { ProductDetail } from "@/lib/types";
import { MovementTable } from "./movements";
import { Qty } from "./ui";

export function availableAt(d: ProductDetail | null, locationId: string) {
  return d?.locations.find((l) => l.location_id === locationId) || null;
}

export function AvailableLine({ detail, locationId }: { detail: ProductDetail | null; locationId: string }) {
  if (!detail) return null;
  const here = availableAt(detail, locationId);
  return (
    <div className="panel panel-pad spread" style={{ background: "#fafbfc" }}>
      <span className="muted">Here now</span>
      <Qty v={here?.quantity || 0} unit={detail.product.base_unit.abbreviation} />
    </div>
  );
}

export function SerialPicker({ detail, locationId, value, onChange, need }: {
  detail: ProductDetail; locationId: string; value: string[]; onChange: (v: string[]) => void; need: number;
}) {
  const here = detail.serials_in_stock.filter((s) => s.location_id === locationId);
  return (
    <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend className="label">Which ones? <span className="meta">({value.length} of {need || "?"} chosen)</span></legend>
      {here.length === 0 ? <p className="meta">No serial numbers in stock here.</p> : (
        <div className="grid two" style={{ gap: 6 }}>
          {here.map((s) => (
            <label key={s.id} className="check">
              <input type="checkbox" checked={value.includes(s.serial)}
                     onChange={(e) => onChange(e.target.checked ? [...value, s.serial] : value.filter((x) => x !== s.serial))} />
              <span className="num">{s.serial}</span>
            </label>
          ))}
        </div>
      )}
    </fieldset>
  );
}

export function SerialEntry({ value, onChange, need }: { value: string; onChange: (v: string) => void; need: number }) {
  const id = useId();
  const count = value.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean).length;
  return (
    <div className="field">
      <label htmlFor={id}>Serial numbers <span className="meta">({count} of {need || "?"})</span></label>
      <textarea id={id} className="input num" rows={Math.min(Math.max(need, 3), 10)} value={value} onChange={(e) => onChange(e.target.value)}
                placeholder="One per line — scan them one after another" />
    </div>
  );
}

export const parseSerials = (s: string) => s.split(/[\n,]+/).map((x) => x.trim()).filter(Boolean);

export function BatchSelect({ detail, locationId, value, onChange, allowAuto = true }: {
  detail: ProductDetail; locationId: string; value: string; onChange: (v: string) => void; allowAuto?: boolean;
}) {
  const id = useId();
  const batches = availableAt(detail, locationId)?.batches.filter((b) => Number(b.quantity) > 0) || [];
  return (
    <div className="field">
      <label htmlFor={id}>Batch</label>
      <select id={id} className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        {allowAuto && <option value="">Earliest expiry first (recommended)</option>}
        {!allowAuto && <option value="">Choose a batch</option>}
        {batches.map((b) => <option key={b.batch_id} value={b.batch_id}>{b.batch_number} · {qty(b.quantity)} left{b.expires_on ? ` · exp ${b.expires_on}` : ""}</option>)}
      </select>
    </div>
  );
}

export function Done({ title, onAgain, againLabel, result, productId }: {
  title: string; onAgain: () => void; againLabel: string; result: { on_hand?: string; movements?: any[] } | null; productId?: string;
}) {
  return (
    <div className="stack">
      <div className="notice ok" role="status"><b>{title}</b>{result?.on_hand !== undefined && <> · {qty(result.on_hand)} now in stock in total.</>}</div>
      {result?.movements?.length ? <div className="panel"><MovementTable rows={result.movements} showProduct={false} compact /></div> : null}
      <div className="row">
        <button className="btn go" onClick={onAgain} autoFocus>{againLabel}</button>
        {productId && <Link className="btn" href={`/products/${productId}`}>View product</Link>}
        <Link className="btn" href="/">Dashboard</Link>
      </div>
    </div>
  );
}

/** Quick-action pages open pre-filled when linked as /receive?product=<id>. */
export function usePreselect(setDetail: (d: ProductDetail) => void) {
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("product");
    if (id) api<ProductDetail>(`/products/${id}`).then(setDetail).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
