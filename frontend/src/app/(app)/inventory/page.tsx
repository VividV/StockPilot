"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { MovementTable } from "@/components/movements";
import { Empty, ExpiryBadge, Loading, Notice, NoAccess, PageHead, Pager, Qty } from "@/components/ui";
import { download } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { MOVEMENT_LABELS, date, errMsg, money } from "@/lib/format";
import { useApi, useDebounced } from "@/lib/hooks";
import type { Movement, Page, StockRow, Warehouse } from "@/lib/types";
import { useToast } from "@/components/ui";

const TABS = [
  { k: "stock", l: "Stock on hand" },
  { k: "movements", l: "History" },
  { k: "expiry", l: "Batches & expiry" },
  { k: "serials", l: "Serial numbers" },
];

function Inventory() {
  const { can, me, mod } = useAuth();
  const sp = useSearchParams();
  const router = useRouter();
  const tab = sp.get("tab") || "stock";
  const [q, setQ] = useState(sp.get("q") || "");
  const [wh, setWh] = useState(sp.get("warehouse_id") || "");
  const [type, setType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [expiring, setExpiring] = useState("");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  const toast = useToast();
  const { data: whs } = useApi<Warehouse[]>("/warehouses");
  const ok = can("inventory.view");
  const stock = useApi<Page<StockRow>>(ok && tab === "stock" ? "/stock" : null, { q: dq, warehouse_id: wh, page, page_size: 50 });
  const mv = useApi<Page<Movement>>(ok && tab === "movements" ? "/movements" : null, { q: dq, warehouse_id: wh, type, date_from: from, date_to: to, page, page_size: 50 });
  const batches = useApi<Page<any>>(ok && tab === "expiry" ? "/batches" : null, { q: dq, expiring_within: expiring, page, page_size: 50 });
  const serials = useApi<Page<any>>(ok && tab === "serials" ? "/serials" : null, { q: dq, page, page_size: 50 });
  if (!ok) return <NoAccess />;
  const tabs = TABS.filter((t) => (t.k !== "expiry" || mod("batches") || mod("expiry")) && (t.k !== "serials" || mod("serials")));
  const go = (k: string) => { setPage(1); router.replace(`/inventory?tab=${k}`); };
  const cur = me?.organization.currency;
  const exportRep = async (name: string, query: Record<string, unknown>) => {
    try { await download(`/reports/${name}`, { ...query, format: "xlsx" }); } catch (e) { toast(errMsg(e), "bad"); }
  };

  return (
    <>
      <PageHead title="Inventory" sub="What you have, where it is, and every change.">
        {can("reports.export") && tab === "stock" && <button className="btn small" onClick={() => exportRep("stock-on-hand", { warehouse_id: wh })}>Export</button>}
        {can("reports.export") && tab === "movements" && <button className="btn small" onClick={() => exportRep("movements", { warehouse_id: wh, type, date_from: from, date_to: to })}>Export</button>}
      </PageHead>
      <div className="tabs" role="tablist">
        {tabs.map((t) => <button key={t.k} role="tab" className="tab" aria-selected={tab === t.k} onClick={() => go(t.k)}>{t.l}</button>)}
      </div>
      <div className="row" style={{ marginBottom: 14 }}>
        <input className="input" style={{ maxWidth: 300 }} aria-label="Search" placeholder={tab === "movements" ? "Product, reference, customer…" : tab === "serials" ? "Serial or product" : "Product, SKU, batch"} value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        {(tab === "stock" || tab === "movements") && (whs?.length || 0) > 1 && (
          <select className="input" style={{ width: "auto" }} aria-label="Location" value={wh} onChange={(e) => { setWh(e.target.value); setPage(1); }}>
            <option value="">All locations</option>{whs!.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        )}
        {tab === "movements" && <>
          <select className="input" style={{ width: "auto" }} aria-label="Type" value={type} onChange={(e) => { setType(e.target.value); setPage(1); }}>
            <option value="">All changes</option>{Object.entries(MOVEMENT_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <input type="date" className="input" style={{ width: "auto" }} aria-label="From date" value={from} onChange={(e) => setFrom(e.target.value)} />
          <input type="date" className="input" style={{ width: "auto" }} aria-label="To date" value={to} onChange={(e) => setTo(e.target.value)} />
        </>}
        {tab === "expiry" && (
          <select className="input" style={{ width: "auto" }} aria-label="Expiry window" value={expiring} onChange={(e) => { setExpiring(e.target.value); setPage(1); }}>
            <option value="">All batches with stock</option><option value="0">Expired</option><option value="7">Expires within 7 days</option>
            <option value="30">Within 30 days</option><option value="90">Within 90 days</option>
          </select>
        )}
      </div>

      <div className="panel">
        {tab === "stock" && (stock.error ? <Notice tone="bad">{stock.error}</Notice> : !stock.data ? <Loading /> : stock.data.items.length === 0 ?
          <Empty title="No stock here yet" text="Receive stock and it will appear here." action={can("inventory.receive") ? <Link className="btn go" href="/receive">Receive stock</Link> : undefined} /> : (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Product</th><th>Location</th><th className="hide-mobile">Batch</th><th className="r">Quantity</th><th className="r hide-mobile">Value</th></tr></thead>
              <tbody>{stock.data.items.map((s) => (
                <tr key={`${s.product_id}${s.location_id}${s.batch_id}`} className="click" onClick={() => router.push(`/products/${s.product_id}`)}>
                  <td><b>{s.product_name}</b><span className="sub">{s.sku}</span></td>
                  <td>{s.location_name}<span className="sub only-mobile">{s.batch_number ? `Batch ${s.batch_number}` : ""}</span></td>
                  <td className="hide-mobile">{s.batch_number ? <>{s.batch_number}<span className="sub">{s.expires_on ? `exp ${date(s.expires_on)}` : ""}</span></> : <span className="meta">—</span>}</td>
                  <td className="r"><Qty v={s.quantity} unit={s.unit} /></td>
                  <td className="r num hide-mobile">{money(s.value, cur)}</td>
                </tr>))}</tbody>
            </table></div>
            <Pager page={page} pageSize={50} total={stock.data.total} onPage={setPage} />
          </>))}

        {tab === "movements" && (!mv.data ? <Loading /> : mv.data.items.length === 0 ? <Empty title="No changes match" /> : (
          <><MovementTable rows={mv.data.items} /><Pager page={page} pageSize={50} total={mv.data.total} onPage={setPage} /></>
        ))}

        {tab === "expiry" && (!batches.data ? <Loading /> : batches.data.items.length === 0 ? <Empty title="No batches match" /> : (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Expires</th><th>Product</th><th>Batch</th><th className="r">Quantity</th></tr></thead>
              <tbody>{batches.data.items.map((b: any) => (
                <tr key={b.id} className="click" onClick={() => router.push(`/products/${b.product_id}`)}>
                  <td><ExpiryBadge days={b.days_to_expiry} /><span className="sub">{date(b.expires_on)}</span></td>
                  <td><b>{b.product_name}</b><span className="sub">{b.sku}</span></td>
                  <td className="num">{b.batch_number}</td>
                  <td className="r"><Qty v={b.quantity} unit={b.unit} /></td>
                </tr>))}</tbody>
            </table></div>
            <Pager page={page} pageSize={50} total={batches.data.total} onPage={setPage} />
          </>))}

        {tab === "serials" && (!serials.data ? <Loading /> : serials.data.items.length === 0 ? <Empty title="No serial numbers yet" /> : (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Serial</th><th>Product</th><th>Status</th><th className="hide-mobile">Where</th></tr></thead>
              <tbody>{serials.data.items.map((s: any) => (
                <tr key={s.id} className="click" onClick={() => router.push(`/serials/${s.id}`)}>
                  <td className="num"><b>{s.serial}</b></td><td>{s.product_name}<span className="sub">{s.sku}</span></td>
                  <td><span className={`badge ${s.status === "in_stock" ? "ok" : s.status === "in_transit" ? "info" : "out"}`}>{s.status.replace("_", " ")}</span></td>
                  <td className="hide-mobile">{s.location || "—"}</td>
                </tr>))}</tbody>
            </table></div>
            <Pager page={page} pageSize={50} total={serials.data.total} onPage={setPage} />
          </>))}
      </div>
    </>
  );
}

export default function InventoryPage() {
  return <Suspense><Inventory /></Suspense>;
}
