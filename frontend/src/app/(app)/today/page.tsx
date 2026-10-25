"use client";
import Link from "next/link";
import { useState } from "react";
import { I } from "@/components/icons";
import { Loading, NoAccess, Notice, PageHead } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { money as fmtMoney, qty } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Warehouse } from "@/lib/types";

type Totals = { sales_count: number; sales_total: number; returns_count: number; returns_total: number; net_sales: number; tax: number;
  discounts: number; cost_of_goods: number; profit: number; average_sale: number };
type Summary = {
  date: string; timezone: string; totals: Totals; yesterday: Totals; same_day_last_week: Totals;
  by_branch: { warehouse_id: string; name: string; sales_count: number; net_sales: number; profit: number }[];
  by_method: { method: string; label: string; amount: number }[]; by_hour: number[];
  by_cashier: { user_id: string; name: string; sales_count: number; net_sales: number }[];
  top_products: { product_id: string; name: string; sku: string; quantity: number; revenue: number }[];
  slow_movers: { product_id: string; name: string; sku: string; on_hand: number; value: number }[];
  open_shifts: { id: string; number: string; user_name: string; location_name: string; opened_at: string; sales_count: number; net_sales: number; expected_cash: number }[];
  needs_review: number;
};

function change(now: number, before: number) {
  if (!before) return null;
  const p = ((now - before) / Math.abs(before)) * 100;
  return { p, text: `${p >= 0 ? "▲" : "▼"} ${Math.abs(p).toFixed(0)}%` };
}

function localISO(d: Date) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

