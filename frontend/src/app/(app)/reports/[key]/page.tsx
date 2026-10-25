"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { I } from "@/components/icons";
import { Empty, Loading, Notice, NoAccess, PageHead, useToast } from "@/components/ui";
import { download } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { MOVEMENT_LABELS, date, dateTime, errMsg, money, qty } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Warehouse } from "@/lib/types";

type Report = { key: string; title: string; columns: { key: string; label: string; type: string }[]; rows: Record<string, any>[];
  total_rows: number; summary: Record<string, any>; generated_at: string };

const SUMMARY_LABELS: Record<string, string> = {
  total_value: "Stock value", total_units: "Units", in_transit_value: "On the way (value)", products: "Products", lines: "Lines",
  movements: "Movements", units_in: "Units in", units_out: "Units out", locations: "Locations", batches: "Batches", serials: "Serials",
  expired: "Expired", "7_days": "Within 7 days", "30_days": "Within 30 days", "90_days": "Within 90 days", value_impact: "Value impact",
  orders: "Orders", ordered_value: "Ordered", received_value: "Received", outstanding_value: "Still to come", suppliers: "Suppliers",
  sales_value: "Sales", amount_paid: "Paid", balance_due: "Owed to you", gross_profit: "Gross profit", revenue: "Revenue",
  receipts: "Receipts", sales: "Sales", returns: "Returns", net_sales: "Net sales", discounts: "Discounts", shifts: "Shifts",
  total_over_short: "Cash over / short",
};
const MONEY_KEYS = new Set(["total_value", "in_transit_value", "value_impact", "ordered_value", "received_value", "outstanding_value",
  "sales_value", "amount_paid", "balance_due", "gross_profit", "revenue", "sales", "returns", "net_sales", "discounts", "total_over_short"]);

export default function ReportPage() {
  const { key } = useParams<{ key: string }>();
  const { can, me } = useAuth();
  const toast = useToast();
  const [wh, setWh] = useState("");
  const [days, setDays] = useState("90");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [type, setType] = useState("");
  const filters = { warehouse_id: wh, days: key === "expiry" ? days : undefined, date_from: from, date_to: to, type: type || undefined };
  const { data, error } = useApi<Report>(can("reports.view") ? `/reports/${key}` : null, { ...filters, limit: 1000 });
  const { data: whs } = useApi<Warehouse[]>("/warehouses");
  if (!can("reports.view")) return <NoAccess />;
  const cur = me?.organization.currency;
  const cell = (v: any, t: string, k: string) => {
    if (v === null || v === undefined || v === "") return <span className="meta">—</span>;
    if (t === "money") return money(v, cur);
    if (t === "number") return qty(v);
    if (k === "created_at" || k === "updated_at" || k === "date") return dateTime(v);
    if (["expires_on", "manufactured_on", "order_date", "expected_on"].includes(k)) return date(v);
    if (k === "movement_type") return MOVEMENT_LABELS[String(v).replace(/ /g, "_")] || v;
    return String(v);
  };
  const exp = async (format: string) => {
    try { await download(`/reports/${key}`, { ...filters, format }); toast("Download started"); } catch (e) { toast(errMsg(e), "bad"); }
  };

  return (
    <>
      <PageHead title={data?.title || "Report"} sub={<Link href="/reports">All reports</Link>}>
        {can("reports.export") && <>
          <button className="btn small" onClick={() => exp("xlsx")}><I.download />Excel</button>
          <button className="btn small" onClick={() => exp("csv")}>CSV</button>
          <button className="btn small" onClick={() => exp("pdf")}>PDF</button>
        </>}
      </PageHead>
      <div className="row" style={{ marginBottom: 14 }}>
        {["valuation", "stock-on-hand", "low-stock", "out-of-stock", "expiry", "movements", "pos-sales", "pos-shifts", "pos-products"].includes(key) && (whs?.length || 0) > 1 && (
          <select className="input" style={{ width: "auto" }} aria-label="Location" value={wh} onChange={(e) => setWh(e.target.value)}>
            <option value="">All locations</option>{whs!.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select>)}
        {key === "expiry" && <select className="input" style={{ width: "auto" }} aria-label="Window" value={days} onChange={(e) => setDays(e.target.value)}>
          <option value="7">Next 7 days</option><option value="30">Next 30 days</option><option value="90">Next 90 days</option><option value="365">Next year</option></select>}
        {["movements", "adjustments", "purchases", "suppliers", "sales", "sales-by-product", "pos-sales", "pos-shifts", "pos-products"].includes(key) && <>
          <input type="date" className="input" style={{ width: "auto" }} aria-label="From" value={from} onChange={(e) => setFrom(e.target.value)} />
          <input type="date" className="input" style={{ width: "auto" }} aria-label="To" value={to} onChange={(e) => setTo(e.target.value)} /></>}
        {key === "movements" && <select className="input" style={{ width: "auto" }} aria-label="Type" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All changes</option>{Object.entries(MOVEMENT_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>}
      </div>
      {error && <Notice tone="bad">{error}</Notice>}
      {!data ? <Loading /> : (
        <div className="stack">
          {Object.keys(data.summary).length > 0 && (
            <div className="stats">{Object.entries(data.summary).map(([k, v]) => (
              <div key={k} className="stat"><div className="k">{SUMMARY_LABELS[k] || k}</div><div className="v qty" style={{ fontSize: 22 }}>{MONEY_KEYS.has(k) ? money(v, cur) : qty(v)}</div></div>
            ))}</div>
          )}
          <div className="panel">
            {data.rows.length === 0 ? <Empty title="Nothing to show" text={key === "low-stock" ? "Everything is above its reorder level." : undefined} /> : (
              <div className="table-wrap"><table className="t">
                <thead><tr>{data.columns.map((c) => <th key={c.key} className={c.type !== "text" ? "r" : ""}>{c.label}</th>)}</tr></thead>
                <tbody>{data.rows.map((r, i) => (
                  <tr key={i}>{data.columns.map((c) => (
                    <td key={c.key} className={c.type !== "text" ? "r num" : ""}>
                      {c.key === "name" && r.product_id ? <Link href={`/products/${r.product_id}`}>{r.name}</Link>
                        : c.key === "number" && r.purchase_order_id ? <Link href={`/purchases/${r.purchase_order_id}`}>{r.number}</Link>
                        : c.key === "number" && r.sales_order_id ? <Link href={`/sales/${r.sales_order_id}`}>{r.number}</Link>
                        : c.key === "number" && r.pos_sale_id ? <Link href={`/pos/receipts/${r.pos_sale_id}`}>{r.number}</Link>
                        : c.key === "number" && r.pos_shift_id ? <Link href={`/pos/shifts/${r.pos_shift_id}`}>{r.number}</Link>
                        : cell(r[c.key], c.type, c.key)}
                    </td>))}</tr>))}</tbody>
              </table></div>
            )}
            {data.total_rows > data.rows.length && <div className="pager">Showing {data.rows.length} of {data.total_rows}. Download to see every row.</div>}
          </div>
          <p className="meta">Generated {dateTime(data.generated_at)}</p>
        </div>
      )}
    </>
  );
}
