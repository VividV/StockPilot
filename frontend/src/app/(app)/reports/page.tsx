"use client";
import Link from "next/link";
import { NoAccess, PageHead } from "@/components/ui";
import { useAuth } from "@/lib/auth";

// Grouped by the question each report answers.
const GROUPS: { title: string; items: { k: string; t: string; d: string; perm?: string }[] }[] = [
  { title: "What do I have?", items: [
    { k: "stock-on-hand", t: "Stock on hand", d: "Every item, in every location, with batches." },
    { k: "valuation", t: "Inventory valuation", d: "What your stock is worth at average cost and at selling price." },
    { k: "locations", t: "Stock by location", d: "Units and value per warehouse, shop, site or van." },
  ] },
  { title: "What needs action?", items: [
    { k: "low-stock", t: "Low stock", d: "Items at or below their reorder level, with how many to order." },
    { k: "out-of-stock", t: "Out of stock", d: "Items you have run out of." },
    { k: "expiry", t: "Expiry", d: "Batches that are expired or expire within 7, 30 or 90 days." },
  ] },
  { title: "Buying and selling", items: [
    { k: "sales", t: "Sales", d: "Every sales order with payments, amounts owed and profit.", perm: "sales.view" },
    { k: "sales-by-product", t: "Sales by product", d: "Units sold, revenue and profit for each product.", perm: "sales.view" },
    { k: "purchases", t: "Purchases", d: "Every purchase order, what arrived and what is still to come.", perm: "purchases.view" },
    { k: "suppliers", t: "Suppliers", d: "Orders, value, late deliveries and delivery times per supplier.", perm: "suppliers.view" },
  ] },
  { title: "Counter sales", items: [
    { k: "pos-sales", t: "Receipts", d: "Every counter sale and return: who sold it, how it was paid, profit.", perm: "sales.view" },
    { k: "pos-products", t: "Counter sales by product", d: "Units sold and returned, discounts and profit per product.", perm: "sales.view" },
    { k: "pos-shifts", t: "Cash-ups", d: "Every till shift: cash expected, counted, over or short.", perm: "sales.view" },
  ] },
  { title: "What happened?", items: [
    { k: "movements", t: "Stock movements", d: "Every change: who, what, where, when." },
    { k: "adjustments", t: "Adjustments", d: "Counts, damage, loss and write-offs with their value." },
    { k: "batches", t: "Batches", d: "Every batch or lot, with supplier and purchase reference." },
    { k: "serials", t: "Serial numbers", d: "Every serial number and its status." },
  ] },
];

export default function Reports() {
  const { can } = useAuth();
  if (!can("reports.view")) return <NoAccess />;
  return (
    <>
      <PageHead title="Reports" sub={can("reports.export") ? "Open any report, then download it as Excel, CSV or PDF." : "Open any report to see it on screen."} />
      <div className="stack" style={{ gap: 24 }}>
        {GROUPS.filter((g) => g.items.some((r) => !r.perm || can(r.perm))).map((g) => (
          <section key={g.title}>
            <h2 style={{ marginBottom: 10 }}>{g.title}</h2>
            <div className="panel">
              {g.items.filter((r) => !r.perm || can(r.perm)).map((r) => (
                <Link key={r.k} href={`/reports/${r.k}`} className="locline" style={{ padding: "14px 18px", textDecoration: "none", flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
                  <b>{r.t}</b><span className="muted">{r.d}</span>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
