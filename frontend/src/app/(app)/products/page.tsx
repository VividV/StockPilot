"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Can, Empty, Loading, Notice, NoAccess, PageHead, Pager, StockBadge } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { money, qty } from "@/lib/format";
import { useApi, useDebounced } from "@/lib/hooks";
import type { Page, Product, Ref } from "@/lib/types";

function Products() {
  const { can, me } = useAuth();
  const router = useRouter();
  const sp = useSearchParams();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState(sp.get("status") || "");
  const [category, setCategory] = useState("");
  const [active, setActive] = useState("true");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  const { data: cats } = useApi<Ref[]>("/categories");
  const { data, error, loading } = useApi<Page<Product>>(can("products.view") ? "/products" : null,
    { q: dq, status, category_id: category, active: active === "all" ? undefined : active, page, page_size: 25 });
  if (!can("products.view")) return <NoAccess />;

  return (
    <>
      <PageHead title="Products" sub={data ? `${data.total} product${data.total === 1 ? "" : "s"}` : undefined}>
        <Can perm="products.create"><Link className="btn" href="/imports?type=products">Import from Excel</Link><Link className="btn" href="/labels">Print labels</Link><Link className="act" href="/products/new">Add product</Link></Can>
      </PageHead>
      <div className="row" style={{ marginBottom: 14 }}>
        <input className="input" style={{ maxWidth: 320 }} placeholder="Name, SKU or barcode" aria-label="Filter products" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <select className="input" style={{ width: "auto" }} aria-label="Stock status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">Any stock level</option><option value="in_stock">In stock</option><option value="low">Low — reorder</option><option value="out">Out of stock</option>
        </select>
        <select className="input" style={{ width: "auto" }} aria-label="Category" value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }}>
          <option value="">All categories</option>{(cats || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select className="input" style={{ width: "auto" }} aria-label="Active" value={active} onChange={(e) => { setActive(e.target.value); setPage(1); }}>
          <option value="true">Active</option><option value="false">Inactive</option><option value="all">All</option>
        </select>
      </div>
      {error && <Notice tone="bad">{error}</Notice>}
      <div className="panel">
        {!data && loading ? <Loading /> : data && data.items.length === 0 ? (
          <Empty title={q || status || category ? "No products match" : "No products yet"}
                 text={q || status || category ? "Try a different search or filter." : "Add the things you keep in stock."}
                 action={!q && can("products.create") ? <Link className="btn primary" href="/products/new">Add your first product</Link> : undefined} />
        ) : data && (
          <>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Product</th><th className="hide-mobile">Category</th><th className="r">In stock</th><th className="hide-mobile">Status</th><th className="r hide-mobile">Price</th></tr></thead>
              <tbody>
                {data.items.map((p) => (
                  <tr key={p.id} className="click" onClick={() => router.push(`/products/${p.id}`)}>
                    <td><Link href={`/products/${p.id}`} onClick={(e) => e.stopPropagation()}><b>{p.name}</b></Link>
                      <span className="sub">SKU {p.sku}{p.has_variants ? ` · ${p.variant_count ?? 0} sizes/colours` : ""}{p.barcode ? ` · ${p.barcode}` : ""}{!p.is_active ? " · inactive" : ""}</span></td>
                    <td className="hide-mobile">{p.category?.name || <span className="meta">—</span>}</td>
                    <td className="r"><span className="qty">{qty(p.on_hand)}<span className="u">{p.base_unit.abbreviation}</span></span>
                      <span className="sub only-mobile"><StockBadge status={p.stock_status} /></span></td>
                    <td className="hide-mobile"><StockBadge status={p.stock_status} /></td>
                    <td className="r num hide-mobile">{money(p.selling_price, me?.organization.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
            <Pager page={page} pageSize={25} total={data.total} onPage={setPage} />
          </>
        )}
      </div>
    </>
  );
}

export default function ProductsPage() {
  return <Suspense><Products /></Suspense>;
}
