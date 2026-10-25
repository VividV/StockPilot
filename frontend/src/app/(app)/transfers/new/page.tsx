"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { LocationSelect, ProductPicker } from "@/components/pickers";
import { Field, Notice, NoAccess, PageHead, Qty, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";
import type { ProductDetail, Transfer } from "@/lib/types";

type Line = { detail: ProductDetail; quantity: string };

export default function NewTransfer() {
  const { can, mod } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [picking, setPicking] = useState<ProductDetail | null>(null);
  const [notes, setNotes] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!can("inventory.transfer")) return <NoAccess />;

  const save = async (mode: "draft" | "submit" | "send") => {
    setBusy(true); setErr(null);
    try {
      let t = await api<Transfer>("/transfers", { body: { from_location_id: from, to_location_id: to, notes: notes || undefined, submit: mode === "submit",
        items: lines.map((l) => ({ product_id: l.detail.product.id, quantity: l.quantity })) } });
      if (mode === "send") t = await api<Transfer>(`/transfers/${t.id}/dispatch`, { method: "POST" });
      toast(`${t.number} ${mode === "send" ? "sent" : "saved"}`);
      router.push(`/transfers/${t.id}`);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };

  return (
    <div className="form-panel" style={{ maxWidth: 760 }}>
      <PageHead title="Transfer several products" sub="Build a list, then send it now or save it for later." />
      <div className="panel panel-pad stack">
        {err && <Notice tone="bad">{err}</Notice>}
        <div className="grid two">
          <LocationSelect label="From" value={from} onChange={(v) => { setFrom(v); if (v === to) setTo(""); }} />
          <LocationSelect label="To" value={to} exclude={from} onChange={setTo} />
        </div>
        {lines.length > 0 && (
          <table className="t"><thead><tr><th>Product</th><th className="r">Here now</th><th className="r">Send</th><th /></tr></thead>
            <tbody>{lines.map((l, i) => {
              const here = l.detail.locations.find((x) => x.location_id === from)?.quantity || "0";
              return (
                <tr key={l.detail.product.id}>
                  <td>{l.detail.product.name}<span className="sub">{l.detail.product.sku}</span></td>
                  <td className="r"><Qty v={here} unit={l.detail.product.base_unit.abbreviation} /></td>
                  <td className="r"><input className="input num" style={{ width: 110 }} inputMode="decimal" aria-label={`Quantity of ${l.detail.product.name}`} value={l.quantity}
                    onChange={(e) => setLines((ls) => ls.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} /></td>
                  <td className="r"><button className="btn small" onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}>Remove</button></td>
                </tr>);
            })}</tbody></table>
        )}
        <ProductPicker label="Add a product" value={picking} onChange={(d) => {
          if (d) {
            if (d.product.track_serials) { setErr("Serial-numbered products are moved one at a time from Transfer stock, so you can pick the exact units."); return; }
            setLines((ls) => ls.some((x) => x.detail.product.id === d.product.id) ? ls : [...ls, { detail: d, quantity: "" }]);
          }
          setPicking(null);
        }} />
        <Field label="Note" htmlFor="n"><input id="n" className="input" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        <div className="row end">
          <button className="btn" disabled={busy || !lines.length} onClick={() => save("draft")}>Save as draft</button>
          {mod("transfer_approval")
            ? <button className="btn go" disabled={busy || !lines.length} onClick={() => save("submit")}>Send for approval</button>
            : <button className="btn go" disabled={busy || !lines.length || lines.some((l) => !l.quantity)} onClick={() => save("send")}>Send stock now</button>}
        </div>
      </div>
    </div>
  );
}
