"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { I } from "@/components/icons";
import { ProductPicker } from "@/components/pickers";
import { Receipt } from "@/components/receipt";
import { Modal, TillBar, useOnline, usePending } from "@/components/till";
import { Field, Notice, Pager, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, errMsg, money as fmtMoney } from "@/lib/format";
import { useApi, useDebounced } from "@/lib/hooks";
import { type OutboxItem, type Shift, METHOD_LABELS, isNetworkError, outboxAll, outboxDelete, outboxPut } from "@/lib/pos";
import type { ProductDetail } from "@/lib/types";

type Row = { id: string; number: string; kind: string; sold_at: string; total: number; customer_name: string | null; cashier: string | null;
  location_name: string; lines: number; needs_review: boolean; offline: boolean; methods: string[] };

export default function Receipts() {
  const { me, can } = useAuth();
  const online = useOnline();
  const { pending, sync } = usePending();
  const [tab, setTab] = useState<"server" | "device">("server");
  useEffect(() => { if (new URLSearchParams(window.location.search).get("tab") === "device") setTab("device"); }, []);
  const [q, setQ] = useState("");
  const dq = useDebounced(q, 250);
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState("");
  const [review, setReview] = useState(false);
  const { data, error, reload } = useApi<{ items: Row[]; total: number }>(tab === "server" ? "/pos/sales" : null,
    { q: dq, page, page_size: 30, kind: kind || undefined, needs_review: review ? true : undefined });
  const [noReceipt, setNoReceipt] = useState(false);
  const cur = me!.organization.currency;
  return (
    <>
      <TillBar title="Receipts and returns" online={online} pending={pending} back={{ href: "/pos", label: "Till" }} />
      <div className="till-page stack">
        <div className="tabs" role="tablist">
          <button role="tab" className="tab" aria-selected={tab === "server"} onClick={() => setTab("server")}>All receipts</button>
          <button role="tab" className="tab" aria-selected={tab === "device"} onClick={() => setTab("device")}>On this device{pending ? ` (${pending})` : ""}</button>
        </div>
        {tab === "device" ? <DeviceQueue sync={sync} currency={cur} /> : (
          <>
            <div className="row">
              <input className="input" style={{ maxWidth: 320 }} type="search" placeholder="Receipt number, customer name or phone" aria-label="Search receipts"
                     value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
              <select className="input" style={{ width: "auto" }} value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }} aria-label="Type">
                <option value="">Sales and returns</option><option value="sale">Sales</option><option value="return">Returns</option>
              </select>
              {can("pos.manage") && <label className="check"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /><span>Needs checking</span></label>}
              {can("pos.refund") && <button className="btn small" onClick={() => setNoReceipt(true)}>Return without a receipt</button>}
            </div>
            {error && <Notice tone={online ? "bad" : "warn"}>{online ? error : "You’re offline. Receipts on the server show when you’re back online; sales made now are under “On this device”."}</Notice>}
            <div className="panel">
              <div className="table-wrap"><table className="t">
                <thead><tr><th>Receipt</th><th className="hide-mobile">When</th><th className="hide-mobile">Customer</th><th className="hide-mobile">Paid by</th><th className="r">Total</th></tr></thead>
                <tbody>{(data?.items || []).map((r) => (
                  <tr key={r.id} className="click" onClick={() => (window.location.href = `/pos/receipts/${r.id}`)}>
                    <td><Link href={`/pos/receipts/${r.id}`} className="num"><b>{r.number}</b></Link>
                      {r.kind === "return" && <span className="badge info" style={{ marginLeft: 6 }}>Return</span>}
                      {r.needs_review && <span className="badge warn" style={{ marginLeft: 6 }}>Check</span>}
                      <span className="sub">{r.lines} item{r.lines !== 1 ? "s" : ""} · {r.cashier}{r.offline ? " · made offline" : ""}</span>
                      <span className="sub only-mobile">{dateTime(r.sold_at)}{r.customer_name ? ` · ${r.customer_name}` : ""}</span></td>
                    <td className="hide-mobile num">{dateTime(r.sold_at)}</td>
                    <td className="hide-mobile">{r.customer_name || <span className="meta">Walk-in</span>}</td>
                    <td className="hide-mobile">{r.methods.map((x) => METHOD_LABELS[x] || x).join(", ")}</td>
                    <td className="r num"><b className={r.kind === "return" ? "chg-out" : ""}>{r.kind === "return" ? "−" : ""}{fmtMoney(r.total, cur)}</b></td>
                  </tr>))}</tbody>
              </table></div>
              {data && data.total === 0 && <p className="panel-pad meta">No receipts yet.</p>}
              {data && <Pager page={page} pageSize={30} total={data.total} onPage={setPage} />}
            </div>
          </>
        )}
      </div>
      {noReceipt && <NoReceiptReturn onClose={() => setNoReceipt(false)} onDone={(id) => { setNoReceipt(false); reload(); window.location.href = `/pos/receipts/${id}`; }} />}
    </>
  );
}

