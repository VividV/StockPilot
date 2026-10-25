"use client";
import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { qty as fq } from "@/lib/format";
import { I } from "./icons";

// ---------------------------------------------------------------- toasts
type Toast = { id: number; text: string; tone?: "bad" };
const ToastCtx = createContext<(text: string, tone?: "bad") => void>(() => {});

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone?: "bad") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, tone }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), tone ? 7000 : 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast ${t.tone || ""}`}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ---------------------------------------------------------------- small pieces
export function Qty({ v, unit, className = "" }: { v: string | number | null | undefined; unit?: string; className?: string }) {
  return <span className={`qty ${className}`}>{fq(v)}{unit ? <span className="u">{unit}</span> : null}</span>;
}

export function Change({ v, unit }: { v: string; unit?: string }) {
  const n = Number(v);
  return <span className={`qty ${n > 0 ? "chg-in" : "chg-out"}`}>{n > 0 ? "+" : "−"}{fq(Math.abs(n))}{unit ? <span className="u">{unit}</span> : null}</span>;
}

export function StockBadge({ status }: { status: string | null }) {
  if (status === "out") return <span className="badge bad"><span className="dot" />Out of stock</span>;
  if (status === "low") return <span className="badge warn"><span className="dot" />Low</span>;
  return <span className="badge ok"><span className="dot" />In stock</span>;
}

export function ExpiryBadge({ days }: { days: number | null }) {
  if (days === null) return <span className="badge">No expiry</span>;
  if (days < 0) return <span className="badge bad">Expired {Math.abs(days)}d ago</span>;
  if (days <= 7) return <span className="badge bad">{days === 0 ? "Expires today" : `${days} days left`}</span>;
  if (days <= 30) return <span className="badge warn">{days} days left</span>;
  if (days <= 90) return <span className="badge info">{days} days left</span>;
  return <span className="badge ok">{days} days left</span>;
}

export function PageHead({ title, sub, children }: { title: string; sub?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="page-head">
      <div><h1>{title}</h1>{sub ? <p className="muted">{sub}</p> : null}</div>
      {children ? <div className="row">{children}</div> : null}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "ok" | "bad" | "warn"; children: React.ReactNode }) {
  return <div className={`notice ${tone}`} role={tone === "bad" ? "alert" : undefined}>{children}</div>;
}

export function Empty({ title, text, action }: { title: string; text?: string; action?: React.ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{text ? <p>{text}</p> : null}{action}</div>;
}

export function Loading() {
  return <div className="empty"><p className="muted">Loading…</p></div>;
}

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return total ? <div className="pager"><span>{total} {total === 1 ? "row" : "rows"}</span></div> : null;
  return (
    <div className="pager">
      <span>{(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}</span>
      <div className="row">
        <button className="btn small" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
        <button className="btn small" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
      </div>
    </div>
  );
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: string; children: React.ReactNode; htmlFor?: string }) {
  return <div className="field"><label htmlFor={htmlFor}>{label}</label>{children}{hint ? <span className="hint">{hint}</span> : null}</div>;
}

export function Drawer({ title, open, onClose, children, footer }: { title: string; open: boolean; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title}>
        <div className="drawer-head"><h2>{title}</h2><button className="btn small" onClick={onClose} aria-label="Close"><I.x /></button></div>
        <div className="drawer-body">{children}</div>
        {footer ? <div className="drawer-foot">{footer}</div> : null}
      </aside>
    </>
  );
}

/** Hide UI the user is not allowed to use. The API enforces it regardless. */
export function Can({ perm, children }: { perm: string; children: React.ReactNode }) {
  const { can } = useAuth();
  return can(perm) ? <>{children}</> : null;
}

export function NoAccess() {
  return <Empty title="You don't have access to this page" text="Ask an owner or administrator if you need it." action={<Link className="btn" href="/">Go to dashboard</Link>} />;
}
