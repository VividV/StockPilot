"use client";
import Link from "next/link";
import { MovementTable } from "@/components/movements";
import { I } from "@/components/icons";
import { QUICK } from "@/components/shell";
import { Empty, Loading, Notice, NoAccess, Qty } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { money, qty } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Movement } from "@/lib/types";

type Dash = {
  currency: string; inventory_value: string; total_units: string; product_count: number; low_stock_count: number;
  out_of_stock_count: number; expiring_count: number; expired_count: number; pending_transfers: number; period_days: number;
  received_units: string; issued_units: string;
  top_products: { product_id: string; name: string; sku: string; quantity: string }[];
  by_location: { warehouse_id: string; name: string; units: string; value: string }[];
  recent_movements: Movement[];
};

export default function Dashboard() {
  const { me, can, mod } = useAuth();
  const { data: d, error } = useApi<Dash>(can("inventory.view") ? "/dashboard" : null);
  const showToday = mod("pos") && can("reports.view");
  const { data: today } = useApi<{ totals: { net_sales: number; sales_count: number; profit: number }; open_shifts: unknown[] }>(showToday ? "/pos/summary" : null);
  if (!can("inventory.view")) return <NoAccess />;
  const quick = QUICK.filter((q) => can(q.perm) && (!q.module || mod(q.module)));
  const hello = new Date().getHours() < 12 ? "Good morning" : new Date().getHours() < 17 ? "Good afternoon" : "Good evening";

  return (
    <div className="stack" style={{ gap: 24 }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div><h1>{hello}, {me?.full_name.split(" ")[0]}</h1><p className="muted">{me?.organization.name}</p></div>
        <div className="actions hide-mobile">
          {quick.map((q, i) => <Link key={q.href} href={q.href} className={`act ${i > 2 ? "secondary" : ""}`}><q.icon />{q.label}</Link>)}
        </div>
      </div>

      {mod("pos") && can("pos.sell") && <Link href="/pos" className="act only-mobile-flex" style={{ justifyContent: "center", padding: 16, fontSize: 17 }}><I.till />Open the till</Link>}
      {showToday && today && (
        <Link href="/today" className="panel panel-pad spread today-strip">
          <span><span className="meta">Counter sales today</span><br /><b className="qty" style={{ fontSize: 24 }}>{money(today.totals.net_sales, me!.organization.currency)}</b></span>
          <span className="meta">{today.totals.sales_count} sale{today.totals.sales_count !== 1 ? "s" : ""} · profit {money(today.totals.profit, me!.organization.currency)} · {today.open_shifts.length} till{today.open_shifts.length !== 1 ? "s" : ""} open</span>
          <span className="btn small">See today</span>
        </Link>
      )}
      {error && <Notice tone="bad">{error}</Notice>}
      {!d ? <Loading /> : (
        <>
          {d.product_count === 0 ? (
            <div className="panel panel-pad stack">
              <h2>Let’s get your stock in</h2>
              <p className="muted">Three steps and you can see exactly what you have and where.</p>
              <ol className="stack tight" style={{ margin: 0, paddingLeft: 20 }}>
                <li><Link href="/products/new">Add your first product</Link>, or <Link href="/imports?type=products">import them all from Excel</Link></li>
                <li><Link href="/warehouses">Check your locations</Link> — you already have a Main Store</li>
                <li><Link href="/opening-stock">Enter the stock you have today</Link></li>
              </ol>
            </div>
          ) : null}

          <div className="stats">
            <div className="stat"><div className="k">Stock value</div><div className="v qty">{money(d.inventory_value, d.currency)}</div></div>
            <Link className="stat" href="/products"><div className="k">Products</div><div className="v qty">{qty(d.product_count)}</div></Link>
            <Link className={`stat ${d.low_stock_count ? "caution" : ""}`} href="/reports/low-stock"><div className="k">Need reordering</div><div className="v qty">{d.low_stock_count}</div></Link>
            <Link className={`stat ${d.out_of_stock_count ? "alert" : ""}`} href="/reports/out-of-stock"><div className="k">Out of stock</div><div className="v qty">{d.out_of_stock_count}</div></Link>
            <Link className={`stat ${d.expired_count ? "alert" : d.expiring_count ? "caution" : ""}`} href="/reports/expiry"><div className="k">Expiring in 30 days</div><div className="v qty">{d.expiring_count}{d.expired_count ? <span className="u" style={{ color: "var(--bad)" }}>+{d.expired_count} expired</span> : null}</div></Link>
          </div>

          <div className="grid two" style={{ alignItems: "start" }}>
            <section className="panel">
              <div className="panel-head"><h2>Last {d.period_days} days</h2></div>
              <div className="panel-pad grid two" style={{ gap: 12 }}>
                <div><div className="meta">Received</div><Qty v={d.received_units} unit="units" className="" /></div>
                <div><div className="meta">Issued &amp; sold</div><Qty v={d.issued_units} unit="units" /></div>
              </div>
              <div className="divider" />
              <div className="panel-pad stack tight">
                <h3>Most issued</h3>
                {d.top_products.length === 0 ? <p className="meta">Nothing issued yet.</p> :
                  d.top_products.map((t) => (
                    <div key={t.product_id} className="locline">
                      <Link href={`/products/${t.product_id}`}>{t.name}</Link><span className="num">{qty(t.quantity)}</span>
                    </div>
                  ))}
              </div>
            </section>
            <section className="panel">
              <div className="panel-head"><h2>Stock by location</h2>{d.pending_transfers > 0 && <Link className="badge info" href="/transfers">{d.pending_transfers} transfer{d.pending_transfers > 1 ? "s" : ""} open</Link>}</div>
              <div className="panel-pad">
                {d.by_location.length === 0 ? <p className="meta">No stock yet.</p> : d.by_location.map((l) => (
                  <div key={l.warehouse_id} className="locline">
                    <Link href={`/inventory?warehouse_id=${l.warehouse_id}`}>{l.name}</Link>
                    <span className="num"><b>{qty(l.units)}</b> <span className="meta">units · {money(l.value, d.currency)}</span></span>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <section className="panel">
            <div className="panel-head"><h2>Latest stock changes</h2><Link href="/inventory?tab=movements" className="btn small">See all</Link></div>
            {d.recent_movements.length === 0 ? <Empty title="No stock changes yet" text="Receive some stock and it will show up here." /> :
              <MovementTable rows={d.recent_movements} compact />}
          </section>
        </>
      )}
    </div>
  );
}
