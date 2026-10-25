"use client";
import { useRef, useState } from "react";
import { api, download, getToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, errMsg } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useToast } from "./ui";

type Att = { id: string; filename: string; size_bytes: number; is_image: boolean; previewable: boolean; created_at: string };

const EDIT_PERM: Record<string, string> = {
  product: "products.edit", purchase_order: "purchases.create", sales_order: "sales.create", transfer: "inventory.transfer",
  adjustment: "inventory.adjust", supplier: "suppliers.manage", customer: "customers.manage",
};

const size = (b: number) => (b < 1024 * 1024 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`);

/** Delivery notes, invoices, photos… attached to a record. */
export function Attachments({ entityType, entityId, title = "Files" }: { entityType: string; entityId: string; title?: string }) {
  const { can } = useAuth();
  const toast = useToast();
  const { data, reload } = useApi<Att[]>("/attachments", { entity_type: entityType, entity_id: entityId });
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const canEdit = can(EDIT_PERM[entityType]);
  if (!data || (!data.length && !canEdit)) return null;

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    for (const f of Array.from(files)) {
      const body = new FormData();
      body.append("entity_type", entityType);
      body.append("entity_id", entityId);
      body.append("file", f);
      const res = await fetch("/api/attachments", { method: "POST", body, headers: { Authorization: `Bearer ${getToken()}`, "X-Requested-With": "StockPilot" } });
      if (!res.ok) {
        let msg = `Couldn’t upload ${f.name}`;
        try { msg = `${f.name}: ${(await res.json()).detail}`; } catch { /* not json */ }
        toast(msg, "bad");
      }
    }
    setBusy(false);
    if (input.current) input.current.value = "";
    reload();
  };
  const open = async (a: Att) => {
    if (!a.previewable) return download(`/attachments/${a.id}/file`);
    const res = await api<Response>(`/attachments/${a.id}/file`, { raw: true });
    const url = URL.createObjectURL(await res.blob());
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{title}</h2>
        {canEdit && <>
          <input ref={input} type="file" multiple hidden accept=".pdf,.png,.jpg,.jpeg,.webp,.gif,.csv,.txt,.xlsx,.xls,.docx,.doc"
                 onChange={(e) => upload(e.target.files)} />
          <button className="btn small" disabled={busy} onClick={() => input.current?.click()}>{busy ? "Uploading…" : "Attach a file"}</button>
        </>}
      </div>
      {data.length === 0 ? <p className="panel-pad meta">Attach delivery notes, invoices or photos. PDF, images, Excel or Word, up to 10 MB.</p> : (
        <div>{data.map((a) => (
          <div key={a.id} className="spread" style={{ padding: "10px 18px", borderBottom: "1px solid var(--rule)" }}>
            <button className="btn link" onClick={() => open(a).catch((e) => toast(errMsg(e), "bad"))}>{a.filename}</button>
            <span className="row" style={{ gap: 10 }}>
              <span className="meta">{size(a.size_bytes)} · {ago(a.created_at)}</span>
              {canEdit && <button className="btn small" onClick={async () => {
                if (!confirm(`Remove ${a.filename}?`)) return;
                try { await api(`/attachments/${a.id}`, { method: "DELETE" }); reload(); } catch (e) { toast(errMsg(e), "bad"); }
              }}>Remove</button>}
            </span>
          </div>))}
        </div>
      )}
    </section>
  );
}
