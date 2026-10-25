"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Attachments } from "@/components/attachments";
import { MovementTable } from "@/components/movements";
import { Loading, Notice, Qty, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { TRANSFER_STATUS, dateTime, errMsg, qty } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Movement, Page, Transfer } from "@/lib/types";

// The order a transfer moves through, shown as a progress line.
const STEPS = ["draft", "requested", "approved", "in_transit", "received"];

export default function TransferDetail() {
  const { id } = useParams<{ id: string }>();
  const { can, mod } = useAuth();
  const toast = useToast();
  const { data: t, reload, setData } = useApi<Transfer>(`/transfers/${id}`);
  const mv = useApi<Page<Movement>>(`/movements`, { reference_id: id, page_size: 100, _r: t?.status });
  const [recv, setRecv] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!t) return <Loading />;
  const s = TRANSFER_STATUS[t.status];

  const act = async (action: string, body?: unknown, done?: string) => {
    setBusy(true); setErr(null);
    try { setData(await api<Transfer>(`/transfers/${t.id}/${action}`, { method: "POST", body })); toast(done || "Saved"); mv.reload(); setRecv({}); }
    catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  const open = ["draft", "requested", "approved"].includes(t.status);
  const needsApproval = mod("transfer_approval") && t.status !== "approved";
  const stepIdx = STEPS.indexOf(t.status);

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div><p className="meta"><Link href="/transfers">Transfers</Link></p>
          <h1 style={{ marginTop: 4 }} className="num">{t.number}</h1>
          <p className="muted" style={{ marginTop: 4 }}>{t.from_name} → {t.to_name}</p></div>
        <span className={`badge ${s.tone}`} style={{ fontSize: 14, padding: "4px 12px" }}>{s.label}</span>
      </div>
      {t.status !== "cancelled" && (
        <ol className="row" aria-label="Progress" style={{ listStyle: "none", padding: 0, margin: 0, gap: 6 }}>
          {STEPS.filter((x) => x !== "requested" || mod("transfer_approval") || t.status === "requested").filter((x) => x !== "approved" || mod("transfer_approval") || t.status === "approved").map((x, i, arr) => (
            <li key={x} className={`badge ${STEPS.indexOf(x) <= stepIdx ? "ok" : ""}`}>{TRANSFER_STATUS[x].label}{i < arr.length - 1 ? "" : ""}</li>
          ))}
        </ol>
      )}
      {err && <Notice tone="bad">{err}</Notice>}
      {t.notes && <Notice tone="info">{t.notes}</Notice>}

      <section className="panel">
        <div className="table-wrap"><table className="t">
          <thead><tr><th>Product</th>{open && <th className="r">Available</th>}<th className="r">Requested</th><th className="r">Sent</th><th className="r">Received</th><th className="r">Still to come</th>
            {t.status === "in_transit" && <th className="r">Receive now</th>}</tr></thead>
          <tbody>{t.items.map((i) => (
            <tr key={i.id}>
              <td><Link href={`/products/${i.product_id}`}>{i.product_name}</Link><span className="sub">{i.sku}</span></td>
              {open && <td className="r">{i.available_qty === null ? "—" : <span className={Number(i.available_qty) < Number(i.requested_qty) ? "chg-out qty" : "qty"}>{qty(i.available_qty)}</span>}</td>}
              <td className="r"><Qty v={i.requested_qty} unit={i.unit} /></td>
              <td className="r num">{qty(i.dispatched_qty)}</td>
              <td className="r num">{qty(i.received_qty)}</td>
              <td className="r num">{qty(Number(i.dispatched_qty) ? Number(i.dispatched_qty) - Number(i.received_qty) : i.remaining_qty)}</td>
              {t.status === "in_transit" && <td className="r"><input className="input num" style={{ width: 100 }} inputMode="decimal" aria-label={`Receive ${i.product_name}`}
                placeholder={qty(Number(i.dispatched_qty) - Number(i.received_qty))} value={recv[i.id] ?? ""} onChange={(e) => setRecv((r) => ({ ...r, [i.id]: e.target.value }))} /></td>}
            </tr>))}</tbody>
        </table></div>
      </section>

      {can("inventory.transfer") && (
        <div className="row">
          {t.status === "draft" && <button className="btn" disabled={busy} onClick={() => act("submit", undefined, "Sent for approval")}>Send for approval</button>}
          {["draft", "requested"].includes(t.status) && can("inventory.transfer_approve") && <button className="btn primary" disabled={busy} onClick={() => act("approve", undefined, "Approved")}>Approve</button>}
          {open && !needsApproval && <button className="btn go" disabled={busy} onClick={() => act("dispatch", undefined, "Stock sent")}>Send stock now</button>}
          {t.status === "in_transit" && <button className="btn go" disabled={busy} onClick={() => {
            const entered = Object.entries(recv).filter(([, v]) => v !== "");
            act("receive", entered.length ? { quantities: Object.fromEntries(t.items.map((i) => [i.id, recv[i.id] || "0"])) } : undefined, "Received");
          }}>{Object.values(recv).some((v) => v) ? "Receive these amounts" : "Receive everything"}</button>}
          {open && <button className="btn danger" disabled={busy} onClick={() => confirm("Cancel this transfer?") && act("cancel", undefined, "Cancelled")}>Cancel transfer</button>}
        </div>
      )}
      {open && needsApproval && <p className="meta">A manager must approve before stock can be sent.</p>}

      <Attachments entityType="transfer" entityId={t.id} title="Delivery notes and files" />

      <section className="panel">
        <div className="panel-head"><h2>Stock movements</h2><span className="meta">Created {dateTime(t.created_at)}{t.dispatched_at ? ` · sent ${dateTime(t.dispatched_at)}` : ""}{t.received_at ? ` · received ${dateTime(t.received_at)}` : ""}</span></div>
        {mv.data?.items.length ? <MovementTable rows={mv.data.items} compact /> : <p className="panel-pad meta">Nothing has moved yet.</p>}
      </section>
    </div>
  );
}
