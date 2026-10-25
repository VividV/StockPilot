"use client";
import { useState } from "react";
import { Drawer, Field, Loading, Notice, PageHead, Pager, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, errMsg } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Page, Warehouse } from "@/lib/types";

const MODULES: { k: string; l: string; d: string }[] = [
  { k: "barcodes", l: "Barcodes", d: "Scan products with a phone camera or USB scanner." },
  { k: "batches", l: "Batch / lot numbers", d: "Record which batch each unit came from." },
  { k: "expiry", l: "Expiry dates", d: "Warn before stock expires and send the oldest out first." },
  { k: "serials", l: "Serial numbers", d: "Track individual units such as laptops, tools or equipment." },
  { k: "multi_location", l: "Shelf and bin locations", d: "Aisles, racks, shelves and bins inside a warehouse." },
  { k: "transfer_approval", l: "Approve transfers", d: "A manager approves before stock moves between locations." },
  { k: "variants", l: "Sizes and colours", d: "Offer “this product comes in different sizes or colours” when adding products." },
  { k: "production", l: "Production", d: "List the materials each product is made from, and record making products. Adds a Production page." },
  { k: "pos", l: "Counter sales (till)", d: "Sell at the counter from a phone, tablet or computer: scan, take cash, transfer or card, print or WhatsApp receipts, cash-up at closing. Works offline." },
];

const METHOD_CHOICES = [["cash", "Cash"], ["transfer", "Bank transfer"], ["card", "POS / card"], ["other", "Other"]] as const;

function timeZones(): string[] {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (Intl as any).supportedValuesOf("timeZone");
  } catch {
    return ["Africa/Lagos", "Africa/Accra", "Africa/Nairobi", "Africa/Johannesburg", "Europe/London", "UTC"];
  }
}

type User = { id: string; email: string; full_name: string; role_id: string; role: string; is_active: boolean; warehouse_ids: string[]; last_login_at: string | null; email_verified: boolean; totp_enabled: boolean };
type Role = { id: string; name: string; description: string | null; is_system: boolean; permissions: string[]; user_count: number };

export default function SettingsPage() {
  const { can } = useAuth();
  const tabs = [
    can("settings.manage") && { k: "org", l: "Business" },
    can("users.manage") && { k: "team", l: "Team" },
    can("users.manage") && { k: "roles", l: "Roles" },
    can("audit.view") && { k: "audit", l: "Activity log" },
    { k: "me", l: "My account" },
  ].filter(Boolean) as { k: string; l: string }[];
  const [tab, setTab] = useState(tabs[0].k);
  return (
    <>
      <PageHead title="Settings" />
      <div className="tabs" role="tablist">{tabs.map((t) => <button key={t.k} role="tab" className="tab" aria-selected={tab === t.k} onClick={() => setTab(t.k)}>{t.l}</button>)}</div>
      {tab === "org" && <OrgSettings />}
      {tab === "team" && <Team />}
      {tab === "roles" && <Roles />}
      {tab === "audit" && <Audit />}
      {tab === "me" && <MyAccount />}
    </>
  );
}

