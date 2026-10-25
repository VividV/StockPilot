"use client";
import Link from "next/link";
import { MOVEMENT_LABELS, dateTime, qty } from "@/lib/format";
import type { Movement } from "@/lib/types";
import { Change } from "./ui";

/** Who changed what stock, where and when. Used everywhere history is shown. */
export function MovementTable({ rows, showProduct = true, compact = false }: { rows: Movement[]; showProduct?: boolean; compact?: boolean }) {
  return (
    <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th className="hide-mobile">When</th>
            <th>What happened</th>
            {showProduct && <th>Product</th>}
            <th>Where</th>
            <th className="r">Change</th>
            {!compact && <th className="r hide-mobile">Left there</th>}
            <th className={compact ? "hide-mobile" : ""}>By</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.id}>
              <td className="num hide-mobile" style={{ whiteSpace: "nowrap" }}>{dateTime(m.created_at)}</td>
              <td>
                {MOVEMENT_LABELS[m.movement_type] || m.movement_type}
                <span className="sub only-mobile">{dateTime(m.created_at)}</span>
                {(m.counterparty || m.reference_no) && (
                  <span className="sub">
                    {m.reference_type === "transfer" && m.reference_id
                      ? <Link href={`/transfers/${m.reference_id}`}>{m.reference_no}</Link> : m.reference_no}
                    {m.reference_no && m.counterparty ? " · " : ""}{m.counterparty}
                  </span>
                )}
                {m.note && <span className="sub">“{m.note}”</span>}
              </td>
              {showProduct && <td><Link href={`/products/${m.product_id}`}>{m.product_name}</Link><span className="sub">{m.sku}</span></td>}
              <td>
                {m.location_name}
                {m.batch_number && <span className="sub">Batch {m.batch_number}</span>}
                {m.serials.length > 0 && <span className="sub">SN {m.serials.join(", ")}</span>}
              </td>
              <td className="r">
                <Change v={m.quantity} unit={m.unit} />
                {m.entered_unit && m.entered_unit !== m.unit && <span className="sub">{qty(m.entered_quantity)} {m.entered_unit}</span>}
              </td>
              {!compact && <td className="r num hide-mobile">{qty(m.balance_after)}</td>}
              <td className={compact ? "hide-mobile" : ""}>{m.user_name || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
