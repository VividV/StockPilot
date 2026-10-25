"use client";
import Link from "next/link";
import { useState } from "react";
import { TillBar, useOnline } from "@/components/till";
import { Notice, Pager } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { dateTime, money as fmtMoney } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Shift } from "@/lib/pos";

export default function Shifts() {
  const { me, can } = useAuth();
  const online = useOnline();
  const [page, setPage] = useState(1);
  const { data, error } = useApi<{ items: Shift[]; total: number }>("/pos/shifts", { page, page_size: 30 });
  const m = (n: number) => fmtMoney(n, me!.organization.currency);
  return (
    <>
      <TillBar title={can("pos.manage") ? "Till shifts" : "My shifts"} online={online} back={{ href: "/pos", label: "Till" }} />
      <div className="till-page stack">
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="panel">
          <div className="table-wrap"><table className="t">
            <thead><tr><th>Shift</th><th className="hide-mobile">Opened</th><th className="r">Net sales</th><th className="r">Cash</th></tr></thead>
            <tbody>{(data?.items || []).map((s) => (
              <tr key={s.id}>
                <td><Link href={`/pos/shifts/${s.id}`}><b className="num">{s.number}</b></Link>
                  {s.status === "open" && <span className="badge ok" style={{ marginLeft: 6 }}>Open</span>}
                  <span className="sub">{s.user_name} · {s.location_name}</span><span className="sub only-mobile">{dateTime(s.opened_at)}</span></td>
                <td className="hide-mobile num">{dateTime(s.opened_at)}{s.closed_at ? ` – ${dateTime(s.closed_at)}` : ""}</td>
                <td className="r num">{m(s.summary?.net_sales || 0)}</td>
                <td className="r num">{s.difference === null ? <span className="meta">{s.status === "open" ? "Counting later" : "—"}</span>
                  : Number(s.difference) === 0 ? <span className="badge ok">Exact</span>
                  : <span className={`badge ${Number(s.difference) < 0 ? "bad" : "warn"}`}>{Number(s.difference) < 0 ? "Short" : "Over"} {m(Math.abs(Number(s.difference)))}</span>}</td>
              </tr>))}</tbody>
          </table></div>
          {data && data.total === 0 && <p className="panel-pad meta">No shifts yet.</p>}
          {data && <Pager page={page} pageSize={30} total={data.total} onPage={setPage} />}
        </div>
      </div>
    </>
  );
}
