"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ORDER_STATUS } from "@/components/orderlines";
import { Can, Empty, Loading, NoAccess, PageHead, Pager } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { date, money } from "@/lib/format";
import { useApi, useDebounced } from "@/lib/hooks";

const TABS = [{ k: "open", l: "Open" }, { k: "overdue", l: "Late" }, { k: "draft", l: "Drafts" }, { k: "completed", l: "Completed" }, { k: "", l: "All" }];

export default function Purchases() {
  const { can, me } = useAuth();
  const router = useRouter();
  const [tab, setTab] = useState("open");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  useEffect(() => { const t = new URLSearchParams(window.location.search).get("tab"); if (t !== null) setTab(t); }, []);
  const query: Record<string, unknown> = { q: dq, page, page_size: 25 };
  if (tab === "open") query.open_only = true;
  else if (tab === "overdue") query.overdue = true;
  else if (tab) query.status = tab;
  const { data } = useApi<any>(can("purchases.view") ? "/purchase-orders" : null, query);
  if (!can("purchases.view")) return <NoAccess />;
  const cur = me?.organization.currency;
  return (
    <>
      <PageHead title="Purchases" sub="Orders to your suppliers, and what has arrived.">
        <Can perm="purchases.create">
          <Link className="btn" href="/purchases/reorder">Reorder low stock</Link>
          <Link className="act" href="/purchases/new">New purchase order</Link>
        </Can>
      </PageHead>
      <div className="tabs" role="tablist">{TABS.map((t) => <button key={t.k} role="tab" className="tab" aria-selected={tab === t.k} onClick={() => { setTab(t.k); setPage(1); }}>{t.l}</button>)}</div>
      <input className="input" style={{ maxWidth: 320, marginBottom: 14 }} placeholder="PO number, supplier or their reference" aria-label="Search purchase orders" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      <div className="panel">
        {!data ? <Loading /> : data.items.length === 0 ? (
          <Empty title={tab === "overdue" ? "Nothing is late" : "No purchase orders here"}
                 action={can("purchases.create") && tab === "open" ? <Link className="btn primary" href="/purchases/new">Create a purchase order</Link> : undefined} />
        ) : (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>PO</th><th>Supplier</th><th className="hide-mobile">Expected</th><th className="hide-mobile">Status</th><th className="r">Total</th><th className="r hide-mobile">Still to come</th></tr></thead>
              <tbody>{data.items.map((po: any) => {
                const s = ORDER_STATUS[po.status];
                return (
                  <tr key={po.id} className="click" onClick={() => router.push(`/purchases/${po.id}`)}>
                    <td className="num" style={{ whiteSpace: "nowrap" }}><Link href={`/purchases/${po.id}`} onClick={(e) => e.stopPropagation()}>{po.number}</Link><span className="sub">{date(po.order_date)}</span></td>
                    <td>{po.supplier_name}<span className="sub hide-mobile">to {po.destination_name}</span>
                      <span className="sub only-mobile"><span className={`badge ${s.tone}`}>{s.label}</span>{po.overdue && <span className="badge bad" style={{ marginLeft: 4 }}>Late</span>}</span></td>
                    <td className="hide-mobile">{po.expected_date ? date(po.expected_date) : "—"}</td>
                    <td className="hide-mobile"><span className={`badge ${s.tone}`}>{s.label}</span>{po.overdue && <span className="badge bad" style={{ marginLeft: 6 }}>Late</span>}</td>
                    <td className="r num">{money(po.total, cur)}</td>
                    <td className="r num hide-mobile">{Number(po.outstanding_value) ? money(po.outstanding_value, cur) : "—"}</td>
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
