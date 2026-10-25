"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Attachments } from "@/components/attachments";
import { MovementTable } from "@/components/movements";
import { LinesEditor, linesPayload, ORDER_STATUS, PAYMENT_STATUS, type OrderLine } from "@/components/orderlines";
import { Drawer, Field, Loading, Notice, Qty, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, dateTime, errMsg, money, qty } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { ProductDetail } from "@/lib/types";

export default function SaleDetail() {
  const { id } = useParams<{ id: string }>();
  const { can, me } = useAuth();
  const toast = useToast();
  const { data: so, setData, reload } = useApi<any>(`/sales-orders/${id}`);
  const [send, setSend] = useState<Record<string, string>>({});
  const [serials, setSerials] = useState<Record<string, string[]>>({});
  const [serialOpts, setSerialOpts] = useState<Record<string, string[]>>({});
  const [pay, setPay] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  if (!so) return <Loading />;
  const cur = me?.organization.currency;
  const open = ["draft", "confirmed", "partial"].includes(so.status);
  const canSend = open && can("sales.fulfill") && can("inventory.issue");
  const hasSerials = so.items.some((i: any) => i.track_serials && Number(i.remaining_qty) > 0);
  const s = ORDER_STATUS[so.status]; const ps = PAYMENT_STATUS[so.payment_status];

  const loadSerials = async (i: any) => {
    if (serialOpts[i.id]) return;
    const d = await api<ProductDetail>(`/products/${i.product_id}`);
    setSerialOpts((x) => ({ ...x, [i.id]: d.serials_in_stock.filter((sn) => sn.location_id === so.source_location_id).map((sn) => sn.serial) }));
  };
  const act = async (path: string, body?: unknown, ok?: string) => {
    setBusy(true); setErr(null);
    try {
      const out = await api(`/sales-orders/${so.id}/${path}`, { method: "POST", body });
      toast(out.message || ok || "Saved");
      setSend({}); setSerials({}); setSerialOpts({}); setPay("");
      reload();
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  const sendEntered = () => {
    const lines = so.items.filter((i: any) => (i.track_serials ? serials[i.id]?.length : send[i.id]))
      .map((i: any) => i.track_serials ? { item_id: i.id, quantity: serials[i.id].length, serials: serials[i.id] } : { item_id: i.id, quantity: send[i.id] });
    if (!lines.length) return setErr("Enter how many you are sending for at least one line.");
    act("fulfil", { lines });
  };

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <p className="meta"><Link href="/sales">Sales</Link></p>
          <h1 style={{ marginTop: 4 }} className="num">{so.number}</h1>
          <p className="muted" style={{ marginTop: 4 }}>{so.customer_name || "Walk-in customer"} · from {so.source_name}{so.customer_reference ? ` · their ref ${so.customer_reference}` : ""}</p>
        </div>
        <div className="row">
          <span className={`badge ${s.tone}`} style={{ fontSize: 14, padding: "4px 12px" }}>{s.label}</span>
          <span className={`badge ${ps.tone}`} style={{ fontSize: 14, padding: "4px 12px" }}>{ps.label}</span>
        </div>
      </div>
      {err && <Notice tone="bad">{err}</Notice>}

      <div className="stats">
        <div className="stat"><div className="k">Order total</div><div className="v qty" style={{ fontSize: 22 }}>{money(so.total, cur)}</div></div>
        <div className="stat"><div className="k">Paid</div><div className="v qty" style={{ fontSize: 22 }}>{money(so.amount_paid, cur)}</div></div>
        <div className={`stat ${Number(so.balance_due) > 0 && so.status !== "cancelled" ? "caution" : ""}`}><div className="k">Owed</div><div className="v qty" style={{ fontSize: 22 }}>{so.status === "cancelled" ? "—" : money(so.balance_due, cur)}</div></div>
        {can("reports.view") && <div className="stat"><div className="k">Profit on goods sent</div><div className="v qty" style={{ fontSize: 22 }}>{money(so.gross_profit, cur)}</div></div>}
      </div>

      <section className="panel">
        <div className="panel-head"><h2>Products</h2>{canSend && <span className="meta">Type what you’re sending, then press Send</span>}</div>
        <div className="table-wrap"><table className="t">
          <thead><tr><th>Product</th><th className="r">Ordered</th><th className="r">Sent</th><th className="r">Left to send</th>
            {open && <th className="r hide-mobile">In stock there</th>}<th className="r hide-mobile">Price</th><th className="r">Line total</th>{canSend && <th>Send now</th>}</tr></thead>
          <tbody>{so.items.map((i: any) => (
            <tr key={i.id}>
              <td><Link href={`/products/${i.product_id}`}>{i.product_name}</Link><span className="sub">{i.sku}{Number(i.discount_percent) ? ` · ${qty(i.discount_percent)}% off` : ""}</span></td>
              <td className="r"><Qty v={i.ordered_qty} unit={i.unit} /></td>
              <td className="r num">{qty(i.fulfilled_qty)}</td>
              <td className="r num">{Number(i.remaining_qty) ? <b>{qty(i.remaining_qty)}</b> : "—"}</td>
              {open && <td className="r hide-mobile"><span className={Number(i.available_qty) < Number(i.remaining_qty) ? "chg-out qty" : "qty"}>{qty(i.available_qty)}</span></td>}
              <td className="r num hide-mobile">{money(i.unit_price, cur)}</td>
              <td className="r num">{money(i.line_total, cur)}</td>
              {canSend && <td style={{ minWidth: 150 }}>{Number(i.remaining_qty) > 0 && (i.track_serials ? (
                <details onToggle={(e) => (e.target as HTMLDetailsElement).open && loadSerials(i)}>
                  <summary className="btn small">Pick serials ({serials[i.id]?.length || 0})</summary>
                  <div className="stack tight" style={{ marginTop: 6 }}>
                    {(serialOpts[i.id] || []).length === 0 ? <span className="meta">None in stock here</span> : serialOpts[i.id].map((sn) => (
                      <label key={sn} className="check"><input type="checkbox" checked={!!serials[i.id]?.includes(sn)}
                        onChange={(e) => setSerials((x) => ({ ...x, [i.id]: e.target.checked ? [...(x[i.id] || []), sn] : (x[i.id] || []).filter((v) => v !== sn) }))} />
                        <span className="num">{sn}</span></label>))}
                  </div>
                </details>
              ) : <input className="input num" inputMode="decimal" placeholder={`up to ${qty(i.remaining_qty)}`} aria-label={`Send ${i.product_name}`}
                  value={send[i.id] || ""} onChange={(e) => setSend((x) => ({ ...x, [i.id]: e.target.value }))} />)}</td>}
            </tr>))}</tbody>
        </table></div>
        <div className="pager"><span>{Number(so.discount) ? `Discount ${money(so.discount, cur)} · ` : ""}before tax {money(so.net, cur)} · tax {money(so.tax, cur)}</span><b className="num">Total {money(so.total, cur)}</b></div>
      </section>

      <div className="row">
        {canSend && <button className="btn go" disabled={busy} onClick={sendEntered}>Send</button>}
        {canSend && !hasSerials && <button className="btn" disabled={busy} onClick={() => act("fulfil", undefined)}>Send everything</button>}
        {can("sales.create") && so.status === "draft" && <>
          <button className="btn primary" disabled={busy} onClick={() => act("confirm", undefined, `${so.number} confirmed`)}>Confirm order</button>
          <button className="btn" onClick={() => setEditing(true)}>Edit</button>
        </>}
        {can("sales.create") && open && <button className="btn danger" disabled={busy} onClick={() => {
          const anySent = so.items.some((i: any) => Number(i.fulfilled_qty) > 0);
          if (confirm(anySent ? "Close this order without sending the rest?" : "Cancel this order?")) act("close", undefined, anySent ? "Order closed" : "Order cancelled");
        }}>{so.items.some((i: any) => Number(i.fulfilled_qty) > 0) ? "Close — don’t send the rest" : "Cancel order"}</button>}
      </div>

      {can("sales.create") && so.status !== "cancelled" && (
        <section className="panel panel-pad stack">
          <h2>Payment</h2>
          {Number(so.balance_due) > 0 ? (
            <div className="row" style={{ alignItems: "flex-end" }}>
              <Field label="Amount received" htmlFor="pay"><input id="pay" className="input num" inputMode="decimal" style={{ width: 180 }} value={pay} placeholder={String(Number(so.balance_due))} onChange={(e) => setPay(e.target.value)} /></Field>
              <button className="btn primary" disabled={busy || !pay} onClick={() => act("payments", { amount: pay }, "Payment recorded")}>Record payment</button>
              <button className="btn" disabled={busy} onClick={() => act("payments", {}, "Marked as paid")}>Mark fully paid</button>
            </div>
          ) : <p className="muted">Fully paid.{" "}<button className="btn link" onClick={() => { const a = prompt("Refund amount"); if (a) act("payments", { amount: `-${a}` }, "Refund recorded"); }}>Record a refund</button></p>}
        </section>
      )}

      {so.notes && <Notice tone="info">{so.notes}</Notice>}
      <Attachments entityType="sales_order" entityId={so.id} />

      <section className="panel">
        <div className="panel-head"><h2>Shipments</h2><span className="meta">Ordered {date(so.order_date)}{so.closed_at ? ` · closed ${dateTime(so.closed_at)}` : ""}</span></div>
        {so.movements?.length ? <MovementTable rows={so.movements} compact /> : <p className="panel-pad meta">Nothing sent yet.</p>}
      </section>

      {editing && <EditDrawer so={so} onClose={() => setEditing(false)} onSaved={(x) => { setEditing(false); setData({ ...x, movements: so.movements }); toast("Saved"); }} />}
    </div>
  );
}

function EditDrawer({ so, onClose, onSaved }: { so: any; onClose: () => void; onSaved: (so: any) => void }) {
  const { me } = useAuth();
  const [lines, setLines] = useState<OrderLine[]>(so.items.map((i: any) => ({ product_id: i.product_id, name: i.product_name, sku: i.sku, unit: i.unit,
    on_hand: "", quantity: String(Number(i.ordered_qty)), price: String(Number(i.unit_price)), tax_rate: String(Number(i.tax_rate)),
    discount_percent: String(Number(i.discount_percent)) })));
  const [err, setErr] = useState<string | null>(null);
  return (
    <Drawer title={`Edit ${so.number}`} open onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" onClick={async () => {
        try { onSaved(await api(`/sales-orders/${so.id}`, { method: "PUT", body: { lines: linesPayload(lines, "sale") } })); }
        catch (e) { setErr(errMsg(e)); }
      }}>Save</button></>}>
      <div className="stack">{err && <Notice tone="bad">{err}</Notice>}<LinesEditor kind="sale" lines={lines} onChange={setLines} currency={me?.organization.currency} /></div>
    </Drawer>
  );
}
