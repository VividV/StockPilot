"use client";
import Link from "next/link";
import { Empty, Loading, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { NotificationItem, Page } from "@/lib/types";

const link = (n: NotificationItem) =>
  n.entity_type === "product" ? `/products/${n.entity_id}` : n.entity_type === "batch" ? "/inventory?tab=expiry" : n.entity_type === "transfer" ? `/transfers/${n.entity_id}` : null;

export default function Notifications() {
  const { data, reload } = useApi<Page<NotificationItem>>("/notifications", { page_size: 100 });
  return (
    <>
      <PageHead title="Alerts" sub="Low stock, out of stock and expiry warnings. Alerts clear themselves once fixed.">
        <button className="btn small" onClick={async () => { await api("/notifications/read-all", { method: "POST" }); reload(); }}>Mark all as read</button>
      </PageHead>
      <div className="panel">
        {!data ? <Loading /> : data.items.length === 0 ? <Empty title="All clear" text="Nothing needs your attention right now." /> : data.items.map((n) => {
          const href = link(n);
          return (
            <div key={n.id} className="spread" style={{ padding: "14px 18px", borderBottom: "1px solid var(--rule)", background: n.is_read ? undefined : "#fbfcfe" }}>
              <div className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
                <span className={`badge ${n.severity === "critical" ? "bad" : n.severity === "warning" ? "warn" : "info"}`} style={{ marginTop: 2 }}>
                  {n.type === "out_of_stock" ? "Out" : n.type === "low_stock" ? "Low" : n.type === "expired" ? "Expired" : n.type === "expiring" ? "Expiring" : n.type}</span>
                <div>{href ? <Link href={href} onClick={() => api(`/notifications/${n.id}/read`, { method: "POST" })}><b>{n.title}</b></Link> : <b>{n.title}</b>}
                  {n.body && <p className="muted">{n.body}</p>}</div>
              </div>
              <span className="meta" style={{ whiteSpace: "nowrap" }}>{ago(n.created_at)}</span>
            </div>
          );
        })}
      </div>
    </>
  );
}