function OrgSettings() {
  const { me, reload } = useAuth();
  const toast = useToast();
  const o = me!.organization;
  const [name, setName] = useState(o.name);
  const [currency, setCurrency] = useState(o.currency);
  const [neg, setNeg] = useState(!!o.settings.allow_negative_stock);
  const [mods, setMods] = useState<Record<string, boolean>>({ ...(o.settings.modules || {}) });
  const [tz, setTz] = useState<string>((o.settings as { timezone?: string }).timezone || "UTC");
  const posIn = { prices_include_tax: true, payment_methods: ["cash", "transfer", "card"], max_discount_percent: 20, receipt_header: "",
                  receipt_footer: "Thank you for shopping with us!", paper_width: 80, ...((o.settings as { pos?: object }).pos || {}) };
  const [pos, setPos] = useState(posIn);
  const [err, setErr] = useState<string | null>(null);
  return (
    <form className="form-panel stack" onSubmit={async (e) => {
      e.preventDefault(); setErr(null);
      try {
        await api("/settings", { method: "PUT", body: { name, currency, allow_negative_stock: neg, modules: mods, timezone: tz,
                                                        pos: { ...pos, max_discount_percent: Number(pos.max_discount_percent) || 0 } } });
        await reload(); toast("Settings saved");
      }
      catch (e) { setErr(errMsg(e)); }
    }}>
      {err && <Notice tone="bad">{err}</Notice>}
      <div className="panel panel-pad stack">
        <div className="grid two">
          <Field label="Business name" htmlFor="on"><input id="on" className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Currency" htmlFor="oc"><input id="oc" className="input" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></Field>
        </div>
        <Field label="Time zone" htmlFor="otz" hint="Decides when “today” starts and ends in sales reports and on receipts.">
          <select id="otz" className="input" value={tz} onChange={(e) => setTz(e.target.value)}>
            {timeZones().map((z) => <option key={z} value={z}>{z.replace(/_/g, " ")}</option>)}
          </select>
        </Field>
      </div>
      <div className="panel panel-pad stack">
        <h2>Features</h2>
        {MODULES.map((m) => (
          <label key={m.k} className="check"><input type="checkbox" checked={!!mods[m.k]} onChange={(e) => setMods({ ...mods, [m.k]: e.target.checked })} />
            <span>{m.l}<span className="sub meta">{m.d}</span></span></label>
        ))}
      </div>
      {mods.pos && (
        <div className="panel panel-pad stack">
          <h2>Counter sales</h2>
          <div className="stack tight">
            <span className="label">Ways customers can pay</span>
            <div className="row">{METHOD_CHOICES.map(([k, l]) => (
              <label key={k} className="check"><input type="checkbox" checked={pos.payment_methods.includes(k)}
                onChange={(e) => setPos({ ...pos, payment_methods: e.target.checked ? [...pos.payment_methods, k] : pos.payment_methods.filter((x: string) => x !== k) })} /><span>{l}</span></label>
            ))}</div>
          </div>
          <label className="check"><input type="checkbox" checked={pos.prices_include_tax} onChange={(e) => setPos({ ...pos, prices_include_tax: e.target.checked })} />
            <span>Selling prices already include VAT<span className="sub meta">On (usual in shops): the price on the shelf is what the customer pays. Off: VAT is added at the till.</span></span></label>
          <Field label="Biggest discount a cashier may give (%)" htmlFor="pmd" hint="For staff allowed to give discounts. Owners and managers can give any discount.">
            <input id="pmd" className="input num" inputMode="decimal" style={{ maxWidth: 140 }} value={pos.max_discount_percent} onChange={(e) => setPos({ ...pos, max_discount_percent: e.target.value as unknown as number })} />
          </Field>
          <Field label="Receipt header" htmlFor="prh" hint="Address, phone number, RC number. One item per line.">
            <textarea id="prh" className="input" rows={3} value={pos.receipt_header} onChange={(e) => setPos({ ...pos, receipt_header: e.target.value })} />
          </Field>
          <Field label="Receipt footer" htmlFor="prf"><input id="prf" className="input" value={pos.receipt_footer} onChange={(e) => setPos({ ...pos, receipt_footer: e.target.value })} /></Field>
          <Field label="Receipt printer paper" htmlFor="ppw">
            <select id="ppw" className="input" style={{ maxWidth: 220 }} value={pos.paper_width} onChange={(e) => setPos({ ...pos, paper_width: Number(e.target.value) })}>
              <option value={80}>80 mm (most counter printers)</option><option value={58}>58 mm (small / Bluetooth printers)</option>
            </select>
          </Field>
        </div>
      )}
      <div className="panel panel-pad stack">
        <h2>Stock rules</h2>
        <label className="check"><input type="checkbox" checked={neg} onChange={(e) => setNeg(e.target.checked)} />
          <span>Allow selling more than the system shows<span className="sub meta">Off (recommended): staff can’t issue stock that isn’t recorded. On: stock can go below zero until it’s received. Batch and serial items never go below zero.</span></span></label>
      </div>
      <div><button className="btn primary">Save settings</button></div>
    </form>
  );
}

