"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { LocationSelect } from "@/components/pickers";
import { Empty, Loading, Notice, NoAccess, PageHead, Qty, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg, money } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type Sug = { product_id: string; name: string; sku: string; unit: string; on_hand: string; on_order: string; reorder_level: string;
  suggested_qty: string; unit_cost: string; supplier_id: string | null; supplier_name: string | null };

/** Everything at or below its reorder level, turned into draft orders in one click. */
export default function Reorder() {
  const { can, me } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const { data } = useApi<Sug[]>(can("purchases.view") ? "/purchase-orders/suggestions" : null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [loc, setLoc] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setPicked(new Set(data.filter((s) => s.supplier_id).map((s) => s.product_id))); }, [data]);
  if (!can("purchases.create")) return <NoAccess />;
  const cur = me?.organization.currency;
  const groups = new Map<string, Sug[]>();
  (data || []).forEach((s) => { const k = s.supplier_name || ""; groups.set(k, [...(groups.get(k) || []), s]); });

  return (
    <div style={{ maxWidth: 960 }}>
      <PageHead title="Reorder low stock" sub="Products at or below their reorder level. Stock already on order is counted, so nothing is ordered twice." />
      {err && <Notice tone="bad">{err}</Notice>}
      {!data ? <Loading /> : data.length === 0 ? (
        <div className="panel"><Empty title="Nothing needs reordering" text="Set a reorder level on a product and it will appear here when stock runs low." /></div>
      ) : (
        <div className="stack">
          {[...groups.entries()].map(([sup, items]) => (
            <section key={sup} className="panel">
              <div className="panel-head"><h2>{sup || "No preferred supplier"}</h2>
                {!sup && <span className="meta">Set a preferred supplier on these products to order them here</span>}</div>
              <div className="table-wrap"><table className="t">
                <thead><tr><th style={{ width: 40 }} /><th>Product</th><th className="r">In stock</th><th className="r hide-mobile">On order</th><th className="r hide-mobile">Reorder at</th><th className="r">Order</th><th className="r hide-mobile">Cost</th></tr></thead>
                <tbody>{items.map((s) => (
                  <tr key={s.product_id}>
                    <td><input type="checkbox" disabled={!s.supplier_id} aria-label={`Order ${s.name}`} checked={picked.has(s.product_id)} style={{ width: 18, height: 18 }}
                      onChange={(e) => { const n = new Set(picked); e.target.checked ? n.add(s.product_id) : n.delete(s.product_id); setPicked(n); }} /></td>
                    <td><Link href={`/products/${s.product_id}`}>{s.name}</Link><span className="sub">{s.sku}</span></td>
                    <td className="r"><Qty v={s.on_hand} unit={s.unit} /></td>
                    <td className="r num hide-mobile">{Number(s.on_order) || "—"}</td>
                    <td className="r num hide-mobile">{Number(s.reorder_level)}</td>
                    <td className="r"><Qty v={s.suggested_qty} unit={s.unit} /></td>
                    <td className="r num hide-mobile">{money(Number(s.suggested_qty) * Number(s.unit_cost), cur)}</td>
                  </tr>))}</tbody>
              </table></div>
            </section>
          ))}
          <div className="panel panel-pad row" style={{ alignItems: "flex-end" }}>
            <div style={{ flex: 1, minWidth: 220 }}><LocationSelect label="Deliver to" value={loc} onChange={setLoc} /></div>
            <button className="btn go" disabled={busy || picked.size === 0} onClick={async () => {
              setBusy(true); setErr(null);
              try {
                const r = await api("/purchase-orders/from-low-stock", { body: { destination_location_id: loc, product_ids: [...picked] } });
                toast(r.message);
                router.push(r.purchase_orders.length === 1 ? `/purchases/${r.purchase_orders[0].id}` : "/purchases?tab=draft");
              } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
            }}>Create draft orders ({picked.size})</button>
          </div>
          <p className="meta">One draft is made per supplier. Check the quantities and prices, then confirm each order.</p>
        </div>
      )}
    </div>
  );
}
