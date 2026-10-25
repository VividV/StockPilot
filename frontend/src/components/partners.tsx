"use client";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";
import { useApi, useDebounced } from "@/lib/hooks";
import type { Page, Partner } from "@/lib/types";
import { Drawer, Empty, Field, Loading, Notice, NoAccess, PageHead, Pager, useToast } from "./ui";

/** Suppliers and customers: same list + edit drawer. */
export function PartnerPage({ kind, title, singular, blurb }: { kind: "suppliers" | "customers"; title: string; singular: string; blurb: string }) {
  const { can } = useAuth();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [edit, setEdit] = useState<Partner | "new" | null>(null);
  const dq = useDebounced(q);
  const { data, reload } = useApi<Page<Partner>>(can(`${kind}.view`) ? `/${kind}` : null, { q: dq, page, page_size: 25, active: "" });
  if (!can(`${kind}.view`)) return <NoAccess />;
  const manage = can(`${kind}.manage`);

  return (
    <>
      <PageHead title={title} sub={blurb}>{manage && <>
        <Link className="btn" href={`/imports?type=${kind}`}>Import from Excel</Link>
        <button className="act" onClick={() => setEdit("new")}>Add {singular}</button></>}</PageHead>
      <input className="input" style={{ maxWidth: 320, marginBottom: 14 }} placeholder="Name, email or phone" aria-label={`Search ${title}`} value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      <div className="panel">
        {!data ? <Loading /> : data.items.length === 0 ? (
          <Empty title={q ? "No matches" : `No ${title.toLowerCase()} yet`} action={manage && !q ? <button className="btn primary" onClick={() => setEdit("new")}>Add {singular}</button> : undefined} />
        ) : (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Name</th><th className="hide-mobile">Contact</th><th>Phone</th><th className="hide-mobile">Email</th></tr></thead>
              <tbody>{data.items.map((p) => (
                <tr key={p.id} className={manage ? "click" : ""} onClick={() => manage && setEdit(p)}>
                  <td><b>{p.name}</b>{!p.is_active && <span className="sub">Inactive</span>}</td>
                  <td className="hide-mobile">{p.contact_name || "—"}</td>
                  <td className="num">{p.phone ? <a href={`tel:${p.phone}`} onClick={(e) => e.stopPropagation()}>{p.phone}</a> : "—"}</td>
                  <td className="hide-mobile">{p.email ? <a href={`mailto:${p.email}`} onClick={(e) => e.stopPropagation()}>{p.email}</a> : "—"}</td>
                </tr>))}</tbody>
            </table></div>
            <Pager page={page} pageSize={25} total={data.total} onPage={setPage} />
          </>
        )}
      </div>
      {edit && <PartnerDrawer kind={kind} singular={singular} item={edit === "new" ? null : edit} onClose={() => setEdit(null)}
        onSaved={(p) => { setEdit(null); toast(`Saved ${p.name}`); reload(); }} />}
    </>
  );
}

function PartnerDrawer({ kind, singular, item, onClose, onSaved }: { kind: string; singular: string; item: Partner | null; onClose: () => void; onSaved: (p: Partner) => void }) {
  const [f, setF] = useState({ name: item?.name || "", contact_name: item?.contact_name || "", email: item?.email || "", phone: item?.phone || "",
    address: item?.address || "", notes: item?.notes || "", is_active: item ? item.is_active : true });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const save = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      const body = { ...f, email: f.email || null };
      onSaved(await api<Partner>(item ? `/${kind}/${item.id}` : `/${kind}`, { method: item ? "PUT" : "POST", body }));
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <Drawer title={item ? item.name : `New ${singular}`} open onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" form="partner-form" disabled={busy}>Save</button></>}>
      <form id="partner-form" className="stack" onSubmit={save}>
        {err && <Notice tone="bad">{err}</Notice>}
        <Field label="Name" htmlFor="pn"><input id="pn" className="input" required autoFocus value={f.name} onChange={(e) => set("name", e.target.value)} /></Field>
        <Field label="Contact person" htmlFor="pc"><input id="pc" className="input" value={f.contact_name} onChange={(e) => set("contact_name", e.target.value)} /></Field>
        <div className="grid two">
          <Field label="Phone" htmlFor="pp"><input id="pp" className="input" type="tel" value={f.phone} onChange={(e) => set("phone", e.target.value)} /></Field>
          <Field label="Email" htmlFor="pe"><input id="pe" className="input" type="email" value={f.email} onChange={(e) => set("email", e.target.value)} /></Field>
        </div>
        <Field label="Address" htmlFor="pa"><textarea id="pa" className="input" value={f.address} onChange={(e) => set("address", e.target.value)} /></Field>
        <Field label="Notes" htmlFor="pno"><textarea id="pno" className="input" value={f.notes} onChange={(e) => set("notes", e.target.value)} /></Field>
        {item && <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => set("is_active", e.target.checked)} /><span>Active</span></label>}
      </form>
    </Drawer>
  );
}