function Team() {
  const toast = useToast();
  const [page, setPage] = useState(1);
  const { data, reload } = useApi<Page<User>>("/users", { page, page_size: 25 });
  const { data: roles } = useApi<Role[]>("/roles");
  const { data: whs } = useApi<Warehouse[]>("/warehouses");
  const [edit, setEdit] = useState<User | "new" | null>(null);
  const whName = (id: string) => whs?.find((w) => w.id === id)?.name || "—";
  return (
    <>
      <div className="row end" style={{ marginBottom: 12 }}><button className="act" onClick={() => setEdit("new")}>Add team member</button></div>
      <div className="panel">
        {!data ? <Loading /> : (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Name</th><th>Role</th><th className="hide-mobile">Works in</th><th className="hide-mobile">Last sign-in</th></tr></thead>
              <tbody>{data.items.map((u) => (
                <tr key={u.id} className="click" onClick={() => setEdit(u)}>
                  <td><b>{u.full_name}</b><span className="sub">{u.email}{!u.is_active ? " · deactivated" : ""}{u.totp_enabled ? " · two-step on" : ""}{!u.email_verified ? " · email not confirmed" : ""}</span></td>
                  <td>{u.role}</td>
                  <td className="hide-mobile">{u.warehouse_ids.length ? u.warehouse_ids.map(whName).join(", ") : "All locations"}</td>
                  <td className="hide-mobile">{u.last_login_at ? dateTime(u.last_login_at) : "Never"}</td>
                </tr>))}</tbody>
            </table></div>
            <Pager page={page} pageSize={25} total={data.total} onPage={setPage} />
          </>
        )}
      </div>
      {edit && roles && whs && <UserDrawer user={edit === "new" ? null : edit} roles={roles} whs={whs} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); toast("Saved"); reload(); }} />}
    </>
  );
}

function UserDrawer({ user, roles, whs, onClose, onSaved }: { user: User | null; roles: Role[]; whs: Warehouse[]; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ full_name: user?.full_name || "", email: user?.email || "", password: "", role_id: user?.role_id || roles.find((r) => r.name === "Warehouse Staff")?.id || "",
    is_active: user ? user.is_active : true, reset_two_factor: false, restrict: !!user?.warehouse_ids.length, warehouse_ids: user?.warehouse_ids || [] as string[] });
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const role = roles.find((r) => r.id === f.role_id);
  return (
    <Drawer title={user ? user.full_name : "Add team member"} open onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" form="user-form">Save</button></>}>
      <form id="user-form" className="stack" onSubmit={async (e) => {
        e.preventDefault(); setErr(null);
        const warehouse_ids = f.restrict ? f.warehouse_ids : [];
        try {
          if (user) await api(`/users/${user.id}`, { method: "PUT", body: { full_name: f.full_name, role_id: f.role_id, is_active: f.is_active, warehouse_ids, password: f.password || undefined, reset_two_factor: f.reset_two_factor } });
          else await api("/users", { body: { full_name: f.full_name, email: f.email, password: f.password, role_id: f.role_id, warehouse_ids } });
          onSaved();
        } catch (e) { setErr(errMsg(e)); }
      }}>
        {err && <Notice tone="bad">{err}</Notice>}
        <Field label="Full name" htmlFor="un"><input id="un" className="input" required value={f.full_name} onChange={(e) => set("full_name", e.target.value)} /></Field>
        {!user && <Field label="Email" htmlFor="ue"><input id="ue" className="input" type="email" required value={f.email} onChange={(e) => set("email", e.target.value)} /></Field>}
        <Field label={user ? "New password (leave empty to keep)" : "Temporary password"} htmlFor="up" hint="At least 10 characters with upper- and lower-case letters and a number. Share it with them privately.">
          <input id="up" className="input" type="password" autoComplete="new-password" required={!user} value={f.password} onChange={(e) => set("password", e.target.value)} /></Field>
        <Field label="Role" htmlFor="ur"><select id="ur" className="input" value={f.role_id} onChange={(e) => set("role_id", e.target.value)}>
          {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Field>
        {role?.description && <p className="meta" style={{ marginTop: -8 }}>{role.description}</p>}
        <label className="check"><input type="checkbox" checked={f.restrict} onChange={(e) => set("restrict", e.target.checked)} /><span>Only certain locations<span className="sub meta">E.g. a shop assistant who should only see their own shop.</span></span></label>
        {f.restrict && <div className="stack tight" style={{ paddingLeft: 28 }}>{whs.map((w) => (
          <label key={w.id} className="check"><input type="checkbox" checked={f.warehouse_ids.includes(w.id)}
            onChange={(e) => set("warehouse_ids", e.target.checked ? [...f.warehouse_ids, w.id] : f.warehouse_ids.filter((x) => x !== w.id))} /><span>{w.name}</span></label>))}</div>}
        {user && <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => set("is_active", e.target.checked)} /><span>Can sign in<span className="sub meta">Untick when someone leaves. Their history is kept.</span></span></label>}
        {user?.totp_enabled && <label className="check"><input type="checkbox" checked={f.reset_two_factor} onChange={(e) => set("reset_two_factor", e.target.checked)} />
          <span>Reset two-step verification<span className="sub meta">For a lost or replaced phone. They can set it up again after signing in.</span></span></label>}
      </form>
    </Drawer>
  );
}

