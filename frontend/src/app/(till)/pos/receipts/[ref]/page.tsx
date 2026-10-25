"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { I } from "@/components/icons";
import { Receipt, usePrinter } from "@/components/receipt";
import { Modal, TillBar, useOnline, usePending } from "@/components/till";
import { Field, Loading, Notice, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg, money as fmtMoney } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { type ReceiptData, type Shift, isNetworkError, receiptText, waNumber } from "@/lib/pos";

type SaleLine = ReceiptData["lines"][number] & { id: string; returnable_qty: number; track_serials: boolean; serials: string[] };
type Sale = Omit<ReceiptData, "lines"> & {
  id: string; lines: SaleLine[]; needs_review: boolean; review_note: string | null; offline: boolean; cost_of_goods?: number; profit?: number;
  returns?: { id: string; number: string; total: number; sold_at: string }[];
};

export default function ReceiptDetail() {
  const { ref } = useParams<{ ref: string }>();
  const { me, can } = useAuth();
  const online = useOnline();
  const { pending } = usePending();
  const toast = useToast();
  const router = useRouter();
  const { data: s, error, reload, setData } = useApi<Sale>(`/pos/sales/${ref}`);
  const [returning, setReturning] = useState(false);
  const [phone, setPhone] = useState("");
  const { print, host } = usePrinter();
  const cur = me!.organization.currency;
  const m = (n: number) => fmtMoney(n, cur);
  if (error) return <><TillBar title="Receipt" online={online} back={{ href: "/pos/receipts", label: "Receipts" }} /><div className="till-page"><Notice tone="bad">{error}</Notice></div></>;
  if (!s) return <><TillBar title="Receipt" online={online} back={{ href: "/pos/receipts", label: "Receipts" }} /><Loading /></>;
  const returnable = s.kind === "sale" && s.lines.some((l) => Number(l.returnable_qty) > 0);
  return (
    <>
      <TillBar title={s.number} sub={s.kind === "return" ? "Return" : "Sale"} online={online} pending={pending} back={{ href: "/pos/receipts", label: "Receipts" }} />
      <div className="till-page receipt-page">
        <div className="done-receipt"><Receipt r={s} /></div>
        <div className="stack">
          {s.needs_review && (
            <Notice tone="warn"><b>Needs checking.</b> {s.review_note}
              {can("pos.manage") && <div style={{ marginTop: 8 }}><button className="btn small" onClick={async () => { setData(await api<Sale>(`/pos/sales/${s.id}/reviewed`, { method: "POST" })); toast("Marked as checked"); }}>Mark as checked</button></div>}
            </Notice>
          )}
          {s.profit !== undefined && <div className="panel panel-pad spread"><span className="meta">Cost of goods {m(s.cost_of_goods || 0)}</span><b>Profit {m(s.profit)}</b></div>}
          <button className="btn block" onClick={() => print(<Receipt r={s} />, s.receipt.paper_width)}><I.printer />Print</button>
          <div className="panel panel-pad stack tight">
            <span className="label">Send to the customer</span>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input className="input" inputMode="tel" placeholder={s.customer_phone || "Phone number"} aria-label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
              <button className="btn small" disabled={!(phone || s.customer_phone)} onClick={() => window.open(`https://wa.me/${waNumber(phone || s.customer_phone || "", cur)}?text=${encodeURIComponent(receiptText(s, m))}`, "_blank", "noopener")}>WhatsApp</button>
            </div>
          </div>
          {returnable && can("pos.refund") && <button className="btn go block" onClick={() => setReturning(true)}>Take items back</button>}
          {s.returns && s.returns.length > 0 && (
            <div className="panel">
              <div className="panel-head"><h2>Returns</h2></div>
              {s.returns.map((r) => <Link key={r.id} href={`/pos/receipts/${r.id}`} className="locline" style={{ padding: "10px 18px", textDecoration: "none" }}><span className="num">{r.number}</span><span className="num">−{m(r.total)}</span></Link>)}
            </div>
          )}
        </div>
      </div>
      {returning && <ReturnSheet s={s} money={m} onClose={() => setReturning(false)} onDone={(id) => { setReturning(false); reload(); router.push(`/pos/receipts/${id}`); }} />}
      {host}
    </>
  );
}

