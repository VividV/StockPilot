"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Can, Empty, Loading, NoAccess, PageHead, Pager } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { TRANSFER_STATUS, date } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Page, Transfer } from "@/lib/types";

const FILTERS = [
  { k: "", l: "All" }, { k: "open", l: "Open" }, { k: "requested", l: "Waiting for approval" },
  { k: "in_transit", l: "On the way" }, { k: "received", l: "Received" },
];

export default function TransfersPage() {
  const { can } = useAuth();
  const router = useRouter();
  const [f, setF] = useState("open");
  const [page, setPage] = useState(1);
  const status = f === "open" ? ["draft", "requested", "approved", "in_transit"] : f ? [f] : undefined;
  const { data } = useApi<Page<Transfer>>(can("inventory.view") ? "/transfers" : null, { status, page, page_size: 25 });
  if (!can("inventory.view")) return <NoAccess />;
  return (
    <>
      <PageHead title="Transfers" sub="Stock moving between your locations.">
        <Can perm="inventory.transfer"><Link className="act" href="/transfer">Transfer stock</Link><Link className="btn" href="/transfers/new">Transfer several products</Link></Can>
      </PageHead>
      <div className="tabs" role="tablist">
        {FILTERS.map((x) => <button key={x.k} role="tab" className="tab" aria-selected={f === x.k} onClick={() => { setF(x.k); setPage(1); }}>{x.l}</button>)}
      </div>
      <div className="panel">
        {!data ? <Loading /> : data.items.length === 0 ? <Empty title={f === "open" ? "Nothing on the move" : "No transfers"} /> : (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Number</th><th>From → to</th><th className="hide-mobile">Items</th><th>Status</th><th className="hide-mobile">Created</th></tr></thead>
              <tbody>{data.items.map((t) => {
                const s = TRANSFER_STATUS[t.status];
                return (
                  <tr key={t.id} className="click" onClick={() => router.push(`/transfers/${t.id}`)}>
                    <td className="num"><Link href={`/transfers/${t.id}`}>{t.number}</Link></td>
                    <td>{t.from_name}<span className="sub">to {t.to_name}</span></td>
                    <td className="hide-mobile">{t.items.length === 1 ? t.items[0].product_name : `${t.items.length} products`}</td>
                    <td><span className={`badge ${s.tone}`}>{s.label}</span></td>
                    <td className="hide-mobile">{date(t.created_at)}</td>
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
