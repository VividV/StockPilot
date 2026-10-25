export function qty(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "0";
  const n = Number(v);
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

export function money(v: string | number | null | undefined, currency = "USD"): string {
  const n = Number(v || 0);
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, currencyDisplay: "narrowSymbol", maximumFractionDigits: 2 }).format(n);
  } catch {
    return `${currency} ${n.toFixed(2)}`;
  }
}

export function date(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v.length === 10 ? v + "T00:00:00" : v);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function ago(v: string): string {
  const s = (Date.now() - new Date(v).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
  return date(v);
}

// Plain-language names: staff never need to know internal type codes.
export const MOVEMENT_LABELS: Record<string, string> = {
  opening_balance: "Opening stock",
  purchase_receipt: "Received",
  sale: "Sold",
  issue: "Issued",
  return: "Returned",
  adjustment_in: "Count: added",
  adjustment_out: "Count: removed",
  transfer_out: "Sent",
  transfer_in: "Arrived",
  damaged: "Damaged",
  lost: "Lost",
  expired: "Expired",
  production_consumption: "Used in production",
  production_output: "Produced",
};

export const TRANSFER_STATUS: Record<string, { label: string; tone: string }> = {
  draft: { label: "Draft", tone: "" },
  requested: { label: "Waiting for approval", tone: "warn" },
  approved: { label: "Approved", tone: "info" },
  in_transit: { label: "On the way", tone: "info" },
  received: { label: "Received", tone: "ok" },
  cancelled: { label: "Cancelled", tone: "out" },
};

export const WAREHOUSE_TYPES: Record<string, string> = {
  warehouse: "Warehouse", store: "Shop / store", branch: "Branch", site: "Site", vehicle: "Vehicle",
  technician: "Technician stock", production: "Production",
};

export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : "Something went wrong. Try again.";
}
