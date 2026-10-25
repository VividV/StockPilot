"use client";
import Link from "next/link";
import { useRef, useState } from "react";
import { CameraScanner } from "@/components/camerascanner";
import { I } from "@/components/icons";
import { MovementTable } from "@/components/movements";
import { Can, ExpiryBadge, Notice, PageHead, Qty, StockBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { date, errMsg } from "@/lib/format";
import type { ProductDetail } from "@/lib/types";

const daysTo = (d: string | null) => (d ? Math.round((new Date(d + "T00:00:00").getTime() - new Date().setHours(0, 0, 0, 0)) / 86400000) : null);

export default function ScanPage() {
  const [code, setCode] = useState("");
  const [res, setRes] = useState<ProductDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [camera, setCamera] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const look = async (c: string) => {
    if (!c.trim()) return;
    setErr(null);
    try { setRes(await api<ProductDetail>("/products/lookup", { query: { code: c.trim() } })); }
    catch (e) { setRes(null); setErr(errMsg(e)); }
    setCode("");
    input.current?.focus();
  };

  const p = res?.product;
  return (
    <div style={{ maxWidth: 820 }}>
      <PageHead title="Scan" sub="Point the camera at a barcode, use a USB scanner, or type a barcode, SKU, batch or serial number." />
      <form className="row" onSubmit={(e) => { e.preventDefault(); look(code); }} style={{ marginBottom: 16, flexWrap: "nowrap" }}>
        <input ref={input} className="input num" autoFocus value={code} onChange={(e) => setCode(e.target.value)} placeholder="Scan or type a code" aria-label="Code" style={{ fontSize: 18 }} />
        <button className="btn primary">Find</button>
        <button type="button" className="btn go" onClick={() => setCamera((c) => !c)} aria-pressed={camera}><I.camera />{camera ? "Stop" : "Camera"}</button>
      </form>
      {camera && <div style={{ marginBottom: 16 }}><CameraScanner onCode={(c) => { setCamera(false); look(c); }} onClose={() => setCamera(false)} /></div>}
      <p className="meta" style={{ marginBottom: 12 }}>A USB or Bluetooth scanner also works: it types the code into the box above.</p>
      {err && <Notice tone="bad">{err}</Notice>}
      {res && p && (
        <div className="stack">
          <section className="panel panel-pad stack">
            <div className="spread">
              <div><h2><Link href={`/products/${p.id}`}>{p.name}</Link></h2><p className="meta">SKU {p.sku}{p.barcode ? ` · ${p.barcode}` : ""}{res.serial ? ` · serial ${res.serial.serial} (${res.serial.status.replace("_", " ")})` : ""}</p></div>
              <StockBadge status={p.stock_status} />
            </div>
            <div className="binlabel"><Qty v={res.on_hand} unit={p.base_unit.abbreviation} /><span className="muted">in stock</span></div>
            {res.locations.map((l) => (
              <div key={l.location_id} style={{ borderTop: "1px solid var(--rule)", paddingTop: 8 }}>
                <div className="spread"><b>{l.location_name}</b><Qty v={l.quantity} unit={p.base_unit.abbreviation} /></div>
                {l.batches.map((b) => <div key={b.batch_id} className="spread meta" style={{ paddingLeft: 12 }}><span>Batch {b.batch_number} · {date(b.expires_on)} <ExpiryBadge days={daysTo(b.expires_on)} /></span><span className="num">{b.quantity}</span></div>)}
              </div>
            ))}
            <div className="actions">
              <Can perm="inventory.receive"><Link className="act" href={`/receive?product=${p.id}`}><I.receive />Receive</Link></Can>
              <Can perm="inventory.issue"><Link className="act" href={`/issue?product=${p.id}`}><I.issue />Issue</Link></Can>
              <Can perm="inventory.transfer"><Link className="act secondary" href={`/transfer?product=${p.id}`}><I.transfers />Transfer</Link></Can>
            </div>
          </section>
          {res.recent_movements.length > 0 && <section className="panel"><div className="panel-head"><h2>Recent changes</h2></div><MovementTable rows={res.recent_movements} showProduct={false} compact /></section>}
        </div>
      )}
    </div>
  );
}
