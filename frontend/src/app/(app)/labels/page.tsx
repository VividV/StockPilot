"use client";
import { useEffect, useState } from "react";
import { I } from "@/components/icons";
import { ProductPicker } from "@/components/pickers";
import { Field, Notice, PageHead, useToast } from "@/components/ui";
import { api, download } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Product, ProductDetail } from "@/lib/types";

type Line = { product: Product; copies: number };

/** Print barcode labels for shelves and products. */
export default function Labels() {
  const { can } = useAuth();
  const toast = useToast();
  const { data: layouts } = useApi<{ key: string; title: string; per_page: number }[]>("/labels/layouts");
  const [lines, setLines] = useState<Line[]>([]);
  const [picking, setPicking] = useState<ProductDetail | null>(null);
  const [layout, setLayout] = useState("a4-3x8");
  const [price, setPrice] = useState(true);
  const [startAt, setStartAt] = useState(1);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const add = (d: ProductDetail) => {
    const items = d.product.has_variants ? d.variants.filter((v) => v.is_active) : [d.product];
    setLines((ls) => [...ls, ...items.filter((p) => !ls.some((l) => l.product.id === p.id))
      .map((p) => ({ product: p, copies: Math.max(1, Math.min(200, Math.round(Number(p.on_hand) || 1))) }))]);
  };
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("product");
    if (id) api<ProductDetail>(`/products/${id}`).then(add).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const total = lines.reduce((n, l) => n + l.copies, 0);
  const per = layouts?.find((l) => l.key === layout)?.per_page || 1;
  const missing = lines.filter((l) => !l.product.barcode).length;
  return (
    <div style={{ maxWidth: 900 }}>
      <PageHead title="Print labels" sub="Barcode labels for products and shelves. Scan them with a phone or any barcode scanner." />
      <div className="stack">
        {err && <Notice tone="bad">{err}</Notice>}
        <div className="panel panel-pad stack">
          <ProductPicker label="Add a product" allowGroups value={picking} onChange={(d) => { if (d) add(d); setPicking(null); }} />
          <p className="meta">Choosing a product with sizes/colours adds all of them. The number of labels starts at how many are in stock.</p>
          {lines.length > 0 && (
            <table className="t"><thead><tr><th>Product</th><th className="hide-mobile">Barcode</th><th className="r">Labels</th><th /></tr></thead>
              <tbody>{lines.map((l, i) => (
                <tr key={l.product.id}>
                  <td>{l.product.name}<span className="sub">{l.product.sku}</span></td>
                  <td className="hide-mobile num">{l.product.barcode || <span className="meta">{can("products.edit") ? "Will be created" : "Prints the SKU"}</span>}</td>
                  <td className="r"><input className="input num" style={{ width: 90 }} type="number" min={1} max={1000} aria-label={`Labels for ${l.product.name}`}
                    value={l.copies} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, copies: Math.max(1, Math.min(1000, Number(e.target.value) || 1)) } : x)))} /></td>
                  <td className="r"><button className="btn small" onClick={() => setLines(lines.filter((_, j) => j !== i))}>Remove</button></td>
                </tr>))}</tbody></table>
          )}
        </div>
        <div className="panel panel-pad stack">
          <div className="grid two">
            <Field label="Label paper" htmlFor="ly"><select id="ly" className="input" value={layout} onChange={(e) => setLayout(e.target.value)}>
              {(layouts || []).map((l) => <option key={l.key} value={l.key}>{l.title}</option>)}</select></Field>
            {per > 1 && <Field label="Start at label number" htmlFor="sa" hint="Reuse a sheet that already has some labels peeled off">
              <input id="sa" className="input num" type="number" min={1} max={per} value={startAt} onChange={(e) => setStartAt(Math.max(1, Math.min(per, Number(e.target.value) || 1)))} /></Field>}
          </div>
          <label className="check"><input type="checkbox" checked={price} onChange={(e) => setPrice(e.target.checked)} /><span>Show the selling price</span></label>
          {missing > 0 && can("products.edit") && <p className="meta">{missing} product{missing > 1 ? "s have" : " has"} no barcode yet; one will be created for {missing > 1 ? "them" : "it"} (a 13-digit in-store code starting with 2).</p>}
          <div className="spread">
            <span className="muted">{total} label{total === 1 ? "" : "s"}{per > 1 ? ` · about ${Math.ceil((total + startAt - 1) / per)} sheet${Math.ceil((total + startAt - 1) / per) === 1 ? "" : "s"}` : ""}</span>
            <button className="btn go" disabled={busy || !lines.length} onClick={async () => {
              setBusy(true); setErr(null);
              try {
                const res = await api<Response>("/labels", { body: { items: lines.map((l) => ({ product_id: l.product.id, copies: l.copies })), layout, show_price: price, start_at: startAt }, raw: true });
                const url = URL.createObjectURL(await res.blob());
                const a = document.createElement("a"); a.href = url; a.download = "stockpilot-labels.pdf"; document.body.appendChild(a); a.click(); a.remove();
                setTimeout(() => URL.revokeObjectURL(url), 5000);
                toast("Labels ready — open the PDF and print at 100% (actual size)");
              } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
            }}><I.download />Download labels (PDF)</button>
          </div>
        </div>
      </div>
    </div>
  );
}
