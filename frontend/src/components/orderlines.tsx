"use client";
import { useState } from "react";
import { money, qty } from "@/lib/format";
import type { ProductDetail } from "@/lib/types";
import { ProductPicker } from "./pickers";

/** One editable line on a purchase or sales order. Prices are per base unit. */
export type OrderLine = {
  product_id: string; name: string; sku: string; unit: string; on_hand: string;
  quantity: string; price: string; tax_rate: string; discount_percent: string;
};

export function lineFromDetail(d: ProductDetail, kind: "purchase" | "sale"): OrderLine {
  const p = d.product;
  return { product_id: p.id, name: p.name, sku: p.sku, unit: p.base_unit.abbreviation, on_hand: d.on_hand, quantity: "",
           price: kind === "purchase" ? p.cost_price : p.selling_price, tax_rate: p.tax_rate, discount_percent: "0" };
}

export function lineTotals(l: OrderLine, kind: "purchase" | "sale") {
  const gross = Number(l.quantity || 0) * Number(l.price || 0);
  const disc = kind === "sale" ? gross * Number(l.discount_percent || 0) / 100 : 0;
  const net = gross - disc;
  const tax = net * Number(l.tax_rate || 0) / 100;
  return { gross, disc, net, tax, total: net + tax };
}

export function LinesEditor({ kind, lines, onChange, currency }: {
  kind: "purchase" | "sale"; lines: OrderLine[]; onChange: (l: OrderLine[]) => void; currency?: string;
}) {
  const [picking, setPicking] = useState<ProductDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const set = (i: number, k: keyof OrderLine, v: string) => onChange(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)));
  const sum = lines.reduce((a, l) => {
    const t = lineTotals(l, kind);
    return { net: a.net + t.net, tax: a.tax + t.tax, disc: a.disc + t.disc, total: a.total + t.total };
  }, { net: 0, tax: 0, disc: 0, total: 0 });

  return (
    <div className="stack">
      {lines.length > 0 && (
        <div className="table-wrap"><table className="t">
          <thead><tr>
            <th>Product</th><th className="r">Qty</th>
            <th className="r">{kind === "purchase" ? "Cost each" : "Price each"}</th>
            {kind === "sale" && <th className="r">Discount %</th>}
            <th className="r hide-mobile">Tax %</th><th className="r">Line total</th><th />
          </tr></thead>
          <tbody>{lines.map((l, i) => (
            <tr key={l.product_id}>
              <td>{l.name}<span className="sub">{l.sku}{l.on_hand !== "" ? ` · ${qty(l.on_hand)} ${l.unit} in stock` : ` · per ${l.unit}`}</span></td>
              <td className="r"><input className="input num" style={{ width: 90 }} inputMode="decimal" required aria-label={`Quantity of ${l.name}`}
                value={l.quantity} onChange={(e) => set(i, "quantity", e.target.value)} /></td>
              <td className="r"><input className="input num" style={{ width: 110 }} inputMode="decimal" aria-label={`Price of ${l.name}`}
                value={l.price} onChange={(e) => set(i, "price", e.target.value)} /></td>
              {kind === "sale" && <td className="r"><input className="input num" style={{ width: 80 }} inputMode="decimal" aria-label={`Discount on ${l.name}`}
                value={l.discount_percent} onChange={(e) => set(i, "discount_percent", e.target.value)} /></td>}
              <td className="r hide-mobile"><input className="input num" style={{ width: 70 }} inputMode="decimal" aria-label={`Tax on ${l.name}`}
                value={l.tax_rate} onChange={(e) => set(i, "tax_rate", e.target.value)} /></td>
              <td className="r num">{money(lineTotals(l, kind).total, currency)}</td>
              <td className="r"><button type="button" className="btn small" onClick={() => onChange(lines.filter((_, j) => j !== i))} aria-label={`Remove ${l.name}`}>Remove</button></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {err && <p className="meta" style={{ color: "var(--bad)" }}>{err}</p>}
      <ProductPicker label={lines.length ? "Add another product" : "Add a product"} value={picking} onChange={(d) => {
        setErr(null);
        if (d) {
          if (d.product.has_variants) setErr(`${d.product.name} comes in sizes/colours — pick the exact one.`);
          else if (lines.some((l) => l.product_id === d.product.id)) setErr(`${d.product.name} is already on the order.`);
          else onChange([...lines, lineFromDetail(d, kind)]);
        }
        setPicking(null);
      }} />
      {lines.length > 0 && (
        <div style={{ marginLeft: "auto", minWidth: 240 }} className="stack tight">
          {kind === "sale" && sum.disc > 0 && <div className="spread"><span className="muted">Discount</span><span className="num">−{money(sum.disc, currency)}</span></div>}
          <div className="spread"><span className="muted">Before tax</span><span className="num">{money(sum.net, currency)}</span></div>
          <div className="spread"><span className="muted">Tax</span><span className="num">{money(sum.tax, currency)}</span></div>
          <div className="spread" style={{ borderTop: "1px solid var(--rule)", paddingTop: 6 }}><b>Total</b><b className="num">{money(sum.total, currency)}</b></div>
        </div>
      )}
    </div>
  );
}

export function linesPayload(lines: OrderLine[], kind: "purchase" | "sale") {
  return lines.map((l) => kind === "purchase"
    ? { product_id: l.product_id, quantity: l.quantity, unit_cost: l.price || undefined, tax_rate: l.tax_rate || undefined }
    : { product_id: l.product_id, quantity: l.quantity, unit_price: l.price || undefined, tax_rate: l.tax_rate || undefined,
        discount_percent: l.discount_percent || 0 });
}

export const ORDER_STATUS: Record<string, { label: string; tone: string }> = {
  draft: { label: "Draft", tone: "" },
  confirmed: { label: "Confirmed", tone: "info" },
  partial: { label: "Partly done", tone: "warn" },
  completed: { label: "Completed", tone: "ok" },
  cancelled: { label: "Cancelled", tone: "out" },
};
export const PAYMENT_STATUS: Record<string, { label: string; tone: string }> = {
  unpaid: { label: "Unpaid", tone: "bad" },
  partial: { label: "Part paid", tone: "warn" },
  paid: { label: "Paid", tone: "ok" },
};
