"use client";
import Link from "next/link";
import { useState } from "react";
import { Drawer, Field, Loading, Notice, NoAccess, PageHead, Qty, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { WAREHOUSE_TYPES, errMsg, money } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Warehouse } from "@/lib/types";

type Loc = { id: string; name: string; code: string; kind: string; parent_id: string | null; is_active: boolean; full_name: string };

export default function WarehousesPage() {
  const { can, me, mod } = useAuth();
  const toast = useToast();
  const { data, reload } = useApi<Warehouse[]>(can("locations.view") ? "/warehouses" : null, { include_inactive: true });
  const [edit, setEdit] = useState<Warehouse | "new" | null>(null);
  const [bins, setBins] = useState<Warehouse | null>(null);
  if (!can("locations.view")) return <NoAccess />;
  const manage = can("locations.manage");

  return (
    <>
      <PageHead title="Warehouses & locations" sub="Every place you keep stock: stores, branches, sites, vans, technicians.">
        {manage && <button className="act" onClick={() => setEdit("new")}>Add location</button>}
      </PageHead>
      {!data ? <Loading /> : (
        <div className="panel"><div className="table-wrap"><table className="t">
          <thead><tr><th>Name</th><th>Type</th><th className="r">Units</th><th className="r hide-mobile">Value</th><th className="hide-mobile" /></tr></thead>
          <tbody>{data.map((w) => (
            <tr key={w.id}>
              <td><Link href={`/inventory?warehouse_id=${w.id}`}><b>{w.name}</b></Link><span className="sub">{w.code}{w.address ? ` · ${w.address}` : ""}{!w.is_active ? " · inactive" : ""}</span></td>
              <td>{WAREHOUSE_TYPES[w.type] || w.type}</td>
              <td className="r"><Qty v={w.stock_units} /></td>
              <td className="r num hide-mobile">{money(w.stock_value, me?.organization.currency)}</td>
              <td className="r hide-mobile"><div className="row end">
                {(mod("multi_location") || w.location_count > 1) && <button className="btn small" onClick={() => setBins(w)}>Shelves & bins ({w.location_count - 1})</button>}
                {manage && <button className="btn small" onClick={() => setEdit(w)}>Edit</button>}
              </div></td>
            </tr>))}</tbody>
        </table></div></div>
      )}
      {!mod("multi_location") && <p className="meta" style={{ marginTop: 12 }}>Need aisles, racks and bins inside a warehouse? Switch on “Shelf and bin locations” in Settings.</p>}
      {edit && <WarehouseDrawer item={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={(w) => { setEdit(null); toast(`Saved ${w.name}`); reload(); }} />}
      {bins && <BinsDrawer wh={bins} manage={manage} onClose={() => { setBins(null); reload(); }} />}
    </>
  );
}

function WarehouseDrawer({ item, onClose, onSaved }: { item: Warehouse | null; onClose: () => void; onSaved: (w: Warehouse) => void }) {
  const [f, setF] = useState({ name: item?.name || "", code: item?.code || "", type: item?.type || "store", address: item?.address || "", is_active: item ? item.is_active : true });
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  return (
    <Drawer title={item ? `Edit ${item.name}` : "Add a location"} open onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" form="wh-form">Save</button></>}>
      <form id="wh-form" className="stack" onSubmit={async (e) => {
        e.preventDefault(); setErr(null);
        try { onSaved(await api<Warehouse>(item ? `/warehouses/${item.id}` : "/warehouses", { method: item ? "PUT" : "POST", body: { ...f, code: f.code || undefined } })); }
        catch (e) { setErr(errMsg(e)); }
      }}>
        {err && <Notice tone="bad">{err}</Notice>}
        <Field label="Name" htmlFor="wn"><input id="wn" className="input" required autoFocus value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="e.g. Lekki branch, Van 2, Site 14" /></Field>
        <div className="grid two">
          <Field label="Type" htmlFor="wt"><select id="wt" className="input" value={f.type} onChange={(e) => set("type", e.target.value)}>
            {Object.entries(WAREHOUSE_TYPES).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          <Field label="Short code" htmlFor="wc" hint="Optional — we’ll make one"><input id="wc" className="input" maxLength={20} value={f.code} onChange={(e) => set("code", e.target.value.toUpperCase())} /></Field>
        </div>
        <Field label="Address" htmlFor="wa"><textarea id="wa" className="input" value={f.address} onChange={(e) => set("address", e.target.value)} /></Field>
        {item && <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => set("is_active", e.target.checked)} /><span>Active<span className="sub meta">A location must be empty before it can be switched off.</span></span></label>}
      </form>
    </Drawer>
  );
}

function BinsDrawer({ wh, manage, onClose }: { wh: Warehouse; manage: boolean; onClose: () => void }) {
  const { data, reload } = useApi<Loc[]>(`/warehouses/${wh.id}/locations`);
  const [f, setF] = useState({ name: "", kind: "bin", parent_id: "" });
  const [err, setErr] = useState<string | null>(null);
  return (
    <Drawer title={`${wh.name}: shelves & bins`} open onClose={onClose}>
      <div className="stack">
        {(data || []).filter((l) => l.kind !== "default").map((l) => (
          <div key={l.id} className="locline"><span>{l.name} <span className="badge">{l.kind}</span>{!l.is_active && <span className="badge out">inactive</span>}</span>
            {manage && l.is_active && <button className="btn small" onClick={async () => { try { await api(`/locations/${l.id}`, { method: "DELETE" }); reload(); } catch (e) { setErr(errMsg(e)); } }}>Remove</button>}</div>
        ))}
        {data && data.length <= 1 && <p className="muted">Only the Main area so far.</p>}
        {err && <Notice tone="bad">{err}</Notice>}
        {manage && (
          <form className="stack panel panel-pad" onSubmit={async (e) => {
            e.preventDefault(); setErr(null);
            try { await api(`/warehouses/${wh.id}/locations`, { body: { name: f.name, kind: f.kind, parent_id: f.parent_id || undefined } }); setF({ ...f, name: "" }); reload(); }
            catch (e) { setErr(errMsg(e)); }
          }}>
            <h3>Add a shelf or bin</h3>
            <div className="grid two">
              <Field label="Name" htmlFor="bn"><input id="bn" className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. Aisle B · Shelf 3" /></Field>
              <Field label="Kind" htmlFor="bk"><select id="bk" className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
                {["zone", "aisle", "rack", "shelf", "bin"].map((k) => <option key={k} value={k}>{k[0].toUpperCase() + k.slice(1)}</option>)}</select></Field>
            </div>
            <Field label="Inside" htmlFor="bp"><select id="bp" className="input" value={f.parent_id} onChange={(e) => setF({ ...f, parent_id: e.target.value })}>
              <option value="">{wh.name}</option>{(data || []).filter((l) => l.kind !== "default" && l.kind !== "bin").map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></Field>
            <button className="btn primary">Add</button>
          </form>
        )}
      </div>
    </Drawer>
  );
}