function Roles() {
  const { data } = useApi<Role[]>("/roles");
  const { data: perms } = useApi<{ code: string; description: string }[]>("/permissions");
  if (!data || !perms) return <Loading />;
  return (
    <div className="panel"><div className="table-wrap"><table className="t">
      <thead><tr><th>Can…</th>{data.map((r) => <th key={r.id} style={{ textAlign: "center" }}>{r.name}<span className="sub" style={{ fontWeight: 400 }}>{r.user_count} {r.user_count === 1 ? "person" : "people"}</span></th>)}</tr></thead>
      <tbody>{perms.map((p) => (
        <tr key={p.code}><td>{p.description}</td>{data.map((r) => (
          <td key={r.id} style={{ textAlign: "center" }}>{r.permissions.includes(p.code) ? <span aria-label="yes" className="chg-in">●</span> : <span aria-label="no" className="meta">·</span>}</td>))}</tr>))}</tbody>
    </table></div></div>
  );
}

type AuditRow = { id: number; created_at: string; user_name: string | null; action: string; summary: string | null; before: any; after: any; ip_address: string | null };
function Audit() {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const { data } = useApi<Page<AuditRow>>("/audit-logs", { page, page_size: 50, q });
  return (
    <>
      <input className="input" style={{ maxWidth: 320, marginBottom: 12 }} placeholder="Search the log" aria-label="Search the log" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      <div className="panel">{!data ? <Loading /> : (
        <>
          <div className="table-wrap"><table className="t">
            <thead><tr><th>When</th><th>Who</th><th>What</th><th className="hide-mobile">Change</th><th className="hide-mobile">From</th></tr></thead>
            <tbody>{data.items.map((a) => (
              <tr key={a.id}>
                <td className="num" style={{ whiteSpace: "nowrap" }}>{dateTime(a.created_at)}</td>
                <td>{a.user_name || "—"}</td>
                <td>{a.summary || a.action}<span className="sub">{a.action}</span></td>
                <td className="hide-mobile" style={{ fontSize: 13 }}>{a.before && a.after ? Object.keys(a.after).filter((k) => k !== "movement_ids").map((k) => (
                  <div key={k}><span className="meta">{k}:</span> {JSON.stringify(a.before[k] ?? "—")} → {JSON.stringify(a.after[k])}</div>)) : <span className="meta">—</span>}</td>
                <td className="hide-mobile meta">{a.ip_address || "—"}</td>
              </tr>))}</tbody>
          </table></div>
          <Pager page={page} pageSize={50} total={data.total} onPage={setPage} />
        </>
      )}</div>
    </>
  );
}

