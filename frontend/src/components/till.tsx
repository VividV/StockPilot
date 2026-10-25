"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { Shift } from "@/lib/pos";
import { Notice } from "./ui";
import { useAuth } from "@/lib/auth";
import { outboxAll, syncOutbox } from "@/lib/pos";
import { I } from "./icons";

export function useOnline() {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    setOnline(navigator.onLine);
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, []);
  return online;
}

/** Sales on this device that haven't reached the server yet, and a way to send them. */
export function usePending() {
  const { me } = useAuth();
  const [pending, setPending] = useState(0);
  const [failed, setFailed] = useState(0);
  const refresh = useCallback(async () => {
    if (!me) return;
    const all = (await outboxAll()).filter((x) => x.user_id === me.id);
    setPending(all.filter((x) => x.status === "pending").length);
    setFailed(all.filter((x) => x.status === "failed").length);
  }, [me]);
  const sync = useCallback(async () => {
    if (!me) return null;
    const r = await syncOutbox(me.id);
    await refresh();
    return r;
  }, [me, refresh]);
  useEffect(() => {
    refresh();
    const t = setInterval(() => { if (navigator.onLine) sync(); }, 30_000);
    const on = () => sync();
    window.addEventListener("online", on);
    return () => { clearInterval(t); window.removeEventListener("online", on); };
  }, [refresh, sync]);
  return { pending, failed, refresh, sync };
}

export function TillBar({ title, sub, online, pending, back, children }: {
  title: string; sub?: React.ReactNode; online: boolean; pending?: number; back?: { href: string; label: string }; children?: React.ReactNode;
}) {
  return (
    <header className="till-bar">
      {back ? <Link href={back.href} className="till-back" aria-label={back.label}><I.back /><span className="hide-mobile">{back.label}</span></Link>
        : <Link href="/" className="till-back" aria-label="Back to StockPilot"><I.back /><span className="hide-mobile">StockPilot</span></Link>}
      <div className="till-title"><b>{title}</b>{sub ? <span>{sub}</span> : null}</div>
      <div className="till-status">
        {!online && <span className="pill off"><I.cloudOff size={15} />Offline</span>}
        {!!pending && <span className="pill wait" title="Sales saved on this device, waiting to upload">{pending} to upload</span>}
        {children}
      </div>
    </header>
  );
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="drawer-head"><h2>{title}</h2><button className="btn small" onClick={onClose} aria-label="Close"><I.x /></button></div>
        <div className="modal-body">{children}</div>
      </div>
    </>
  );
}


export function ShiftReport({ s, money }: { s: Shift; money: (n: number) => string }) {
  const sm = s.summary!;
  return (
    <div className="stack shift-report money-stats">
      <div className="meta">{s.location_name} · {s.user_name} · opened {new Date(s.opened_at).toLocaleString()}{s.closed_at ? ` · closed ${new Date(s.closed_at).toLocaleString()}` : ""}</div>
      <div className="stats">
        <div className="stat"><div className="k">Net sales</div><div className="v qty">{money(sm.net_sales)}</div></div>
        <div className="stat"><div className="k">Sales</div><div className="v qty">{sm.sales_count}</div></div>
        <div className="stat"><div className="k">Returns</div><div className="v qty">{money(sm.returns_total)}</div></div>
      </div>
      <table className="t"><tbody>
        {sm.methods.map((x) => <tr key={x.method}><td>{x.label}</td><td className="r num">{money(x.net)}</td></tr>)}
        {sm.discounts > 0 && <tr><td>Discounts given</td><td className="r num">{money(sm.discounts)}</td></tr>}
      </tbody></table>
      <h3>Cash drawer</h3>
      <table className="t"><tbody>
        <tr><td>Cash at start</td><td className="r num">{money(sm.opening_float)}</td></tr>
        <tr><td>Cash sales</td><td className="r num">+{money(sm.cash_sales)}</td></tr>
        {sm.cash_refunds > 0 && <tr><td>Cash refunds</td><td className="r num">−{money(sm.cash_refunds)}</td></tr>}
        {sm.cash_movements.map((c) => <tr key={c.id}><td>{c.kind === "in" ? "Cash in" : "Cash out"}: {c.reason}</td><td className="r num">{c.kind === "in" ? "+" : "−"}{money(c.amount)}</td></tr>)}
        <tr><td><b>Should be in the drawer</b></td><td className="r num"><b>{money(s.status === "closed" && s.expected_cash_at_close !== null ? s.expected_cash_at_close : sm.expected_cash)}</b></td></tr>
        {s.counted_cash !== null && <tr><td>Counted</td><td className="r num">{money(s.counted_cash)}</td></tr>}
        {s.difference !== null && <tr><td>{Number(s.difference) < 0 ? "Short" : Number(s.difference) > 0 ? "Over" : "Difference"}</td>
          <td className={`r num ${Number(s.difference) < 0 ? "bad-text" : Number(s.difference) > 0 ? "warn-text" : "ok-text"}`}>{money(Math.abs(Number(s.difference)))}</td></tr>}
      </tbody></table>
      {s.closing_note && <p className="meta">Note: {s.closing_note}</p>}
      {sm.needs_review > 0 && <Notice tone="warn">{sm.needs_review} sale(s) on this shift need checking by a manager.</Notice>}
      {sm.synced_after_close > 0 && <Notice tone="info">{sm.synced_after_close} offline sale(s) were uploaded after this shift closed and are included above.</Notice>}
    </div>
  );
}

