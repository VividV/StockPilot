// Till logic that must work without the internet: prices, promotions, tax,
// the product list kept on the device, and the queue of sales waiting to upload.
// The pricing mirrors backend/app/services/pos.py:price_lines exactly (to the kobo/cent),
// so a receipt printed while offline matches what the server records later.
import { api, ApiError } from "./api";

export type CatalogItem = {
  id: string; sku: string; barcode: string | null; name: string; category_id: string | null; category: string | null;
  price: number; tax_rate: number; unit: string; allow_decimals: boolean; track_serials: boolean; track_batches: boolean;
  parent_id: string | null; parent_name: string | null; attributes: Record<string, string>; image_url: string | null;
  on_hand: number; serials: string[];
};
export type Promotion = {
  id: string; name: string; kind: "percent" | "amount"; value: number; scope: "all" | "category" | "products";
  category_id: string | null; product_ids: string[]; min_qty: number; starts_on: string | null; ends_on: string | null; is_active: boolean;
};
export type PosSettings = {
  prices_include_tax: boolean; payment_methods: string[]; max_discount_percent: number; receipt_header: string;
  receipt_footer: string; paper_width: number;
};
export type ReceiptInfo = { business_name: string; header: string; footer: string; paper_width: number; prices_include_tax: boolean; currency: string; timezone: string };
export type ShiftSummary = {
  sales_count: number; sales_total: number; discounts: number; tax: number; returns_count: number; returns_total: number;
  net_sales: number; cost_of_goods: number; methods: { method: string; label: string; received: number; refunded: number; net: number }[];
  opening_float: number; cash_sales: number; cash_refunds: number; cash_in: number; cash_out: number; expected_cash: number;
  cash_movements: { id: string; kind: string; amount: number; reason: string; created_at: string; user_name: string | null }[];
  needs_review: number; synced_after_close: number;
};
export type Shift = {
  id: string; number: string; status: "open" | "closed"; location_id: string; location_name: string; warehouse_id: string;
  user_id: string | null; user_name: string | null; opening_float: number; opened_at: string; closed_at: string | null;
  counted_cash: number | null; expected_cash_at_close: number | null; difference: number | null; closing_note: string | null;
  summary?: ShiftSummary;
};
export type Boot = {
  enabled: boolean; settings: PosSettings; receipt: ReceiptInfo; methods: { key: string; label: string }[];
  locations: { location_id: string; warehouse_id: string; name: string; type: string; address: string | null }[];
  shift: Shift | null; promotions: Promotion[]; categories: { id: string; name: string }[];
  can: { discount: boolean; price_override: boolean; refund: boolean; manage: boolean; customers: boolean };
  server_time: string; today: string;
};
export type Discount = { type: "percent" | "amount"; value: number };
export type CartLine = { key: string; item: CatalogItem; qty: number; unitPrice?: number; discount?: Discount; serials: string[] };

export type ReceiptLine = {
  name: string; sku?: string; quantity: number; unit?: string; unit_price: number; list_price?: number; discount: number;
  promotion_name: string | null; tax: number; total: number; serials?: string[]; returned_qty?: number;
};
/** What a receipt needs. Both server sales and sales made offline have this shape. */
export type ReceiptData = {
  id?: string; number: string; kind: "sale" | "return"; sold_at: string; location_name: string; location_address?: string | null;
  cashier: string | null; customer_name: string | null; customer_phone?: string | null; lines: ReceiptLine[];
  subtotal: number; discount: number; tax: number; total: number;
  payments: { method: string; label: string; amount: number; reference?: string | null }[];
  amount_tendered: number; change_given: number; credit_remaining?: number; receipt: ReceiptInfo; receipt_url?: string;
  pending?: boolean; note?: string | null; original_sale?: { id: string; number: string } | null;
};

export const METHOD_LABELS: Record<string, string> = { cash: "Cash", transfer: "Bank transfer", card: "POS / card", other: "Other", credit: "Store credit" };

// ------------------------------------------------------------------ money in minor units (kobo / cents)
const k = (n: number) => Math.round(Number(n || 0) * 100);
const rnd = (x: number) => Math.floor(x + 0.5 + 1e-7);
export const fromMinor = (n: number) => n / 100;

export function promoApplies(p: Promotion, item: CatalogItem, qty: number, today: string): boolean {
  if (!p.is_active) return false;
  if (p.starts_on && p.starts_on > today) return false;
  if (p.ends_on && p.ends_on < today) return false;
  if (qty < Number(p.min_qty || 1)) return false;
  if (p.scope === "all") return true;
  if (p.scope === "category") return !!p.category_id && item.category_id === p.category_id;
  return p.product_ids.includes(item.id) || (!!item.parent_id && p.product_ids.includes(item.parent_id));
}

