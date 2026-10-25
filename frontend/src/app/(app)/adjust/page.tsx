"use client";
import { useState } from "react";
import { LocationSelect, ProductPicker } from "@/components/pickers";
import { BatchSelect, Done, SerialEntry, SerialPicker, availableAt, parseSerials, usePreselect } from "@/components/stockform";
import { Field, Notice, NoAccess, PageHead, Qty } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg, qty } from "@/lib/format";
import type { ProductDetail } from "@/lib/types";

const REASONS = [
  { v: "count_correction", l: "Stock count — set to what I counted" },
  { v: "damaged", l: "Damaged" },
  { v: "lost", l: "Lost or stolen" },
  { v: "expired", l: "Expired" },
  { v: "found", l: "Found extra stock" },
];

export default function AdjustPage() {
  const { can } = useAuth();
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  usePreselect(setDetail);
  const [f, setF] = useState({ reason: "count_correction", location_id: "", batch_id: "", counted: "", amount: "", notes: "" });
  const [serialsOut, setSerialsOut] = useState<string[]>([]);
  const [serialsIn, setSerialsIn] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<any>(null);
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));
  if (!can("inventory.adjust")) return <NoAccess />;
  const p = detail?.product;
  const here = availableAt(detail, f.location_id);
  const systemQty = f.batch_id ? here?.batches.find((b) => b.batch_id === f.batch_id)?.quantity || "0" : here?.quantity || "0";
  const isCount = f.reason === "count_correction";
  const removing = ["damaged", "lost", "expired"].includes(f.reason);
  const diff = isCount && f.counted !== "" ? Number(f.counted) - Number(systemQty) : null;

  if (done) return (
    <div className="form-panel"><PageHead title="Adjust stock" />
      <Done title={done.message} result={done} productId={p?.id} againLabel="Adjust another item" onAgain={() => {
        setDone(null); setDetail(null); setSerialsOut([]); setSerialsIn(""); setF((x) => ({ ...x, batch_id: "", counted: "", amount: "", notes: "" }));
      }} />
    </div>
  );

  return (
    <div className="form-panel">
      <PageHead title="Adjust stock" sub="Correct a count, or write off damaged, lost or expired stock. Every change is recorded." />
      <form className="panel panel-pad stack" onSubmit={async (e) => {
        e.preventDefault();
        if (!p) return setErr("Choose a product first.");
        setBusy(true); setErr(null);
        try {
          const line: Record<string, unknown> = { product_id: p.id, location_id: f.location_id, batch_id: f.batch_id || undefined };
          if (p.track_serials) {
            if (removing) { line.change = -serialsOut.length; line.serials = serialsOut; }
            else { const s = parseSerials(serialsIn); line.change = s.length; line.serials = s; }
          } else if (isCount) line.counted_qty = f.counted;
          else line.change = removing ? -Math.abs(Number(f.amount)) : Math.abs(Number(f.amount));
          const r = await api("/inventory/adjust", { body: { reason: f.reason, notes: f.notes || undefined, lines: [line] } });
          setDone({ ...r, on_hand: undefined });
        } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
      }}>
        {err && <Notice tone="bad">{err}</Notice>}
        <Field label="Reason" htmlFor="r">
          <select id="r" className="input" value={f.reason} onChange={(e) => set("reason", e.target.value)}>
            {REASONS.filter((r) => !(p?.track_serials && r.v === "count_correction")).map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}
          </select>
        </Field>
        <ProductPicker value={detail} onChange={(d) => { setDetail(d); set("batch_id", ""); if (d?.product.track_serials && f.reason === "count_correction") set("reason", "damaged"); }} autoFocus />
        <LocationSelect value={f.location_id} onChange={(v) => set("location_id", v)} />
        {p?.track_batches && !p.track_serials && (
          <BatchSelect detail={detail!} locationId={f.location_id} value={f.batch_id} onChange={(v) => set("batch_id", v)} allowAuto={!isCount && f.reason !== "found"} />
        )}
        {p && !p.track_serials && (isCount ? (
          <div className="grid two">
            <div className="field"><span className="label">System says</span><div className="picked"><Qty v={systemQty} unit={p.base_unit.abbreviation} /></div></div>
            <Field label="You counted" htmlFor="c" hint={diff === null ? undefined : diff === 0 ? "Matches — nothing to change." : `${diff > 0 ? "+" : ""}${qty(diff)} ${p.base_unit.abbreviation} will be recorded`}>
              <input id="c" className="input num" inputMode="decimal" required value={f.counted} onChange={(e) => set("counted", e.target.value)} />
            </Field>
          </div>
        ) : (
          <Field label={removing ? "How many to remove?" : "How many to add?"} htmlFor="a">
            <input id="a" className="input num" inputMode="decimal" required value={f.amount} onChange={(e) => set("amount", e.target.value)} />
          </Field>
        ))}
        {p?.track_serials && (removing
          ? <SerialPicker detail={detail!} locationId={f.location_id} value={serialsOut} onChange={setSerialsOut} need={serialsOut.length} />
          : <SerialEntry value={serialsIn} onChange={setSerialsIn} need={parseSerials(serialsIn).length} />)}
        <Field label="Note" htmlFor="n" hint="Say what happened — managers see this in reports."><input id="n" className="input" value={f.notes} onChange={(e) => set("notes", e.target.value)} /></Field>
        <button className="btn go block" disabled={busy || !p || (f.reason === "count_correction" && f.batch_id === "" && !!p?.track_batches)}>{busy ? "Saving…" : "Save adjustment"}</button>
      </form>
    </div>
  );
}
