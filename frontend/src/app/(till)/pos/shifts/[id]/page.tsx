"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { I } from "@/components/icons";
import { usePrinter } from "@/components/receipt";
import { ShiftReport, TillBar, useOnline } from "@/components/till";
import { Loading, Notice } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { dateTime, money as fmtMoney } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { type Shift, METHOD_LABELS } from "@/lib/pos";

type Detail = Shift & { sales: { id: string; number: string; kind: string; total: number; sold_at: string; customer_name: string | null; needs_review: boolean; methods: string[] }[] };

export default function ShiftPage() {
  const { id } = useParams<{ id: string }>();
  const { me } = useAuth();
  const online = useOnline();
  const { data: s, error } = useApi<Detail>(`/pos/shifts/${id}`);
  const { print, host } = usePrinter();
  const m = (n: number) => fmtMoney(n, me!.organization.currency);
  return (
    <>
      <TillBar title={s ? `Shift ${s.number}` : "Shift"} sub={s?.status === "open" ? "Open" : s ? "Closed" : undefined} online={online} back={{ href: "/pos/shifts", label: "Shifts" }} />
      <div className="till-page stack" style={{ maxWidth: 760 }}>
        {error && <Notice tone="bad">{error}</Notice>}
        {!s ? <Loading /> : <>
          <div className="panel panel-pad"><ShiftReport s={s} money={m} /></div>
          <button className="btn" style={{ alignSelf: "flex-start" }} onClick={() => print(
            <div className="receipt w80"><div className="rc-center rc-biz">{me!.organization.name}</div><div className="rc-center">Cash-up {s.number}</div><ShiftReport s={s} money={m} /></div>)}>
            <I.printer />Print cash-up</button>
          <div className="panel">
            <div className="panel-head"><h2>Sales on this shift</h2><span className="meta">{s.sales.length}</span></div>
            <table className="t"><tbody>{s.sales.map((x) => (
              <tr key={x.id}>
                <td><Link href={`/pos/receipts/${x.id}`} className="num"><b>{x.number}</b></Link>{x.kind === "return" && <span className="badge info" style={{ marginLeft: 6 }}>Return</span>}
                  {x.needs_review && <span className="badge warn" style={{ marginLeft: 6 }}>Check</span>}
                  <span className="sub">{dateTime(x.sold_at)} · {x.methods.map((k) => METHOD_LABELS[k] || k).join(", ")}{x.customer_name ? ` · ${x.customer_name}` : ""}</span></td>
                <td className="r num">{x.kind === "return" ? "−" : ""}{m(x.total)}</td>
              </tr>))}</tbody></table>
          </div>
        </>}
      </div>
      {host}
    </>
  );
}