export type PricedLine = {
  gross: number; promo: number; promoName: string | null; manual: number; cartShare: number; discount: number; tax: number; total: number;
  unitPrice: number;
};
export type Priced = { lines: PricedLine[]; subtotal: number; discount: number; tax: number; total: number; manualDiscount: number; afterPromo: number };

/** All amounts returned in minor units (kobo). */
export function priceCart(lines: CartLine[], promos: Promotion[], opts: { inclusive: boolean; cart?: Discount | null; today: string }): Priced {
  const out = lines.map((l) => {
    const price = k(l.unitPrice ?? l.item.price);
    const gross = rnd(l.qty * price);
    let promo = 0, promoName: string | null = null;
    for (const p of promos) {
      if (!promoApplies(p, l.item, l.qty, opts.today)) continue;
      const a = p.kind === "percent" ? rnd(gross * Math.min(Number(p.value), 100) / 100) : Math.min(rnd(Number(p.value) * 100 * l.qty), gross);
      if (a > promo) { promo = a; promoName = p.name; }
    }
    const afterPromo = gross - promo;
    let manual = 0;
    if (l.discount && Number(l.discount.value) > 0) {
      const v = Number(l.discount.value);
      manual = l.discount.type === "percent" ? rnd(afterPromo * Math.min(v, 100) / 100) : Math.min(k(v), afterPromo);
    }
    return { gross, promo, promoName, afterPromo, manual, net: afterPromo - manual, unitPrice: price, rate: Number(l.item.tax_rate || 0) };
  });
  const base = out.reduce((s, x) => s + x.net, 0);
  let cart = 0;
  if (opts.cart && Number(opts.cart.value) > 0 && base > 0) {
    const v = Number(opts.cart.value);
    cart = opts.cart.type === "percent" ? rnd(base * Math.min(v, 100) / 100) : Math.min(k(v), base);
  }
  let left = cart;
  const priced: PricedLine[] = out.map((x, i) => {
    let share = i === out.length - 1 ? left : (base ? rnd(cart * x.net / base) : 0);
    share = Math.min(share, x.net, left);
    left -= share;
    const discount = x.promo + x.manual + share;
    const amount = x.gross - discount;
    let tax: number, total: number;
    if (opts.inclusive) { tax = x.rate ? rnd(amount * x.rate / (100 + x.rate)) : 0; total = amount; }
    else { tax = rnd(amount * x.rate / 100); total = amount + tax; }
    return { gross: x.gross, promo: x.promo, promoName: x.promoName, manual: x.manual, cartShare: share, discount, tax, total, unitPrice: x.unitPrice };
  });
  return {
    lines: priced,
    subtotal: priced.reduce((s, x) => s + x.gross, 0), discount: priced.reduce((s, x) => s + x.discount, 0),
    tax: priced.reduce((s, x) => s + x.tax, 0), total: priced.reduce((s, x) => s + x.total, 0),
    manualDiscount: priced.reduce((s, x) => s + x.manual + x.cartShare, 0), afterPromo: out.reduce((s, x) => s + x.afterPromo, 0),
  };
}

