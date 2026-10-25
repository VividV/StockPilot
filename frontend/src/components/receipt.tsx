"use client";
import { useEffect, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import type { ReceiptData } from "@/lib/pos";

function fmt(n: number | string, currency: string) {
  const v = Number(n || 0);
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, currencyDisplay: "narrowSymbol", maximumFractionDigits: 2 }).format(v);
  } catch {
    return `${currency} ${v.toFixed(2)}`;
  }
}

function when(iso: string, tz: string) {
  try {
    return new Date(iso).toLocaleString(undefined, { timeZone: tz, day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return new Date(iso).toLocaleString();
  }
}

/** A till receipt. Looks like the paper one, on screen and when printed (58 or 80 mm). */
export function Receipt({ r }: { r: ReceiptData }) {
  const cur = r.receipt.currency;
  const m = (n: number | string) => fmt(n, cur);
  const isReturn = r.kind === "return";
  return (
    <div className={`receipt w${r.receipt.paper_width || 80}`}>
      <div className="rc-center">
        <div className="rc-biz">{r.receipt.business_name}</div>
        {r.receipt.header && r.receipt.header.split("\n").map((l, i) => <div key={i}>{l}</div>)}
        {!r.receipt.header && r.location_address && <div>{r.location_address}</div>}
      </div>
      <div className="rc-rule" />
      <div className="rc-row"><span>{isReturn ? "Return" : "Receipt"}</span><b>{r.number}</b></div>
      <div className="rc-row"><span>{when(r.sold_at, r.receipt.timezone)}</span><span>{r.location_name}</span></div>
      {r.cashier && <div className="rc-row"><span>Served by</span><span>{r.cashier}</span></div>}
      {r.customer_name && <div className="rc-row"><span>Customer</span><span>{r.customer_name}</span></div>}
      {r.original_sale && <div className="rc-row"><span>For receipt</span><span>{r.original_sale.number}</span></div>}
      <div className="rc-rule" />
      {r.lines.map((l, i) => (
        <div key={i} className="rc-item">
          <div className="rc-row"><span className="rc-name">{l.name}</span><span>{m(l.total)}</span></div>
          <div className="rc-sub">{Number(l.quantity)} × {m(l.unit_price)}</div>
          {Number(l.discount) > 0 && <div className="rc-sub">{l.promotion_name || "Discount"} −{m(l.discount)}</div>}
          {l.serials && l.serials.length > 0 && <div className="rc-sub">S/N {l.serials.join(", ")}</div>}
        </div>
      ))}
      <div className="rc-rule" />
      {Number(r.discount) > 0 && <div className="rc-row"><span>You saved</span><span>{m(r.discount)}</span></div>}
      {Number(r.tax) > 0 && <div className="rc-row"><span>{r.receipt.prices_include_tax ? "Includes tax" : "Tax"}</span><span>{m(r.tax)}</span></div>}
      <div className="rc-row rc-total"><span>{isReturn ? "REFUND" : "TOTAL"}</span><span>{m(r.total)}</span></div>
      {r.payments.map((p, i) => (
        <div key={i} className="rc-row"><span>{p.label}{p.reference ? ` (${p.reference})` : ""}</span><span>{m(Math.abs(Number(p.amount)))}</span></div>
      ))}
      {Number(r.change_given) > 0 && <div className="rc-row"><span>Change</span><span>{m(r.change_given)}</span></div>}
      {isReturn && Number(r.credit_remaining) > 0 && (
        <div className="rc-center rc-note">Store credit {m(r.credit_remaining || 0)}. Show receipt {r.number} to use it.</div>
      )}
      {r.pending && <div className="rc-center rc-note">Made offline. The receipt number will be confirmed when the till is back online.</div>}
      {r.receipt.footer && <><div className="rc-rule" /><div className="rc-center">{r.receipt.footer}</div></>}
      <div className="rc-center rc-small">Powered by StockPilot</div>
    </div>
  );
}

/** Mount once on a page; call the returned function to print a receipt (or any content) on the receipt printer.
 *  The content is put on the page synchronously before the print dialog opens, and stays (hidden on screen)
 *  until the browser says printing is finished, because some phones print in the background. */
export function usePrinter() {
  const [job, setJob] = useState<{ node: React.ReactNode; width: number } | null>(null);
  useEffect(() => {
    const done = () => setJob(null);
    window.addEventListener("afterprint", done);
    return () => window.removeEventListener("afterprint", done);
  }, []);
  const host = job && typeof document !== "undefined"
    ? createPortal(
      <div className="print-host">
        <style>{`@page { size: ${job.width}mm auto; margin: 2mm; }`}</style>
        {job.node}
      </div>, document.body)
    : null;
  const print = (node: React.ReactNode, width = 80) => {
    flushSync(() => setJob({ node, width }));
    window.print();
  };
  return { print, host };
}