function DeviceQueue({ sync, currency }: { sync: () => Promise<{ sent: number; failed: number; left: number } | null>; currency: string }) {
  const { me } = useAuth();
  const toast = useToast();
  const [items, setItems] = useState<OutboxItem[]>([]);
  const [open, setOpen] = useState<OutboxItem | null>(null);
  const load = useCallback(async () => setItems((await outboxAll()).filter((x) => x.user_id === me!.id).sort((a, b) => b.created_at.localeCompare(a.created_at))), [me]);
  useEffect(() => { load(); }, [load]);
  if (!items.length) return <Notice tone="ok">Every sale made on this device has been uploaded.</Notice>;
  return (
    <div className="stack">
      <div className="row">
        <button className="btn primary" onClick={async () => { const r = await sync(); await load(); toast(r?.sent ? `Uploaded ${r.sent} sale(s)` : "Couldn’t upload yet. Check the internet."); }}>
          <I.upload />Upload now</button>
        <span className="meta">They also upload by themselves when the internet is back.</span>
      </div>
      <div className="panel"><table className="t"><tbody>
        {items.map((x) => (
          <tr key={x.client_id}>
            <td><b className="num">{x.receipt.number}</b><span className="sub">{dateTime(x.created_at)} · {x.receipt.lines.length} item(s)</span>
              {x.status === "failed" && <span className="sub bad-text">Not accepted: {x.error}</span>}</td>
            <td className="r num">{fmtMoney(x.receipt.total, currency)}</td>
            <td className="r">
              <div className="row end">
                <button className="btn small" onClick={() => setOpen(x)}>View</button>
                {x.status === "failed" && <button className="btn small" onClick={async () => { await outboxPut({ ...x, status: "pending" }); await sync(); await load(); }}>Try again</button>}
                {x.status === "failed" && <button className="btn small danger" onClick={async () => {
                  if (confirm("Delete this sale from the device? It will not be recorded anywhere. Only do this if it was a mistake.")) { await outboxDelete(x.client_id); load(); }
                }}>Delete</button>}
              </div>
            </td>
          </tr>
        ))}
      </tbody></table></div>
      {open && <Modal title={open.receipt.number} onClose={() => setOpen(null)}><Receipt r={open.receipt} /></Modal>}
    </div>
  );
}

function NoReceiptReturn({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const { me, can } = useAuth();
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [qty, setQty] = useState("1");
  const [price, setPrice] = useState("");
  const [restock, setRestock] = useState(true);
  const [batch, setBatch] = useState("");
  const [method, setMethod] = useState("cash");
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const { data: shift } = useApi<Shift | null>("/pos/shifts/current");
  const { data: boot } = useApi<{ methods: { key: string; label: string }[] }>("/pos/bootstrap");
  const p = detail?.product;
  return (
    <Modal title="Return without a receipt" onClose={onClose}>
      <form className="stack" onSubmit={async (e) => {
        e.preventDefault(); setErr(null);
        if (!shift) return setErr("Start a shift on the till first.");
        try {
          const r = await api<{ id: string }>("/pos/returns", { body: { shift_id: shift.id, refund_method: method, reason: reason || null,
            lines: [{ product_id: p!.id, quantity: qty, unit_price: price === "" ? undefined : price, restock, batch_number: batch || undefined }] } });
          onDone(r.id);
        } catch (e) { setErr(isNetworkError(e) ? "Returns need the internet." : errMsg(e)); }
      }}>
        {err && <Notice tone="bad">{err}</Notice>}
        {shift === null && <Notice tone="warn">Start a shift on the till first: refunds come out of a till.</Notice>}
        <ProductPicker value={detail} onChange={setDetail} label="What is coming back?" />
        {p && <>
          <div className="grid two">
            <Field label="How many" htmlFor="nq"><input id="nq" className="input num" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} /></Field>
            <Field label="Refund each" htmlFor="np" hint={can("pos.price_override") ? "Current price unless you change it" : "Current selling price"}>
              <input id="np" className="input num" inputMode="decimal" disabled={!can("pos.price_override")} placeholder={String(Number(p.selling_price))}
                     value={price} onChange={(e) => setPrice(e.target.value)} />
            </Field>
          </div>
          <label className="check"><input type="checkbox" checked={restock} onChange={(e) => setRestock(e.target.checked)} />
            <span>It can be sold again<span className="sub meta">Untick if it’s damaged: it won’t go back into stock.</span></span></label>
          {p.track_batches && restock && <Field label="Batch number" htmlFor="nb"><input id="nb" className="input" value={batch} onChange={(e) => setBatch(e.target.value)} /></Field>}
        </>}
        <Field label="Give the money back as" htmlFor="nm">
          <select id="nm" className="input" value={method} onChange={(e) => setMethod(e.target.value)}>
            {(boot?.methods || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
            <option value="credit">Store credit (for an exchange)</option>
          </select>
        </Field>
        <Field label="Reason (optional)" htmlFor="nr"><input id="nr" className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <button className="btn primary block" disabled={!p || !shift}>Record return{p ? ` · ${fmtMoney(Number(qty || 0) * Number(price || p.selling_price), me!.organization.currency)}` : ""}</button>
      </form>
    </Modal>
  );
}