/** Today's date in the shop's time zone, as YYYY-MM-DD. */
export function shopToday(tz: string): string {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

/** Find a product by barcode, SKU or serial number in the list kept on this device. */
export function findByCode(items: CatalogItem[], code: string): { item: CatalogItem; serial?: string } | null {
  const c = code.trim();
  if (!c) return null;
  const lc = c.toLowerCase();
  const hit = items.find((i) => i.barcode === c) || items.find((i) => i.sku.toLowerCase() === lc);
  if (hit) return { item: hit };
  for (const i of items) if (i.track_serials && i.serials.includes(c)) return { item: i, serial: c };
  return null;
}

/** Products matching the search, things in stock first (so the cashier doesn't tap an empty shelf). */
export function searchItems(items: CatalogItem[], q: string, categoryId: string | null, limit = 60): CatalogItem[] {
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const inStock: CatalogItem[] = [], empty: CatalogItem[] = [];
  for (const i of items) {
    if (categoryId && i.category_id !== categoryId) continue;
    if (words.length) {
      const hay = `${i.name} ${i.sku} ${i.barcode || ""} ${i.category || ""}`.toLowerCase();
      if (!words.every((w) => hay.includes(w))) continue;
    }
    (i.on_hand > 0 ? inStock : empty).push(i);
    if (inStock.length >= limit) break;
  }
  return inStock.concat(empty).slice(0, limit);
}

// ------------------------------------------------------------------ storage on this device (IndexedDB)
const DB_NAME = "stockpilot-till";
let dbp: Promise<IDBDatabase> | null = null;
function idb(): Promise<IDBDatabase> {
  if (!dbp) {
    dbp = new Promise((resolve, reject) => {
      const r = indexedDB.open(DB_NAME, 1);
      r.onupgradeneeded = () => {
        r.result.createObjectStore("kv");
        r.result.createObjectStore("outbox", { keyPath: "client_id" });
      };
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }
  return dbp;
}
function tx<T>(store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return idb().then((db) => new Promise<T>((resolve, reject) => {
    const t = db.transaction(store, mode);
    const req = fn(t.objectStore(store));
    t.oncomplete = () => resolve(req.result);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  }));
}
export const kvGet = <T>(key: string) => tx<T | undefined>("kv", "readonly", (s) => s.get(key) as IDBRequest<T | undefined>).catch(() => undefined);
export const kvSet = (key: string, v: unknown) => tx("kv", "readwrite", (s) => s.put(v, key)).catch(() => undefined);

export type OutboxItem = {
  client_id: string; org_id: string; user_id: string; created_at: string; body: Record<string, unknown>;
  receipt: ReceiptData; status: "pending" | "failed"; error?: string; attempts: number;
};
export const outboxAll = () => tx<OutboxItem[]>("outbox", "readonly", (s) => s.getAll() as IDBRequest<OutboxItem[]>).catch(() => [] as OutboxItem[]);
export const outboxPut = (x: OutboxItem) => tx("outbox", "readwrite", (s) => s.put(x));
export const outboxDelete = (id: string) => tx("outbox", "readwrite", (s) => s.delete(id));

/** Network trouble (rather than the server saying no)? Then the sale is kept and sent later. */
export function isNetworkError(e: unknown): boolean {
  if (e instanceof ApiError) return e.status === 0 || e.status === 500 || e.status === 502 || e.status === 503 || e.status === 504;
  return e instanceof TypeError || (e instanceof Error && /fetch|network|load failed/i.test(e.message));
}

let syncing: Promise<{ sent: number; failed: number; left: number }> | null = null;
/** Upload sales made offline, oldest first. Safe to call often and from several places. */
export function syncOutbox(userId: string): Promise<{ sent: number; failed: number; left: number }> {
  if (syncing) return syncing;
  syncing = (async () => {
    let sent = 0, failed = 0;
    const items = (await outboxAll()).filter((x) => x.user_id === userId && x.status === "pending")
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
    for (const it of items) {
      try {
        const r = await api<{ number: string }>("/pos/sales", { body: { ...it.body, offline: true } });
        await outboxDelete(it.client_id);
        await kvSet(`synced:${it.client_id}`, r.number);
        sent++;
      } catch (e) {
        if (isNetworkError(e) || (e instanceof ApiError && (e.status === 401 || e.status === 429))) break;
        await outboxPut({ ...it, status: "failed", error: e instanceof Error ? e.message : "Upload failed", attempts: it.attempts + 1 });
        failed++;
      }
    }
    const left = (await outboxAll()).filter((x) => x.user_id === userId).length;
    return { sent, failed, left };
  })().finally(() => { setTimeout(() => (syncing = null), 0); });
  return syncing;
}

export function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
    (Number(c) ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (Number(c) / 4)))).toString(16));
}

/** Plain-text receipt for WhatsApp / SMS. */
export function receiptText(r: ReceiptData, fmtMoney: (n: number) => string): string {
  const lines = [r.receipt.business_name, `${r.kind === "return" ? "Return" : "Receipt"} ${r.number}`,
    new Date(r.sold_at).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }), ""];
  for (const l of r.lines) lines.push(`${Number(l.quantity)} x ${l.name}  ${fmtMoney(l.total)}`);
  if (Number(r.discount)) lines.push(`Discount  -${fmtMoney(r.discount)}`);
  lines.push(`TOTAL  ${fmtMoney(r.total)}`);
  for (const p of r.payments) lines.push(`${p.label}  ${fmtMoney(p.amount)}`);
  if (Number(r.change_given)) lines.push(`Change  ${fmtMoney(r.change_given)}`);
  if (r.receipt_url) lines.push("", `Receipt: ${r.receipt_url}`);
  if (r.receipt.footer) lines.push("", r.receipt.footer);
  return lines.join("\n");
}

const DIAL: Record<string, string> = { NGN: "234", GHS: "233", KES: "254", ZAR: "27", UGX: "256", TZS: "255", RWF: "250", XOF: "225" };
/** 0803 123 4567 → 2348031234567 (the form wa.me links need). */
export function waNumber(raw: string, currency: string): string {
  let d = raw.replace(/[^\d+]/g, "");
  if (d.startsWith("+")) return d.slice(1);
  if (d.startsWith("00")) return d.slice(2);
  if (d.startsWith("0") && DIAL[currency]) d = DIAL[currency] + d.slice(1);
  return d;
}
