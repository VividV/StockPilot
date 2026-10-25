"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useDebounced } from "@/lib/hooks";
import { I, Mark } from "./icons";

const NAV: { href: string; label: string; icon: (p: { size?: number }) => React.ReactElement; perm: string | null; module?: string; billing?: boolean }[] = [
  { href: "/pos", label: "Sell (till)", icon: I.till, perm: "pos.sell", module: "pos" },
  { href: "/", label: "Dashboard", icon: I.dashboard, perm: "inventory.view" },
  { href: "/today", label: "Sales today", icon: I.trend, perm: "reports.view", module: "pos" },
  { href: "/inventory", label: "Inventory", icon: I.inventory, perm: "inventory.view" },
  { href: "/products", label: "Products", icon: I.products, perm: "products.view" },
  { href: "/purchases", label: "Purchases", icon: I.purchases, perm: "purchases.view" },
  { href: "/sales", label: "Sales", icon: I.sales, perm: "sales.view" },
  { href: "/transfers", label: "Transfers", icon: I.transfers, perm: "inventory.view" },
  { href: "/production", label: "Production", icon: I.production, perm: "production.run", module: "production" },
  { href: "/warehouses", label: "Warehouses", icon: I.warehouses, perm: "locations.view" },
  { href: "/suppliers", label: "Suppliers", icon: I.suppliers, perm: "suppliers.view" },
  { href: "/customers", label: "Customers", icon: I.customers, perm: "customers.view" },
  { href: "/promotions", label: "Promotions", icon: I.tag, perm: "promotions.manage", module: "pos" },
  { href: "/reports", label: "Reports", icon: I.reports, perm: "reports.view" },
  { href: "/billing", label: "Subscription", icon: I.card, perm: "billing.manage", billing: true },
  { href: "/settings", label: "Settings", icon: I.settings, perm: null },
];

export const QUICK: { href: string; label: string; icon: (p: { size?: number }) => React.ReactElement; perm: string; module?: string }[] = [
  { href: "/pos", label: "Sell (open the till)", icon: I.till, perm: "pos.sell", module: "pos" },
  { href: "/receive", label: "Receive stock", icon: I.receive, perm: "inventory.receive" },
  { href: "/issue", label: "Issue stock", icon: I.issue, perm: "inventory.issue" },
  { href: "/transfer", label: "Transfer stock", icon: I.transfers, perm: "inventory.transfer" },
  { href: "/products/new", label: "Add product", icon: I.plus, perm: "products.create" },
  { href: "/adjust", label: "Adjustment", icon: I.adjust, perm: "inventory.adjust" },
];

function active(path: string, href: string) {
  return href === "/" ? path === "/" : path === href || path.startsWith(href + "/");
}

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [res, setRes] = useState<{ type: string; id: string; title: string; subtitle: string; url: string }[]>([]);
  const dq = useDebounced(q, 220);
  const router = useRouter();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (dq.trim().length < 1) { setRes([]); return; }
    api("/search", { query: { q: dq } }).then((r) => setRes(r.results)).catch(() => setRes([]));
  }, [dq]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "/" && !(e.target as HTMLElement).closest("input,textarea,select")) {
        e.preventDefault();
        box.current?.querySelector("input")?.focus();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  return (
    <div className="search" ref={box} onBlur={(e) => !box.current?.contains(e.relatedTarget as Node) && setOpen(false)}>
      <I.search />
      <input
        type="search" placeholder="Search products, SKU, barcode, batch, serial, supplier…" aria-label="Search everything"
        value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && res[0]) { router.push(res[0].url); setOpen(false); setQ(""); }
          if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && q && (
        <div className="search-results">
          {res.length === 0 ? <div style={{ padding: 14 }} className="meta">Nothing matches “{q}”.</div> :
            res.map((r) => (
              <Link key={r.type + r.id} href={r.url} onClick={() => { setOpen(false); setQ(""); }}>
                <b>{r.title}</b><span className="sub meta">{r.subtitle}</span>
              </Link>
            ))}
        </div>
      )}
    </div>
  );
}

