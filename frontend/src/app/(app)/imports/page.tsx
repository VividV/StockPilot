"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { I } from "@/components/icons";
import { Empty, Loading, Notice, PageHead, useToast } from "@/components/ui";
import { api, download, getToken } from "@/lib/api";
import { errMsg } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type Kind = { kind: string; title: string; allowed: boolean; columns: { key: string; label: string; required: boolean; help: string }[] };
type Report = { kind: string; title: string; columns: Kind["columns"]; summary: { total: number; create: number; update: number; with_errors: number };
  rows: { row: number; data: Record<string, string>; action: string; errors: { field: string; message: string }[]; notes: string[] }[] };

const BLURB: Record<string, string> = {
  products: "Add or update many products at once. Rows with an SKU you already have update that product.",
  suppliers: "Add or update suppliers. A supplier with the same name is updated.",
  customers: "Add or update customers. A customer with the same name is updated.",
  "opening-stock": "Enter the stock you already have on day one: product, location and quantity.",
};
const DONE_LINK: Record<string, [string, string]> = {
  products: ["/products", "See products"], suppliers: ["/suppliers", "See suppliers"],
  customers: ["/customers", "See customers"], "opening-stock": ["/inventory", "See stock"],
};

export default function ImportPage() {
  const { data: kinds } = useApi<Kind[]>("/imports");
  const [kind, setKind] = useState<string>("");
  const [rep, setRep] = useState<Report | null>(null);
  const [rows, setRows] = useState<Report["rows"]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [onlyErrors, setOnlyErrors] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const toast = useToast();
  useEffect(() => {
    if (kind || !kinds) return;
    const k = new URLSearchParams(window.location.search).get("type");
    setKind(kinds.find((x) => x.kind === k && x.allowed)?.kind || kinds.find((x) => x.allowed)?.kind || "");
  }, [kinds, kind]);
  if (!kinds) return <Loading />;
  const allowed = kinds.filter((k) => k.allowed);
  if (!allowed.length) return <Empty title="You don't have access to imports" />;
  const current = kinds.find((k) => k.kind === kind);
  const reset = () => { setRep(null); setRows([]); setErr(null); setDone(null); if (file.current) file.current.value = ""; };

  const show = (r: Report) => { setRep(r); setRows(r.rows); setOnlyErrors(r.summary.with_errors > 0 && r.summary.total > 20); };
  const preview = async (f: File) => {
    setBusy(true); setErr(null); setDone(null);
    const body = new FormData(); body.append("file", f);
    try {
      const res = await fetch(`/api/imports/${kind}/preview`, { method: "POST", body, headers: { Authorization: `Bearer ${getToken()}`, "X-Requested-With": "StockPilot" } });
      const out = await res.json();
      if (!res.ok) throw new Error(out.detail || "Couldn’t read the file");
      show(out);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  const check = async () => {
    setBusy(true); setErr(null);
    try { show(await api(`/imports/${kind}/validate`, { body: { rows: rows.map((r) => r.data) } })); }
    catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  const commit = async () => {
    setBusy(true); setErr(null);
    try { const r = await api(`/imports/${kind}/commit`, { body: { rows: rows.map((r) => r.data) } }); setDone(r.message); setRep(null); toast(r.message); }
    catch (e: any) {
      if (e?.data?.rows) { show(e.data); setErr("Some rows have problems. Nothing was imported — fix them below and check again."); }
      else setErr(errMsg(e));
    } finally { setBusy(false); }
  };
  const edit = (i: number, key: string, v: string) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, data: { ...r.data, [key]: v } } : r)));
  const dirty = rep && JSON.stringify(rows.map((r) => r.data)) !== JSON.stringify(rep.rows.map((r) => r.data));
  const errorCount = rows.filter((r) => r.errors.length).length;

  return (
    <div style={{ maxWidth: 1200 }}>
      <PageHead title="Import from a spreadsheet" sub="Download the template, fill it in Excel or Google Sheets, then upload it here. Nothing is saved until you press Import." />

      <div className="tabs" role="tablist">
        {allowed.map((k) => <button key={k.kind} role="tab" className="tab" aria-selected={kind === k.kind} onClick={() => { setKind(k.kind); reset(); }}>{k.title}</button>)}
      </div>

      {!current ? <p className="muted">Choose what you want to import.</p> : (
        <div className="stack">
          {done ? (
            <div className="stack">
              <Notice tone="ok"><b>{done}</b></Notice>
              <div className="row"><Link className="btn primary" href={DONE_LINK[kind][0]}>{DONE_LINK[kind][1]}</Link><button className="btn" onClick={reset}>Import another file</button></div>
            </div>
          ) : !rep ? (
            <div className="grid two" style={{ alignItems: "start" }}>
              <section className="panel panel-pad stack">
                <h2>1. Get the template</h2>
                <p className="muted">{BLURB[kind]}</p>
                <div className="row">
                  <button className="btn" onClick={() => download(`/imports/${kind}/template`, { format: "xlsx" })}><I.download />Excel template</button>
                  <button className="btn" onClick={() => download(`/imports/${kind}/template`, { format: "csv" })}>CSV template</button>
                </div>
                <details><summary className="label" style={{ cursor: "pointer" }}>What goes in each column</summary>
                  <table className="t" style={{ marginTop: 8 }}><tbody>{current.columns.map((c) => (
                    <tr key={c.key}><td><b>{c.label}</b>{c.required && <span className="badge warn" style={{ marginLeft: 6 }}>required</span>}</td><td className="muted">{c.help}</td></tr>))}</tbody></table>
                </details>
              </section>
              <section className="panel panel-pad stack">
                <h2>2. Upload your file</h2>
                <p className="muted">Excel (.xlsx) or CSV. We’ll check every row and show you what will happen before anything is saved.</p>
                {err && <Notice tone="bad">{err}</Notice>}
                <input ref={file} type="file" accept=".xlsx,.csv" hidden onChange={(e) => e.target.files?.[0] && preview(e.target.files[0])} />
                <button className="btn go block" disabled={busy} onClick={() => file.current?.click()}><I.upload />{busy ? "Checking…" : "Choose file"}</button>
              </section>
            </div>
          ) : (
            <>
              <div className="stats">
                <div className="stat"><div className="k">Rows</div><div className="v qty">{rep.summary.total}</div></div>
                <div className="stat"><div className="k">New</div><div className="v qty">{rep.summary.create}</div></div>
                {kind !== "opening-stock" && <div className="stat"><div className="k">Will be updated</div><div className="v qty">{rep.summary.update}</div></div>}
                <div className={`stat ${errorCount ? "alert" : ""}`}><div className="k">With problems</div><div className="v qty">{errorCount}</div></div>
              </div>
              {err && <Notice tone="bad">{err}</Notice>}
              {errorCount > 0 ? <Notice tone="warn">Fix the highlighted cells (or remove those rows), then press <b>Check again</b>. Hover a red cell to see what’s wrong.</Notice>
                : dirty ? <Notice tone="info">You changed some cells. Press <b>Check again</b> before importing.</Notice>
                : <Notice tone="ok">Everything looks right. Nothing has been saved yet — press <b>Import</b> to save all {rows.length} rows.</Notice>}
              <div className="row">
                <button className="btn" onClick={reset}>Start over</button>
                <button className="btn" disabled={busy || rows.length === 0} onClick={check}>Check again</button>
                <button className="btn go" disabled={busy || errorCount > 0 || !!dirty || rows.length === 0} onClick={commit}>{busy ? "Working…" : `Import ${rows.length} row${rows.length === 1 ? "" : "s"}`}</button>
                {rep.summary.with_errors > 0 && <label className="check" style={{ marginLeft: "auto" }}><input type="checkbox" checked={onlyErrors} onChange={(e) => setOnlyErrors(e.target.checked)} />Only rows with problems</label>}
              </div>
              <div className="panel"><div className="table-wrap" style={{ maxHeight: "60vh" }}>
                <table className="t" style={{ fontSize: 13.5 }}>
                  <thead style={{ position: "sticky", top: 0, zIndex: 1 }}><tr>
                    <th>Row</th>{kind !== "opening-stock" && <th>Will</th>}
                    {rep.columns.map((c) => <th key={c.key}>{c.label}</th>)}<th />
                  </tr></thead>
                  <tbody>{rows.map((r, i) => (onlyErrors && !r.errors.length) ? null : (
                    <tr key={r.row} style={r.errors.length ? { background: "#fff8f7" } : undefined}>
                      <td className="num meta">{r.row}</td>
                      {kind !== "opening-stock" && <td><span className={`badge ${r.action === "create" ? "ok" : "info"}`}>{r.action === "create" ? "Add" : "Update"}</span>
                        {r.notes.map((n) => <span key={n} className="sub">{n}</span>)}</td>}
                      {rep.columns.map((c) => {
                        const e = r.errors.filter((x) => x.field === c.key);
                        return (
                          <td key={c.key} style={{ padding: 4 }}>
                            <input className="input" style={{ minWidth: c.key === "name" || c.key === "description" ? 180 : 100, minHeight: 34, padding: "4px 8px",
                              ...(e.length ? { borderColor: "var(--bad)", background: "#fff" } : {}) }}
                              aria-label={`${c.label}, row ${r.row}`} aria-invalid={e.length > 0} title={e.map((x) => x.message).join(". ")}
                              value={r.data[c.key] ?? ""} onChange={(ev) => edit(i, c.key, ev.target.value)} />
                            {e.map((x) => <span key={x.message} className="sub" style={{ color: "var(--bad)", maxWidth: 220 }}>{x.message}</span>)}
                          </td>);
                      })}
                      <td><button className="btn small" aria-label={`Remove row ${r.row}`} onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}>Remove</button></td>
                    </tr>))}</tbody>
                </table>
              </div></div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