export default function Today() {
  const { me, can, mod } = useAuth();
  const [day, setDay] = useState("");
  const [wh, setWh] = useState("");
  const { data: whs } = useApi<Warehouse[]>(can("locations.view") ? "/warehouses" : null);
  const { data, error } = useApi<Summary>(can("reports.view") ? "/pos/summary" : null, { date: day || undefined, warehouse_id: wh || undefined });
  if (!can("reports.view")) return <NoAccess />;
  const cur = me!.organization.currency;
  const m = (n: number) => fmtMoney(n, cur);
  const t = data?.totals;
  const isToday = !day || day === data?.date;
  const label = !data ? "" : isToday ? "today" : new Date(data.date + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  const maxHour = data ? Math.max(1, ...data.by_hour.map(Number)) : 1;
  const firstHour = data ? Math.max(0, Math.min(8, data.by_hour.findIndex((h) => Number(h) !== 0))) : 8;
  const lastHour = data ? Math.max(20, 23 - [...data.by_hour].reverse().findIndex((h) => Number(h) !== 0)) : 20;
  const vsY = t && data ? change(Number(t.net_sales), Number(data.yesterday.net_sales)) : null;
  const vsW = t && data ? change(Number(t.net_sales), Number(data.same_day_last_week.net_sales)) : null;
  return (
    <>
      <PageHead title="Sales today" sub={data ? `Counter sales ${label}, ${data.timezone.replace("_", " ")} time` : "Counter sales"}>
        <input type="date" className="input" style={{ width: "auto" }} aria-label="Day" value={day || data?.date || ""} max={data?.date && isToday ? data.date : undefined}
               onChange={(e) => setDay(e.target.value)} />
        {whs && whs.length > 1 && (
          <select className="input" style={{ width: "auto" }} value={wh} onChange={(e) => setWh(e.target.value)} aria-label="Shop">
            <option value="">All shops</option>{whs.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        )}
        {mod("pos") && can("pos.sell") && <Link href="/pos" className="act"><I.till />Open the till</Link>}
      </PageHead>
      {!mod("pos") && <Notice tone="info">Counter sales are switched off. Turn on “Counter sales (till)” in Settings → Business to sell from the till.</Notice>}
      {error && <Notice tone="bad">{error}</Notice>}
      {!data || !t ? <Loading /> : (
        <div className="stack" style={{ gap: 20 }}>
          <div className="stats money-stats">
            <div className="stat"><div className="k">Net sales {label}</div><div className="v qty">{m(t.net_sales)}</div>
              <div className="meta">{vsY ? <span className={vsY.p >= 0 ? "ok-text" : "bad-text"}>{vsY.text}</span> : null}{vsY ? " vs yesterday" : "Nothing yesterday to compare"}
                {vsW ? <> · <span className={vsW.p >= 0 ? "ok-text" : "bad-text"}>{vsW.text}</span> vs last week</> : null}</div></div>
            <div className="stat"><div className="k">Profit</div><div className="v qty">{m(t.profit)}</div><div className="meta">after cost of goods{Number(t.tax) ? " and tax" : ""}</div></div>
            <div className="stat"><div className="k">Sales</div><div className="v qty">{t.sales_count}</div><div className="meta">average {m(t.average_sale)}</div></div>
            <div className={`stat ${t.returns_count ? "caution" : ""}`}><div className="k">Returns</div><div className="v qty">{m(t.returns_total)}</div><div className="meta">{t.returns_count} return{t.returns_count !== 1 ? "s" : ""}</div></div>
          </div>

          {data.needs_review > 0 && (
            <Notice tone="warn">{data.needs_review} counter sale{data.needs_review !== 1 ? "s" : ""} in the last week need checking (made offline with too little stock, a changed price or a short payment).{" "}
              <Link href="/pos/receipts">See them</Link></Notice>
          )}

          <div className="grid two">
            <section className="panel">
              <div className="panel-head"><h2>Money in</h2><span className="meta">by how customers paid</span></div>
              {data.by_method.length === 0 ? <p className="panel-pad meta">No payments yet.</p> : (
                <table className="t"><tbody>{data.by_method.map((x) => (
                  <tr key={x.method}><td>{x.method === "credit" ? "Store credit (given − used)" : x.label}</td><td className="r num"><b>{m(x.amount)}</b></td></tr>
                ))}</tbody></table>
              )}
            </section>
            <section className="panel">
              <div className="panel-head"><h2>Through the day</h2></div>
              <div className="hours" role="img" aria-label="Sales by hour">
                {data.by_hour.slice(firstHour, lastHour + 1).map((h, i) => (
                  <div key={i} className="hour" title={`${firstHour + i}:00 · ${m(Number(h))}`}>
                    <span className="bar" style={{ height: `${Math.max(0, Number(h)) / maxHour * 100}%` }} />
                    <span className="hl">{(firstHour + i) % 3 === 0 ? firstHour + i : ""}</span>
                  </div>
                ))}
              </div>
            </section>
          </div>

          {data.by_branch.length > 1 || (!wh && data.by_branch.length === 1) ? (
            <section className="panel">
              <div className="panel-head"><h2>By shop</h2></div>
              <table className="t"><thead><tr><th>Shop</th><th className="r">Sales</th><th className="r">Net sales</th><th className="r hide-mobile">Profit</th></tr></thead>
                <tbody>{data.by_branch.map((b) => (
                  <tr key={b.warehouse_id}><td>{b.name}</td><td className="r num">{b.sales_count}</td><td className="r num"><b>{m(b.net_sales)}</b></td><td className="r num hide-mobile">{m(b.profit)}</td></tr>
                ))}</tbody></table>
            </section>
          ) : null}

          <div className="grid two">
            <section className="panel">
              <div className="panel-head"><h2>Best sellers</h2><Link href="/reports/pos-products" className="meta">All products</Link></div>
              {data.top_products.length === 0 ? <p className="panel-pad meta">Nothing sold yet.</p> : (
                <table className="t"><tbody>{data.top_products.map((p, i) => (
                  <tr key={p.product_id}><td><span className="meta num">{i + 1}.</span> <Link href={`/products/${p.product_id}`}>{p.name}</Link><span className="sub">{qty(p.quantity)} sold</span></td>
                    <td className="r num">{m(p.revenue)}</td></tr>
                ))}</tbody></table>
              )}
            </section>
            <section className="panel">
              <div className="panel-head"><h2>Not selling</h2><span className="meta">in stock, no sales in 30 days</span></div>
              {data.slow_movers.length === 0 ? <p className="panel-pad meta">Everything in stock has sold recently.</p> : (
                <table className="t"><tbody>{data.slow_movers.map((p) => (
                  <tr key={p.product_id}><td><Link href={`/products/${p.product_id}`}>{p.name}</Link><span className="sub">{qty(p.on_hand)} on the shelf</span></td>
                    <td className="r num"><span className="meta">tied up</span> {m(p.value)}</td></tr>
                ))}</tbody></table>
              )}
            </section>
          </div>

          <div className="grid two">
            <section className="panel">
              <div className="panel-head"><h2>Tills open now</h2><Link href="/pos/shifts" className="meta">All shifts</Link></div>
              {data.open_shifts.length === 0 ? <p className="panel-pad meta">No till is open.</p> : (
                <table className="t"><tbody>{data.open_shifts.map((s) => (
                  <tr key={s.id}><td><Link href={`/pos/shifts/${s.id}`}>{s.user_name}</Link><span className="sub">{s.location_name} · {s.sales_count} sale{s.sales_count !== 1 ? "s" : ""}</span></td>
                    <td className="r num"><span className="meta">cash expected</span><br /><b>{m(s.expected_cash)}</b></td></tr>
                ))}</tbody></table>
              )}
            </section>
            <section className="panel">
              <div className="panel-head"><h2>By cashier</h2></div>
              {data.by_cashier.length === 0 ? <p className="panel-pad meta">No sales yet.</p> : (
                <table className="t"><tbody>{data.by_cashier.map((c) => (
                  <tr key={c.user_id || c.name}><td>{c.name}<span className="sub">{c.sales_count} sale{c.sales_count !== 1 ? "s" : ""}</span></td><td className="r num">{m(c.net_sales)}</td></tr>
                ))}</tbody></table>
              )}
            </section>
          </div>
          <p className="meta">More detail: <Link href="/reports/pos-sales">every receipt</Link> · <Link href="/reports/pos-shifts">cash-ups</Link> · <Link href="/reports/pos-products">sales by product</Link>. {isToday ? "" : <button className="btn link" onClick={() => setDay(localISO(new Date()))}>Back to today</button>}</p>
        </div>
      )}
    </>
  );
}
