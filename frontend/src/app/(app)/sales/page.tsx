"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ORDER_STATUS, PAYMENT_STATUS } from "@/components/orderlines";
import { Can, Empty, Loading, NoAccess, PageHead, Pager } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { date, money } from "@/lib/format";
import { useApi, useDebounced } from "@/lib/hooks";

const TABS = [{ k: "open", l: "To send" }, { k: "unpaid", l: "Not fully paid" }, { k: "completed", l: "Completed" }, { k: "", l: "All" }];

export default function Sales() {
  const { can, me } = useAuth();
  const router = useRouter();
  const [tab, setTab] = useState("open");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  const query: Record<string, unknown> = { q: dq, page, page_size: 25 };
  if (tab === "open") query.open_only = true;
  else if (tab === "unpaid") query.payment_status = ["unpaid", "partial"];
  else if (tab) query.status = tab;
  const { data } = useApi<any>(can("sales.view") ? "/sales-orders" : null, query);
  if (!can("sales.view")) return <NoAccess />;
  const cur = me?.organization.currency;
  return (
    <>
      <PageHead title="Sales" sub="Customer orders: what to send and what you’re owed.">
        <Can perm="sales.create"><Link className="act" href="/sales/new">New sales order</Link></Can>
      </PageHead>
      <div className="tabs" role="tablist">{TABS.map((t) => <button key={t.k} role="tab" className="tab" aria-selected={tab === t.k} onClick={() => { setTab(t.k); setPage(1); }}>{t.l}</button>)}</div>
      <input className="input" style={{ maxWidth: 320, marginBottom: 14 }} placeholder="Order number, customer or their reference" aria-label="Search sales orders" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      <div className="panel">
        {!data ? <Loading /> : data.items.length === 0 ? (
          <Empty title={tab === "open" ? "Nothing waiting to be sent" : "No sales orders here"}
                 action={can("sales.create") ? <Link className="btn primary" href="/sales/new">Create a sales order</Link> : undefined} />
        ) : (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Order</th><th>Customer</th><th>Status</th><th className="hide-mobile">Payment</th><th className="r">Total</th><th className="r hide-mobile">Owed</th></tr></thead>
              <tbody>{data.items.map((so: any) => {
                const s = ORDER_STATUS[so.status]; const ps = PAYMENT_STATUS[so.payment_status];
                return (
                  <tr key={so.id} className="click" onClick={() => router.push(`/sales/${so.id}`)}>
                    <td className="num" style={{ whiteSpace: "nowrap" }}><Link href={`/sales/${so.id}`} onClick={(e) => e.stopPropagation()}>{so.number}</Link><span className="sub">{date(so.order_date)}</span></td>
                    <td>{so.customer_name || "Walk-in"}<span className="sub hide-mobile">from {so.source_name}</span></td>
                    <td><span className={`badge ${s.tone}`}>{s.label}</span><span className="sub only-mobile"><span className={`badge ${ps.tone}`}>{ps.label}</span></span></td>
                    <td className="hide-mobile"><span className={`badge ${ps.tone}`}>{ps.label}</span></td>
                    <td className="r num">{money(so.total, cur)}</td>
                    <td className="r num hide-mobile">{Number(so.balance_due) > 0 && so.status !== "cancelled" ? money(so.balance_due, cur) : "—"}</td>
                  </tr>);
              })}</tbody>
            </table></div>
            <Pager page={page} pageSize={25} total={data.total} onPage={setPage} />
          </>
        )}
      </div>
    </>
  );
}