function ReturnSheet({ s, money, onClose, onDone }: { s: Sale; money: (n: number) => string; onClose: () => void; onDone: (id: string) => void }) {
  const { data: shift } = useApi<Shift | null>("/pos/shifts/current");
  const { data: boot } = useApi<{ methods: { key: string; label: string }[] }>("/pos/bootstrap");
  const [qty, setQty] = useState<Record<string, string>>({});
  const [serials, setSerials] = useState<Record<string, string[]>>({});
  const [restock, setRestock] = useState<Record<string, boolean>>({});
  const [method, setMethod] = useState(s.payments.find((p) => Number(p.amount) > 0)?.method || "cash");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const lines = s.lines.filter((l) => Number(l.returnable_qty) > 0);
  const estimate = lines.reduce((sum, l) => {
    const n = l.track_serials ? (serials[l.id]?.length || 0) : Number(qty[l.id] || 0);
    return sum + (Number(l.total) / Number(l.quantity)) * n;
  }, 0);
  return (
    <Modal title={`Take back items from ${s.number}`} onClose={onClose} wide>
      <form className="stack" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true); setErr(null);
        try {
          if (!shift) throw new Error("Start a shift on the till first: refunds come out of a till.");
          const body = {
            shift_id: shift.id, original_sale_id: s.id, refund_method: method, reason: reason || null,
            lines: lines.map((l) => ({ original_item_id: l.id, quantity: l.track_serials ? 0 : Number(qty[l.id] || 0), serials: l.track_serials ? serials[l.id] || [] : undefined,
                                       restock: restock[l.id] ?? true }))
              .filter((l) => l.quantity > 0 || (l.serials && l.serials.length)),
          };
          if (!body.lines.length) throw new Error("Enter how many of each item are coming back.");
          const r = await api<{ id: string }>("/pos/returns", { body });
          onDone(r.id);
        } catch (e) { setErr(isNetworkError(e) ? "Returns need the internet." : errMsg(e)); }
        finally { setBusy(false); }
      }}>
        {err && <Notice tone="bad">{err}</Notice>}
        {shift === null && <Notice tone="warn">Start a shift on the till first: refunds come out of a till.</Notice>}
        <div className="panel"><table className="t"><tbody>
          {lines.map((l) => (
            <tr key={l.id}>
              <td>{l.name}<span className="sub">Bought {Number(l.quantity)} · paid {money(l.total)} · can return {Number(l.returnable_qty)}</span>
                <label className="check" style={{ marginTop: 6 }}><input type="checkbox" checked={restock[l.id] ?? true} onChange={(e) => setRestock({ ...restock, [l.id]: e.target.checked })} />
                  <span className="meta">Can be sold again</span></label></td>
              <td style={{ width: 140 }}>{l.track_serials ? (
                <div className="stack tight">{l.serials.map((sn) => (
                  <label key={sn} className="check"><input type="checkbox" checked={!!serials[l.id]?.includes(sn)}
                    onChange={(e) => setSerials({ ...serials, [l.id]: e.target.checked ? [...(serials[l.id] || []), sn] : (serials[l.id] || []).filter((x) => x !== sn) })} />
                    <span className="num">{sn}</span></label>))}</div>
              ) : (
                <input className="input num" inputMode="decimal" aria-label={`How many ${l.name} back`} placeholder={`0–${Number(l.returnable_qty)}`}
                       value={qty[l.id] || ""} onChange={(e) => setQty({ ...qty, [l.id]: e.target.value })} />
              )}</td>
            </tr>
          ))}
        </tbody></table></div>
        <div className="grid two">
          <Field label="Give the money back as" htmlFor="rm">
            <select id="rm" className="input" value={method} onChange={(e) => setMethod(e.target.value)}>
              {(boot?.methods || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
              <option value="credit">Store credit (for an exchange)</option>
            </select>
          </Field>
          <Field label="Reason (optional)" htmlFor="rr"><input id="rr" className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Wrong size" /></Field>
        </div>
        {method === "credit" && <p className="meta">The customer gets a return receipt. They pay with it at the till by choosing “Store credit” and typing its number.</p>}
        <button className="btn primary block" disabled={busy || !shift}>{busy ? "Saving…" : `Refund about ${money(estimate)}`}</button>
      </form>
    </Modal>
  );
}