function Bell() {
  const [n, setN] = useState(0);
  const path = usePathname();
  useEffect(() => {
    let live = true;
    const load = () => api("/notifications/unread-count").then((r) => live && setN(r.unread)).catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, [path]);
  return (
    <Link href="/notifications" className="btn small" aria-label={`Alerts${n ? `, ${n} unread` : ""}`} style={{ position: "relative" }}>
      <I.bell />
      {n > 0 && <span className="badge bad" style={{ position: "absolute", top: -8, right: -8, padding: "0 6px" }}>{n > 99 ? "99+" : n}</span>}
    </Link>
  );
}

type BillingState = { enabled: boolean; status?: string; days_left?: number | null; locked?: boolean; plan_name?: string };

function useBilling() {
  const [b, setB] = useState<BillingState | null>(null);
  const path = usePathname();
  useEffect(() => { api<BillingState>("/billing").then(setB).catch(() => {}); }, [path === "/billing"]); // eslint-disable-line react-hooks/exhaustive-deps
  return b;
}

function BillingBanner({ b }: { b: BillingState | null }) {
  const { can } = useAuth();
  const path = usePathname();
  if (!b?.enabled || path === "/billing") return null;
  const manage = can("billing.manage");
  if (b.locked) {
    return <div className="notice bad spread" style={{ marginBottom: 18 }}><span><b>Your StockPilot subscription has ended.</b> You can still look at everything, but changes and new sales are paused.</span>
      {manage ? <Link className="btn small" href="/billing">Choose a plan</Link> : <span className="meta">Ask the owner to renew.</span>}</div>;
  }
  if (b.status === "past_due") {
    return <div className="notice warn spread" style={{ marginBottom: 18 }}><span>The last payment for StockPilot didn’t go through.</span>{manage && <Link className="btn small" href="/billing">Pay now</Link>}</div>;
  }
  if (b.status === "trialing" && manage && b.days_left !== null && b.days_left !== undefined && b.days_left <= 7) {
    return <div className="notice info spread" style={{ marginBottom: 18 }}><span>Your free trial ends in <b>{b.days_left} day{b.days_left !== 1 ? "s" : ""}</b>.</span><Link className="btn small" href="/billing">Choose a plan</Link></div>;
  }
  return null;
}

function OfflineBanner() {
  const { offline } = useAuth();
  if (!offline) return null;
  return <div className="notice warn spread" style={{ marginBottom: 18 }}><span>You’re offline. Only the till works without the internet; it keeps sales on this device and uploads them later.</span><Link className="btn small" href="/pos">Open the till</Link></div>;
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { me, can, mod, signOut } = useAuth();
  const path = usePathname();
  const [sheet, setSheet] = useState(false);
  const billing = useBilling();
  useEffect(() => setSheet(false), [path]);
  const nav = NAV.filter((n) => (!n.perm || can(n.perm)) && (!n.module || mod(n.module)) && (!n.billing || billing?.enabled));
  const quick = QUICK.filter((q) => can(q.perm) && (!q.module || mod(q.module)));

  return (
    <div className="shell">
      <nav className="side" aria-label="Main">
        <Link href="/" className="brand"><span className="brand-mark"><Mark /></span><span className="brand-name">StockPilot</span></Link>
        <div className="org-name" title={me?.organization.name}>{me?.organization.name}</div>
        {nav.map((n) => (
          <Link key={n.href} href={n.href} className={`nav-link ${n.href === "/pos" ? "nav-sell" : ""}`} aria-current={active(path, n.href) ? "page" : undefined}>
            <n.icon />{n.label}
          </Link>
        ))}
        <div className="side-foot">
          <div style={{ padding: "4px 10px 10px" }}>
            <div style={{ color: "#fff", fontWeight: 600, fontSize: 14 }}>{me?.full_name}</div>
            <div style={{ fontSize: 12.5, color: "#9fb0c6" }}>{me?.role}</div>
          </div>
          <button className="nav-link" style={{ width: "100%", background: "none", border: "none", cursor: "pointer" }} onClick={signOut}>
            <I.logout />Sign out
          </button>
        </div>
      </nav>

      <div className="main">
        <header className="topbar">
          <Link href="/" className="brand only-mobile" style={{ padding: 0 }} aria-label="StockPilot home"><span className="brand-mark"><Mark /></span></Link>
          <GlobalSearch />
          <Link href="/scan" className="btn small" aria-label="Scan a barcode"><I.scan /><span className="hide-mobile">Scan</span></Link>
          <Bell />
        </header>
        <main className="content" id="main"><OfflineBanner /><BillingBanner b={billing} /><VerifyBanner />{children}</main>
      </div>

      {/* phone navigation: the stock actions sit under the thumb */}
      <nav className="tabbar" aria-label="Main">
        <Link href="/" aria-current={path === "/" ? "page" : undefined}><I.dashboard />Home</Link>
        <Link href="/inventory" aria-current={active(path, "/inventory") ? "page" : undefined}><I.inventory />Stock</Link>
        <button onClick={() => setSheet(true)} aria-label="Stock actions"><span className="plus"><I.plus size={24} /></span></button>
        <Link href="/products" aria-current={active(path, "/products") ? "page" : undefined}><I.products />Products</Link>
        <Link href="/more" aria-current={path === "/more" ? "page" : undefined}><I.more />More</Link>
      </nav>
      {sheet && (
        <>
          <div className="scrim" onClick={() => setSheet(false)} />
          <div className="fab-sheet" role="dialog" aria-label="Stock actions">
            {quick.map((q) => <Link key={q.href} href={q.href} className="act"><q.icon />{q.label}</Link>)}
            <Link href="/scan" className="act secondary"><I.scan />Scan a barcode</Link>
          </div>
        </>
      )}
    </div>
  );
}

export function MoreMenu() {
  const { can, mod, signOut } = useAuth();
  const billing = useBilling();
  return (
    <div className="panel">
      {NAV.filter((n) => !["/", "/inventory", "/products"].includes(n.href) && (!n.perm || can(n.perm)) && (!n.module || mod(n.module)) && (!n.billing || billing?.enabled)).map((n) => (
        <Link key={n.href} href={n.href} className="locline" style={{ padding: "14px 18px", textDecoration: "none", justifyContent: "flex-start", gap: 12 }}>
          <n.icon />{n.label}
        </Link>
      ))}
      <button className="locline btn link" style={{ padding: "14px 18px", width: "100%", justifyContent: "flex-start", gap: 12 }} onClick={signOut}><I.logout />Sign out</button>
    </div>
  );
}

function VerifyBanner() {
  const { me } = useAuth();
  const [msg, setMsg] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);
  if (!me || me.email_verified || hidden) return null;
  return (
    <div className="notice info spread" style={{ marginBottom: 18 }}>
      <span>{msg || <>Please confirm your email address <b>{me.email}</b> using the link we sent. It lets you reset your password and get alerts by email.</>}</span>
      <span className="row" style={{ gap: 6 }}>
        {!msg && <button className="btn small" onClick={async () => { try { setMsg((await api("/auth/resend-verification", { method: "POST" })).message); } catch (e) { setMsg(e instanceof Error ? e.message : "Couldn’t send"); } }}>Send the link again</button>}
        <button className="btn small" aria-label="Hide" onClick={() => setHidden(true)}><I.x /></button>
      </span>
    </div>
  );
}