function MyAccount() {
  const { me } = useAuth();
  const toast = useToast();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="stack form-panel">
      <form className="panel panel-pad stack" onSubmit={async (e) => {
        e.preventDefault(); setErr(null);
        try { const r = await api("/auth/change-password", { body: { current_password: cur, new_password: next } }); toast(r.message); setCur(""); setNext(""); }
        catch (e) { setErr(errMsg(e)); }
      }}>
        <div><b>{me?.full_name}</b><p className="muted">{me?.email} · {me?.role}{me?.email_verified ? "" : " · email not confirmed yet"}</p></div>
        <h2>Password</h2>
        {err && <Notice tone="bad">{err}</Notice>}
        <Field label="Current password" htmlFor="cp"><input id="cp" className="input" type="password" autoComplete="current-password" required value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
        <Field label="New password" htmlFor="np" hint="At least 10 characters with upper- and lower-case letters and a number."><input id="np" className="input" type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} /></Field>
        <div><button className="btn primary">Change password</button></div>
      </form>
      <TwoStep />
      <AlertPrefs />
    </div>
  );
}

function TwoStep() {
  const { me, reload } = useAuth();
  const toast = useToast();
  const [setup, setSetup] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const on = me?.totp_enabled;
  return (
    <section className="panel panel-pad stack">
      <div className="spread"><h2>Two-step verification</h2><span className={`badge ${on ? "ok" : ""}`}>{on ? "On" : "Off"}</span></div>
      <p className="muted">Ask for a code from an authenticator app (Google Authenticator, Microsoft Authenticator, Authy…) as well as your password. Recommended for owners and administrators.</p>
      {err && <Notice tone="bad">{err}</Notice>}
      {codes ? (
        <div className="stack">
          <Notice tone="warn"><b>Save these recovery codes now.</b> Each one works once, if you lose your phone. They won’t be shown again.</Notice>
          <pre className="num" style={{ background: "var(--paper)", padding: 14, borderRadius: 8, margin: 0, fontSize: 15, lineHeight: 1.8 }}>{codes.join("\n")}</pre>
          <div className="row">
            <button className="btn" onClick={() => { navigator.clipboard?.writeText(codes.join("\n")); toast("Copied"); }}>Copy</button>
            <button className="btn primary" onClick={() => setCodes(null)}>I’ve saved them</button>
          </div>
        </div>
      ) : on ? (
        <form className="stack" onSubmit={async (e) => {
          e.preventDefault(); setErr(null);
          try { const r = await api("/auth/2fa/disable", { body: { password: pw, code } }); toast(r.message); setPw(""); setCode(""); reload(); } catch (e) { setErr(errMsg(e)); }
        }}>
          <div className="grid two">
            <Field label="Password" htmlFor="d-pw"><input id="d-pw" className="input" type="password" autoComplete="current-password" required value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
            <Field label="Code from the app" htmlFor="d-code"><input id="d-code" className="input num" inputMode="numeric" required value={code} onChange={(e) => setCode(e.target.value)} /></Field>
          </div>
          <div><button className="btn danger">Turn off</button></div>
        </form>
      ) : setup ? (
        <form className="stack" onSubmit={async (e) => {
          e.preventDefault(); setErr(null);
          try { const r = await api("/auth/2fa/enable", { body: { code } }); setCodes(r.recovery_codes); setSetup(null); setCode(""); reload(); toast(r.message); } catch (e) { setErr(errMsg(e)); }
        }}>
          <ol className="stack tight" style={{ margin: 0, paddingLeft: 20 }}>
            <li>Open your authenticator app and add an account.</li>
            <li>Scan this code with it.</li>
          </ol>
          <img alt="QR code to scan with your authenticator app" width={180} height={180} style={{ background: "#fff", padding: 8, borderRadius: 8, border: "1px solid var(--rule)" }}
               src={`data:image/svg+xml;base64,${typeof window !== "undefined" ? window.btoa(setup.qr_svg) : ""}`} />
          <p className="meta">Can’t scan? Type this key into the app: <span className="num" style={{ userSelect: "all" }}>{setup.secret.match(/.{1,4}/g)?.join(" ")}</span></p>
          <Field label="3. Type the 6-digit code the app shows" htmlFor="e-code">
            <input id="e-code" className="input num" style={{ maxWidth: 200, fontSize: 20, letterSpacing: 3 }} inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} /></Field>
          <div className="row"><button className="btn primary">Turn on</button><button type="button" className="btn" onClick={() => setSetup(null)}>Cancel</button></div>
        </form>
      ) : (
        <div><button className="btn primary" onClick={async () => { setErr(null); try { setSetup(await api("/auth/2fa/setup", { method: "POST" })); } catch (e) { setErr(errMsg(e)); } }}>Set up two-step verification</button></div>
      )}
    </section>
  );
}

