"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { MovementTable } from "@/components/movements";
import { LinesEditor, linesPayload, ORDER_STATUS, type OrderLine } from "@/components/orderlines";
import { Attachments } from "@/components/attachments";
import { Drawer, Field, Loading, Notice, Qty, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, dateTime, errMsg, money, qty } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type Rcv = { quantity: string; batch_number: string; expires_on: string; serials: string };

export default function PurchaseDetail() {
  const { id } = useParams<{ id: string }>();
  const { can, me } = useAuth();
  const toast = useToast();
  const { data: po, setData, reload } = useApi<any>(`/purchase-orders/${id}`);
  const [rcv, setRcv] = useState<Record<string, Rcv>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  if (!po) return <Loading />;
  const cur = me?.organization.currency;
  const open = ["draft", "confirmed", "partial"].includes(po.status);
  const canReceive = open && can("purchases.receive") && can("inventory.receive");
  const needsDetail = po.items.some((i: any) => i.track_batches || i.track_serials);
  const s = ORDER_STATUS[po.status];
  const r = (iid: string): Rcv => rcv[iid] || { quantity: "", batch_number: "", expires_on: "", serials: "" };
  const setR = (iid: string, k: keyof Rcv, v: string) => setRcv((x) => ({ ...x, [iid]: { ...r(iid), [k]: v } }));

  const act = async (path: string, body?: unknown, ok?: string) => {
    setBusy(true); setErr(null);
    try {
      const out = await api(`/purchase-orders/${po.id}/${path}`, { method: "POST", body });
      toast(out.message || ok || "Saved");
      setRcv({});
      reload();
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  const receiveEntered = () => {
    const lines = po.items.filter((i: any) => r(i.id).quantity || r(i.id).serials).map((i: any) => {
      const x = r(i.id);
      const serials = x.serials.split(/[\n,]+/).map((v) => v.trim()).filter(Boolean);
      return { item_id: i.id, quantity: i.track_serials ? serials.length : x.quantity, batch_number: x.batch_number || undefined,
               expires_on: x.expires_on || undefined, serials: i.track_serials ? serials : undefined };
    });
    if (!lines.length) return setErr("Enter how many arrived for at least one line.");
    act("receive", { lines });
  };

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <p className="meta"><Link href="/purchases">Purchases</Link></p>
          <h1 style={{ marginTop: 4 }} className="num">{po.number}</h1>
          <p className="muted" style={{ marginTop: 4 }}>{po.supplier_name} → {po.destination_name}</p>
        </div>
        <div className="row">
          <span className={`badge ${s.tone}`} style={{ fontSize: 14, padding: "4px 12px" }}>{s.label}</span>
          {po.overdue && <span className="badge bad" style={{ fontSize: 14, padding: "4px 12px" }}>Late</span>}
        </div>
      </div>
      {err && <Notice tone="bad">{err}</Notice>}

      <div className="stats">
        <div className="stat"><div className="k">Order total</div><div className="v qty" style={{ fontSize: 22 }}>{money(po.total, cur)}</div></div>
        <div className="stat"><div className="k">Received so far</div><div className="v qty" style={{ fontSize: 22 }}>{money(po.received_value, cur)}</div></div>
        <div className="stat"><div className="k">Still to come</div><div className="v qty" style={{ fontSize: 22 }}>{money(po.outstanding_value, cur)}</div></div>
        <div className="stat"><div className="k">Expected</div><div className="v" style={{ fontSize: 18 }}>{po.expected_date ? date(po.expected_date) : "Not set"}</div></div>
      </div>

      <section className="panel">
        <div className="panel-head"><h2>Products</h2>{canReceive && <span className="meta">Type what arrived, then press Receive</span>}</div>
        <div className="table-wrap"><table className="t">
          <thead><tr><th>Product</th><th className="r">Ordered</th><th className="r">Received</th><th className="r">Still to come</th>
            <th className="r hide-mobile">Cost each</th>{canReceive && <th>Arrived now</th>}</tr></thead>
          <tbody>{po.items.map((i: any) => (
            <tr key={i.id}>
              <td><Link href={`/products/${i.product_id}`}>{i.product_name}</Link><span className="sub">{i.sku}</span></td>
              <td className="r"><Qty v={i.ordered_qty} unit={i.unit} /></td>
              <td className="r num">{qty(i.received_qty)}</td>
              <td className="r num">{Number(i.backorder_qty) ? <b>{qty(i.backorder_qty)}</b> : "—"}</td>
              <td className="r num hide-mobile">{money(i.unit_cost, cur)}</td>
              {canReceive && <td style={{ minWidth: 200 }}>{Number(i.backorder_qty) > 0 && (
                <div className="stack tight">
                  {i.track_serials
                    ? <textarea className="input num" rows={2} placeholder="Scan serial numbers, one per line" aria-label={`Serials for ${i.product_name}`} value={r(i.id).serials} onChange={(e) => setR(i.id, "serials", e.target.value)} />
                    : <input className="input num" inputMode="decimal" placeholder={`up to ${qty(i.backorder_qty)}`} aria-label={`Quantity of ${i.product_name} arrived`} value={r(i.id).quantity} onChange={(e) => setR(i.id, "quantity", e.target.value)} />}
                  {i.track_batches && <>
                    <input className="input" placeholder="Batch / lot number" aria-label={`Batch for ${i.product_name}`} value={r(i.id).batch_number} onChange={(e) => setR(i.id, "batch_number", e.target.value)} />
                    {i.track_expiry && <input className="input" type="date" aria-label={`Expiry for ${i.product_name}`} value={r(i.id).expires_on} onChange={(e) => setR(i.id, "expires_on", e.target.value)} />}
                  </>}
                </div>)}</td>}
            </tr>))}</tbody>
        </table></div>
        <div className="pager"><span>Before tax {money(po.subtotal, cur)} · tax {money(po.tax, cur)}</span><b className="num">Total {money(po.total, cur)}</b></div>
      </section>

      <div className="row">
        {canReceive && <button className="btn go" disabled={busy} onClick={receiveEntered}>Receive</button>}
        {canReceive && !needsDetail && <button className="btn" disabled={busy} onClick={() => act("receive", undefined)}>Everything arrived</button>}
        {can("purchases.create") && po.status === "draft" && <>
          <button className="btn primary" disabled={busy} onClick={() => act("confirm", undefined, `${po.number} confirmed`)}>Confirm order</button>
          <button className="btn" onClick={() => setEditing(true)}>Edit</button>
        </>}
        {can("purchases.create") && open && <button className="btn danger" disabled={busy} onClick={() => {
          const anyIn = po.items.some((i: any) => Number(i.received_qty) > 0);
          if (confirm(anyIn ? "Stop waiting for the rest of this order? What already arrived stays in stock." : "Cancel this order?"))
            act("close", undefined, anyIn ? "Order closed" : "Order cancelled");
        }}>{po.items.some((i: any) => Number(i.received_qty) > 0) ? "Close — rest won’t come" : "Cancel order"}</button>}
      </div>

      {(po.supplier_reference || po.notes) && <Notice tone="info">{po.supplier_reference && <>Supplier ref: <b>{po.supplier_reference}</b>. </>}{po.notes}</Notice>}

      <Attachments entityType="purchase_order" entityId={po.id} />

      <section className="panel">
        <div className="panel-head"><h2>Deliveries</h2><span className="meta">Ordered {date(po.order_date)}{po.confirmed_at ? ` · confirmed ${dateTime(po.confirmed_at)}` : ""}{po.closed_at ? ` · closed ${dateTime(po.closed_at)}` : ""}</span></div>
        {po.movements?.length ? <MovementTable rows={po.movements} compact /> : <p className="panel-pad meta">Nothing received yet.</p>}
      </section>

      {editing && <EditDrawer po={po} onClose={() => setEditing(false)} onSaved={(x) => { setEditing(false); setData({ ...x, movements: po.movements }); toast("Saved"); }} />}
    </div>
  );
}

function EditDrawer({ po, onClose, onSaved }: { po: any; onClose: () => void; onSaved: (po: any) => void }) {
  const { me } = useAuth();
  const [lines, setLines] = useState<OrderLine[]>(po.items.map((i: any) => ({ product_id: i.product_id, name: i.product_name, sku: i.sku,
    unit: i.unit, on_hand: "", quantity: String(Number(i.ordered_qty)), price: String(Number(i.unit_cost)), tax_rate: String(Number(i.tax_rate)), discount_percent: "0" })));
  const [expected, setExpected] = useState(po.expected_date || "");
  const [err, setErr] = useState<string | null>(null);
  return (
    <Drawer title={`Edit ${po.number}`} open onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" onClick={async () => {
        try { onSaved(await api(`/purchase-orders/${po.id}`, { method: "PUT", body: { expected_date: expected || null, lines: linesPayload(lines, "purchase") } })); }
        catch (e) { setErr(errMsg(e)); }
      }}>Save</button></>}>
      <div className="stack">
        {err && <Notice tone="bad">{err}</Notice>}
        <Field label="Expected delivery" htmlFor="ee"><input id="ee" type="date" className="input" value={expected} onChange={(e) => setExpected(e.target.value)} /></Field>
        <LinesEditor kind="purchase" lines={lines} onChange={setLines} currency={me?.organization.currency} />
      </div>
    </Drawer>
  );
}