type Prefs = { email: boolean; sms: boolean; whatsapp: boolean; phone: string | null; types: string[]; email_verified: boolean;
  channels: { email_configured: boolean; sms: boolean; whatsapp: boolean }; types_available: { key: string; label: string }[] };

function AlertPrefs() {
  const toast = useToast();
  const { data, setData } = useApi<Prefs>("/me/notifications");
  const [err, setErr] = useState<string | null>(null);
  if (!data) return null;
  const set = (patch: Partial<Prefs>) => setData({ ...data, ...patch });
  return (
    <form className="panel panel-pad stack" onSubmit={async (e) => {
      e.preventDefault(); setErr(null);
      try { setData(await api("/me/notifications", { method: "PUT", body: { email: data.email, sms: data.sms, whatsapp: data.whatsapp, phone: data.phone, types: data.types } })); toast("Alert settings saved"); }
      catch (e) { setErr(errMsg(e)); }
    }}>
      <h2>Alerts outside the app</h2>
      <p className="muted">Alerts always appear under the bell. You can also get them by email, text or WhatsApp. New alerts are bundled, so you get one message, not dozens.</p>
      {err && <Notice tone="bad">{err}</Notice>}
      <label className="check"><input type="checkbox" checked={data.email} onChange={(e) => set({ email: e.target.checked })} />
        <span>Email me<span className="sub meta">{!data.email_verified ? "Confirm your email address first (see the banner at the top)." : !data.channels.email_configured ? "Email sending isn’t set up on this server yet, so messages stay in the server log." : ""}</span></span></label>
      <label className="check"><input type="checkbox" disabled={!data.channels.sms} checked={data.sms} onChange={(e) => set({ sms: e.target.checked })} />
        <span>Text message (SMS){!data.channels.sms && <span className="sub meta">Not set up on this server</span>}</span></label>
      <label className="check"><input type="checkbox" disabled={!data.channels.whatsapp} checked={data.whatsapp} onChange={(e) => set({ whatsapp: e.target.checked })} />
        <span>WhatsApp{!data.channels.whatsapp && <span className="sub meta">Not set up on this server</span>}</span></label>
      {(data.sms || data.whatsapp) && <Field label="Mobile number with country code" htmlFor="ph"><input id="ph" className="input" type="tel" placeholder="+2348012345678" value={data.phone || ""} onChange={(e) => set({ phone: e.target.value })} /></Field>}
      <fieldset style={{ border: 0, padding: 0, margin: 0 }} className="stack tight">
        <legend className="label">Which alerts</legend>
        {data.types_available.map((t) => (
          <label key={t.key} className="check"><input type="checkbox" checked={data.types.includes(t.key)}
            onChange={(e) => set({ types: e.target.checked ? [...data.types, t.key] : data.types.filter((x) => x !== t.key) })} /><span>{t.label}</span></label>))}
      </fieldset>
      <div><button className="btn primary">Save alert settings</button></div>
    </form>
  );
}
