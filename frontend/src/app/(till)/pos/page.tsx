"use client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CameraScanner } from "@/components/camerascanner";
import { I } from "@/components/icons";
import { Receipt, usePrinter } from "@/components/receipt";
import { Modal, ShiftReport, TillBar, useOnline, usePending } from "@/components/till";
import { Field, Notice, useToast } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errMsg } from "@/lib/format";
import {
  type Boot, type CartLine, type CatalogItem, type Discount, type Priced, type ReceiptData, type Shift, METHOD_LABELS,
  findByCode, fromMinor, isNetworkError, kvGet, kvSet, newId, outboxPut, priceCart, receiptText, searchItems, shopToday, waNumber,
} from "@/lib/pos";

type Pay = { key: string; method: string; amount: string; reference: string; credit_ref: string };

function useMoney(currency: string) {
  return useCallback((n: number | string) => {
    const v = Number(n || 0);
    try { return new Intl.NumberFormat(undefined, { style: "currency", currency, currencyDisplay: "narrowSymbol", maximumFractionDigits: 2 }).format(v); }
    catch { return `${currency} ${v.toFixed(2)}`; }
  }, [currency]);
}

export default function TillPage() {
  const { me, offline: signedInOffline, can } = useAuth();
  const toast = useToast();
  const online = useOnline();
  const { pending, failed, refresh: refreshPending, sync } = usePending();
  const [boot, setBoot] = useState<Boot | null>(null);
  const [bootErr, setBootErr] = useState<string | null>(null);
  const bootKey = me ? `boot:${me.organization.id}:${me.id}` : "";

  const loadBoot = useCallback(async () => {
    if (!me) return;
    try {
      const b = await api<Boot>("/pos/bootstrap");
      setBoot(b);
      kvSet(bootKey, b);
      setBootErr(null);
    } catch (e) {
      const cached = await kvGet<Boot>(bootKey);
      if (cached && isNetworkError(e)) setBoot(cached);
      else setBootErr(errMsg(e));
    }
  }, [me, bootKey]);
  useEffect(() => { loadBoot(); }, [loadBoot]);

  if (!me) return null;
  if (!can("pos.sell")) {
    return <><TillBar title="Till" online={online} /><div className="till-center"><Notice tone="warn">You don’t have permission to sell at the counter. Ask an owner or manager.</Notice></div></>;
  }
  if (bootErr) return <><TillBar title="Till" online={online} /><div className="till-center"><Notice tone="bad">{bootErr}</Notice></div></>;
  if (!boot) return <><TillBar title="Till" online={online} /><div className="till-center"><p className="muted">Loading the till…</p></div></>;
  if (!boot.enabled) {
    return <><TillBar title="Till" online={online} /><div className="till-center stack" style={{ maxWidth: 520 }}>
      <Notice tone="info">Counter sales are switched off for this business.</Notice>
      {can("settings.manage") && <p>Turn on <b>Counter sales (till)</b> in <Link href="/settings">Settings → Business → Features</Link>.</p>}
    </div></>;
  }
  if (!boot.shift || boot.shift.status !== "open") {
    return <StartShift boot={boot} online={online && !signedInOffline} onStarted={(s) => { const b = { ...boot, shift: s }; setBoot(b); kvSet(bootKey, b); }} />;
  }
  return <Till boot={boot} setBoot={(b) => { setBoot(b); kvSet(bootKey, b); }} online={online} pending={pending} failed={failed}
               refreshPending={refreshPending} sync={sync} toast={toast} reloadBoot={loadBoot} />;
}

// ------------------------------------------------------------------ start of day
function StartShift({ boot, online, onStarted }: { boot: Boot; online: boolean; onStarted: (s: Shift) => void }) {
  const [loc, setLoc] = useState(boot.locations[0]?.location_id || "");
  const [float, setFloat] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <>
      <TillBar title="Till" online={online} />
      <form className="till-center panel panel-pad stack" style={{ maxWidth: 460 }} onSubmit={async (e) => {
        e.preventDefault(); setBusy(true); setErr(null);
        try { onStarted(await api<Shift>("/pos/shifts", { body: { location_id: loc, opening_float: float || 0 } })); }
        catch (e) { setErr(isNetworkError(e) ? "You need the internet to start a shift. Once it’s started, the till keeps working offline." : errMsg(e)); }
        finally { setBusy(false); }
      }}>
        <div><h1>Start your shift</h1><p className="muted" style={{ marginTop: 6 }}>Count the cash in the drawer before your first sale. You’ll count it again when you close.</p></div>
        {err && <Notice tone="bad">{err}</Notice>}
        {boot.locations.length === 0 ? <Notice tone="warn">You aren’t assigned to any shop. Ask a manager.</Notice> : boot.locations.length > 1 && (
          <Field label="Shop" htmlFor="loc">
            <select id="loc" className="input" value={loc} onChange={(e) => setLoc(e.target.value)}>
              {boot.locations.map((l) => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
            </select>
          </Field>
        )}
        <Field label="Cash in the drawer now" htmlFor="float" hint="The change float. Enter 0 if the drawer is empty.">
          <input id="float" className="input num big" inputMode="decimal" autoFocus value={float} placeholder="0" onChange={(e) => setFloat(e.target.value)} />
        </Field>
        <button className="btn go block" disabled={busy || !loc}>{busy ? "Starting…" : "Start selling"}</button>
        <Link href="/pos/shifts" className="btn link" style={{ alignSelf: "center" }}>Past shifts</Link>
      </form>
    </>
  );
}

// ------------------------------------------------------------------ the till
function Till({ boot, setBoot, online, pending, failed, refreshPending, sync, toast, reloadBoot }: {
  boot: Boot; setBoot: (b: Boot) => void; online: boolean; pending: number; failed: number; refreshPending: () => void;
  sync: () => Promise<{ sent: number; failed: number; left: number } | null>; toast: (t: string, tone?: "bad") => void; reloadBoot: () => void;
}) {
  const { me } = useAuth();
  const shift = boot.shift!;
  const cur = boot.receipt.currency;
  const m = useMoney(cur);
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [catInfo, setCatInfo] = useState<{ at: string | null; loading: boolean }>({ at: null, loading: true });
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<string | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [cartDisc, setCartDisc] = useState<Discount | null>(null);
  const [customer, setCustomer] = useState({ name: "", phone: "" });
  const [editing, setEditing] = useState<string | null>(null);
  const [serialFor, setSerialFor] = useState<CatalogItem | null>(null);
  const [paying, setPaying] = useState(false);
  const [done, setDone] = useState<ReceiptData | null>(null);
  const [panel, setPanel] = useState<"cart" | null>(null);  // phone: basket opens over the products
  const [menu, setMenu] = useState<null | "menu" | "shift" | "cash" | "close">(null);
  const [camera, setCamera] = useState(false);
  const search = useRef<HTMLInputElement>(null);
  const catKey = `cat:${me!.organization.id}:${shift.location_id}`;
  const today = shopToday(boot.receipt.timezone);
  const { print, host } = usePrinter();

  const loadCatalog = useCallback(async () => {
    setCatInfo((c) => ({ ...c, loading: true }));
    try {
      let page = 1, all: CatalogItem[] = [], total = 0, at = "";
      do {
        const r = await api<{ items: CatalogItem[]; total: number; generated_at: string }>("/pos/catalog", { query: { location_id: shift.location_id, page, page_size: 2000 } });
        all = all.concat(r.items.map((x) => ({ ...x, price: Number(x.price), tax_rate: Number(x.tax_rate), on_hand: Number(x.on_hand) })));
        total = r.total; at = r.generated_at; page++;
      } while (all.length < total && page < 100);
      setItems(all);
      setCatInfo({ at, loading: false });
      kvSet(catKey, { items: all, at });
    } catch {
      setCatInfo((c) => ({ ...c, loading: false }));
    }
  }, [shift.location_id, catKey]);

  useEffect(() => {
    kvGet<{ items: CatalogItem[]; at: string }>(catKey).then((c) => { if (c) { setItems(c.items); setCatInfo({ at: c.at, loading: true }); } });
    loadCatalog();
    const t = setInterval(() => { if (navigator.onLine) loadCatalog(); }, 5 * 60_000);
    return () => clearInterval(t);
  }, [catKey, loadCatalog]);

  const priced: Priced = useMemo(() => priceCart(cart, boot.promotions, { inclusive: boot.settings.prices_include_tax, cart: cartDisc, today }),
    [cart, boot.promotions, boot.settings.prices_include_tax, cartDisc, today]);
  const results = useMemo(() => searchItems(items, q, cat, 60), [items, q, cat]);
  const count = cart.reduce((s, l) => s + (l.item.allow_decimals ? 1 : l.qty), 0);

  const add = useCallback((item: CatalogItem, serial?: string) => {
    if (item.track_serials && !serial) { setSerialFor(item); return; }
    setCart((c) => {
      const i = c.findIndex((l) => l.item.id === item.id);
      if (i >= 0) {
        const l = c[i];
        if (serial) {
          if (l.serials.includes(serial)) { toast(`${serial} is already in the basket`, "bad"); return c; }
          const serials = [...l.serials, serial];
          return c.map((x, j) => j === i ? { ...x, serials, qty: serials.length } : x);
        }
        return c.map((x, j) => j === i ? { ...x, qty: x.qty + 1 } : x);
      }
      return [...c, { key: newId(), item, qty: 1, serials: serial ? [serial] : [] }];
    });
    if (navigator.vibrate) navigator.vibrate(20);
  }, [toast]);

  const onCode = useCallback((code: string) => {
    const hit = findByCode(items, code);
    if (hit) { add(hit.item, hit.serial); setQ(""); return true; }
    return false;
  }, [items, add]);

  // a USB / Bluetooth scanner types the code then Enter
  const onSearchKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (!q.trim()) return;
    if (onCode(q)) return;
    if (results.length === 1) { add(results[0]); setQ(""); return; }
    toast(`Nothing found for “${q.trim()}”`, "bad");
  };

  const newSale = () => {
    setCart([]); setCartDisc(null); setCustomer({ name: "", phone: "" }); setDone(null); setPaying(false); setPanel(null);
    setTimeout(() => search.current?.focus(), 30);
  };

  const finished = (r: ReceiptData) => {
    // keep the local stock figures roughly right until the next refresh
    setItems((its) => its.map((it) => {
      const l = cart.find((x) => x.item.id === it.id);
      return l ? { ...it, on_hand: it.on_hand - l.qty, serials: it.serials.filter((s) => !l.serials.includes(s)) } : it;
    }));
    setDone(r);
    setPaying(false);
    refreshPending();
  };

  if (done) {
    return (
      <>
        <TillBar title={boot.locations.find((l) => l.location_id === shift.location_id)?.name || "Till"} online={online} pending={pending} />
        <DoneScreen r={done} boot={boot} online={online} money={m} onPrint={() => print(<Receipt r={done} />, boot.receipt.paper_width)} onNew={newSale} toast={toast} />
        {host}
      </>
    );
  }

  const shopName = boot.locations.find((l) => l.location_id === shift.location_id)?.name || shift.location_name;
  return (
    <>
      <TillBar title={shopName} sub={`${me!.full_name} · ${shift.number}`} online={online} pending={pending}>
        <button className="btn small dark" onClick={() => setMenu("menu")} aria-label="Till menu"><I.more /><span className="hide-mobile">Menu</span></button>
      </TillBar>
      {failed > 0 && <div className="till-warn"><Notice tone="bad">{failed} sale{failed !== 1 ? "s" : ""} made offline couldn’t be uploaded. <Link href="/pos/receipts?tab=device">See why</Link></Notice></div>}
      <div className={`till ${panel === "cart" ? "show-cart" : ""}`}>
        <section className="till-products" aria-label="Products">
          <div className="till-search">
            <I.search />
            <input ref={search} type="search" autoFocus value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onSearchKey}
                   placeholder="Scan or type a name, SKU or barcode" aria-label="Find a product" />
            <button type="button" className="btn small" aria-label="Scan with camera" onClick={() => setCamera((c) => !c)} aria-pressed={camera}><I.camera /></button>
          </div>
          {camera && <CameraScanner onCode={(c) => { setCamera(false); if (!onCode(c)) toast(`Nothing found for “${c}”`, "bad"); }} onClose={() => setCamera(false)} />}
          {boot.categories.length > 0 && (
            <div className="chips" role="toolbar" aria-label="Categories">
              <button className="chip" aria-pressed={cat === null} onClick={() => setCat(null)}>All</button>
              {boot.categories.map((c) => <button key={c.id} className="chip" aria-pressed={cat === c.id} onClick={() => setCat(cat === c.id ? null : c.id)}>{c.name}</button>)}
            </div>
          )}
          {items.length === 0 && catInfo.loading ? <p className="muted till-pad">Loading products…</p> :
            items.length === 0 ? <div className="till-pad"><Notice tone="info">No products to sell yet. Add products and receive stock into {shopName} first.</Notice></div> :
            results.length === 0 ? <p className="muted till-pad">Nothing matches “{q}”.</p> : (
              <div className="tiles">
                {results.map((it) => (
                  <button key={it.id} className={`tile ${it.on_hand <= 0 ? "empty-stock" : ""}`} onClick={() => add(it)}>
                    <span className="tile-name">{it.name}</span>
                    <span className="tile-foot"><b className="num">{m(it.price)}</b><span className="meta">{it.on_hand <= 0 ? "None left" : `${+it.on_hand.toFixed(2)} left`}</span></span>
                  </button>
                ))}
              </div>
            )}
          <p className="meta till-pad">{catInfo.at ? `Product list updated ${new Date(catInfo.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}{catInfo.loading && items.length ? " · refreshing…" : ""}</p>
        </section>

        <section className="till-cart" aria-label="Basket">
          <div className="cart-head">
            <h2>Basket{count ? ` (${+count.toFixed(2)})` : ""}</h2>
            <div className="row" style={{ gap: 6 }}>
              {cart.length > 0 && <button className="btn small" onClick={() => { if (confirm("Clear the basket?")) newSale(); }}>Clear</button>}
              <button className="btn small only-mobile-flex" onClick={() => setPanel(null)} aria-label="Back to products"><I.x /></button>
            </div>
          </div>
          {cart.length === 0 ? <div className="cart-empty"><I.basket size={32} /><p>Scan or tap a product to start a sale.</p></div> : (
            <ul className="cart-lines">
              {cart.map((l, i) => {
                const p = priced.lines[i];
                const short = l.qty > l.item.on_hand;
                return (
                  <li key={l.key} className={editing === l.key ? "open" : ""}>
                    <button className="cart-line" onClick={() => setEditing(editing === l.key ? null : l.key)} aria-expanded={editing === l.key}>
                      <span className="cl-qty num">{+l.qty.toFixed(3)}×</span>
                      <span className="cl-name">{l.item.name}
                        {p.promoName && <span className="sub ok-text">{p.promoName} −{m(fromMinor(p.promo))}</span>}
                        {(p.manual + p.cartShare) > 0 && <span className="sub ok-text">Discount −{m(fromMinor(p.manual + p.cartShare))}</span>}
                        {l.serials.length > 0 && <span className="sub">S/N {l.serials.join(", ")}</span>}
                        {short && <span className="sub bad-text">Only {+l.item.on_hand.toFixed(2)} in stock here</span>}
                      </span>
                      <span className="cl-total num">{m(fromMinor(p.total))}</span>
                    </button>
                    {editing === l.key && (
                      <LineEditor line={l} boot={boot} money={m}
                                  onChange={(nl) => setCart((c) => c.map((x) => x.key === l.key ? nl : x))}
                                  onRemove={() => { setCart((c) => c.filter((x) => x.key !== l.key)); setEditing(null); }} />
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {cart.length > 0 && (
            <div className="cart-foot">
              <details className="cart-extra">
                <summary>{customer.name || customer.phone ? `Customer: ${customer.name || customer.phone}` : "Add customer (optional)"}</summary>
                <div className="grid two" style={{ marginTop: 8 }}>
                  <input className="input" placeholder="Name" aria-label="Customer name" value={customer.name} onChange={(e) => setCustomer({ ...customer, name: e.target.value })} />
                  <input className="input" placeholder="Phone (for WhatsApp receipt)" inputMode="tel" aria-label="Customer phone" value={customer.phone} onChange={(e) => setCustomer({ ...customer, phone: e.target.value })} />
                </div>
              </details>
              {boot.can.discount && (
                <details className="cart-extra">
                  <summary>{cartDisc ? `Discount on whole sale: ${cartDisc.type === "percent" ? `${cartDisc.value}%` : m(cartDisc.value)}` : "Discount on whole sale"}</summary>
                  <DiscountInput value={cartDisc} onChange={setCartDisc} max={boot.can.price_override ? 100 : boot.settings.max_discount_percent} currency={cur} />
                </details>
              )}
              <div className="totals">
                {priced.discount > 0 && <div className="spread"><span>Discounts</span><span className="num ok-text">−{m(fromMinor(priced.discount))}</span></div>}
                {priced.tax > 0 && <div className="spread meta"><span>{boot.settings.prices_include_tax ? "Includes tax" : "Tax"}</span><span className="num">{m(fromMinor(priced.tax))}</span></div>}
                <div className="spread total"><span>Total</span><span className="num">{m(fromMinor(priced.total))}</span></div>
              </div>
              <button className="btn go block charge" onClick={() => setPaying(true)}>Charge {m(fromMinor(priced.total))}</button>
            </div>
          )}
        </section>
      </div>

      {/* phone: the basket sits under the thumb */}
      {panel !== "cart" && (
        <div className="till-dock only-mobile-flex">
          <button className="btn block dock-basket" onClick={() => setPanel("cart")}><I.basket />Basket {count ? `(${+count.toFixed(2)})` : ""}<b className="num">{m(fromMinor(priced.total))}</b></button>
          {cart.length > 0 && <button className="btn go block" onClick={() => setPaying(true)}>Charge</button>}
        </div>
      )}

      {serialFor && <SerialChooser item={serialFor} taken={cart.find((l) => l.item.id === serialFor.id)?.serials || []}
                                   onPick={(s) => { add(serialFor, s); setSerialFor(null); }} onClose={() => setSerialFor(null)} />}
      {paying && (
        <PaymentSheet boot={boot} shift={shift} cart={cart} priced={priced} cartDisc={cartDisc} customer={customer} online={online}
                      money={m} onClose={() => setPaying(false)} onDone={finished} toast={toast} />
      )}
      {menu && <TillMenu which={menu} setWhich={setMenu} boot={boot} shift={shift} money={m} pending={pending} sync={sync}
                         online={online} toast={toast} print={print}
                         onClosed={() => { setBoot({ ...boot, shift: null }); reloadBoot(); }} refreshCatalog={loadCatalog} />}
      {host}
    </>
  );
}

// ------------------------------------------------------------------ pieces
function DiscountInput({ value, onChange, max, currency }: { value: Discount | null; onChange: (d: Discount | null) => void; max: number; currency: string }) {
  const [type, setType] = useState<Discount["type"]>(value?.type || "percent");
  const [v, setV] = useState(value ? String(value.value) : "");
  useEffect(() => {
    const n = Number(v);
    onChange(v && n > 0 ? { type, value: n } : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, v]);
  return (
    <div className="row" style={{ marginTop: 8, flexWrap: "nowrap" }}>
      <div className="seg" role="group" aria-label="Discount type">
        <button type="button" aria-pressed={type === "percent"} onClick={() => setType("percent")}>%</button>
        <button type="button" aria-pressed={type === "amount"} onClick={() => setType("amount")}>{currency}</button>
      </div>
      <input className="input num" inputMode="decimal" placeholder={type === "percent" ? `up to ${max}%` : "Amount off"} aria-label="Discount"
             value={v} onChange={(e) => setV(e.target.value)} />
    </div>
  );
}

function LineEditor({ line, boot, money, onChange, onRemove }: {
  line: CartLine; boot: Boot; money: (n: number) => string; onChange: (l: CartLine) => void; onRemove: () => void;
}) {
  const step = line.item.allow_decimals ? 0.5 : 1;
  const setQty = (q: number) => onChange({ ...line, qty: Math.max(line.item.allow_decimals ? 0.001 : 1, +q.toFixed(3)) });
  return (
    <div className="line-edit">
      {!line.item.track_serials ? (
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <button className="btn" aria-label="One fewer" onClick={() => setQty(line.qty - step)}><I.minus /></button>
          <input className="input num" style={{ textAlign: "center", maxWidth: 110 }} inputMode="decimal" aria-label="Quantity" value={line.qty}
                 onChange={(e) => { const n = Number(e.target.value); if (n > 0) setQty(n); }} />
          <button className="btn" aria-label="One more" onClick={() => setQty(line.qty + step)}><I.plus /></button>
          <span className="meta">{line.item.unit} · {money(line.unitPrice ?? line.item.price)} each</span>
        </div>
      ) : (
        <div className="row">{line.serials.map((s) => (
          <button key={s} className="chip" onClick={() => {
            const serials = line.serials.filter((x) => x !== s);
            if (!serials.length) onRemove(); else onChange({ ...line, serials, qty: serials.length });
          }} aria-label={`Remove ${s}`}>{s} <I.x size={12} /></button>
        ))}</div>
      )}
      {boot.can.discount && (
        <div>
          <span className="label">Discount on this item</span>
          <DiscountInput value={line.discount || null} onChange={(d) => onChange({ ...line, discount: d || undefined })}
                         max={boot.can.price_override ? 100 : boot.settings.max_discount_percent} currency={boot.receipt.currency} />
        </div>
      )}
      {boot.can.price_override && (
        <Field label="Price each" htmlFor={`pr-${line.key}`} hint={`Normal price ${money(line.item.price)}`}>
          <input id={`pr-${line.key}`} className="input num" inputMode="decimal" value={line.unitPrice ?? ""} placeholder={String(line.item.price)}
                 onChange={(e) => onChange({ ...line, unitPrice: e.target.value === "" ? undefined : Number(e.target.value) })} />
        </Field>
      )}
      <button className="btn small danger" onClick={onRemove}>Remove from basket</button>
    </div>
  );
}

function SerialChooser({ item, taken, onPick, onClose }: { item: CatalogItem; taken: string[]; onPick: (s: string) => void; onClose: () => void }) {
  const [v, setV] = useState("");
  const free = item.serials.filter((s) => !taken.includes(s));
  return (
    <Modal title={`Which ${item.name}?`} onClose={onClose}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); if (v.trim()) onPick(v.trim()); }}>
        <Field label="Scan or type the serial number" htmlFor="sn">
          <input id="sn" className="input" autoFocus value={v} onChange={(e) => setV(e.target.value)} />
        </Field>
        {free.length > 0 ? <div className="row">{free.slice(0, 60).map((s) => <button type="button" key={s} className="chip" onClick={() => onPick(s)}>{s}</button>)}</div>
          : <p className="meta">No serial numbers of this product are in stock here.</p>}
      </form>
    </Modal>
  );
}

// ------------------------------------------------------------------ taking payment
function quickCash(total: number): number[] {
  const out = new Set<number>([total]);
  for (const step of [500, 1000, 5000]) out.add(Math.ceil(total / step) * step);
  for (const note of [1000, 2000, 5000, 10000, 20000]) if (note > total) out.add(note);
  return [...out].filter((x) => x >= total).sort((a, b) => a - b).slice(0, 5);
}

function PaymentSheet({ boot, shift, cart, priced, cartDisc, customer, online, money, onClose, onDone, toast }: {
  boot: Boot; shift: Shift; cart: CartLine[]; priced: Priced; cartDisc: Discount | null; customer: { name: string; phone: string };
  online: boolean; money: (n: number) => string; onClose: () => void; onDone: (r: ReceiptData) => void; toast: (t: string, tone?: "bad") => void;
}) {
  const { me } = useAuth();
  const total = fromMinor(priced.total);
  const methods = boot.methods.map((x) => x.key);
  const [pays, setPays] = useState<Pay[]>([{ key: newId(), method: methods[0] || "cash", amount: String(total), reference: "", credit_ref: "" }]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const clientId = useRef(newId());
  const sum = pays.reduce((s, p) => s + (Number(p.amount) || 0), 0);
  const cash = pays.filter((p) => p.method === "cash").reduce((s, p) => s + (Number(p.amount) || 0), 0);
  const nonCash = +(sum - cash).toFixed(2);
  const left = +(total - sum).toFixed(2);
  const change = Math.max(0, Math.min(-left, cash));
  const problem = left > 0.004 ? `${money(left)} still to pay` : nonCash > total + 0.004 ? "Transfer and card payments can’t be more than the total. Only cash can be more (for change)." : null;
  const set = (key: string, patch: Partial<Pay>) => setPays((ps) => ps.map((p) => p.key === key ? { ...p, ...patch } : p));

  const submit = async () => {
    setBusy(true); setErr(null);
    const body = {
      client_id: clientId.current, shift_id: shift.id, sold_at: new Date().toISOString(),
      customer_name: customer.name || null, customer_phone: customer.phone || null, cart_discount: cartDisc,
      lines: cart.map((l) => ({ product_id: l.item.id, quantity: l.qty, unit_price: l.unitPrice ?? undefined, discount: l.discount || undefined,
                                serials: l.serials.length ? l.serials : undefined })),
      payments: pays.filter((p) => Number(p.amount) > 0).map((p) => ({ method: p.method, amount: Number(p.amount),
                                                                         reference: p.reference || undefined, credit_ref: p.credit_ref || undefined })),
    };
    const local = (): ReceiptData => ({
      number: `OFFLINE-${clientId.current.slice(0, 6).toUpperCase()}`, kind: "sale", sold_at: body.sold_at, pending: true,
      location_name: boot.locations.find((l) => l.location_id === shift.location_id)?.name || shift.location_name,
      cashier: me!.full_name, customer_name: customer.name || null, customer_phone: customer.phone || null,
      lines: cart.map((l, i) => ({ name: l.item.name, quantity: l.qty, unit_price: fromMinor(priced.lines[i].unitPrice), discount: fromMinor(priced.lines[i].discount),
                                   promotion_name: priced.lines[i].promoName, tax: fromMinor(priced.lines[i].tax), total: fromMinor(priced.lines[i].total), serials: l.serials })),
      subtotal: fromMinor(priced.subtotal), discount: fromMinor(priced.discount), tax: fromMinor(priced.tax), total,
      payments: [
        ...(cash - change > 0 ? [{ method: "cash", label: "Cash", amount: +(cash - change).toFixed(2) }] : []),
        ...body.payments.filter((p) => p.method !== "cash").map((p) => ({ method: p.method, label: METHOD_LABELS[p.method] || p.method, amount: p.amount, reference: p.reference })),
      ],
      amount_tendered: sum, change_given: change, receipt: boot.receipt,
    });
    const keep = async () => {
      await outboxPut({ client_id: clientId.current, org_id: me!.organization.id, user_id: me!.id, created_at: body.sold_at,
                        body, receipt: local(), status: "pending", attempts: 0 });
      toast("No internet: the sale is saved on this device and will upload by itself.");
      onDone(local());
    };
    try {
      if (!online) { await keep(); return; }
      onDone(await api<ReceiptData>("/pos/sales", { body }));
    } catch (e) {
      if (isNetworkError(e)) { await keep(); return; }
      setErr(e instanceof ApiError ? e.message : errMsg(e));
    } finally { setBusy(false); }
  };

  return (
    <Modal title="Take payment" onClose={onClose}>
      <div className="stack">
        <div className="due"><span>To pay</span><b className="num">{money(total)}</b></div>
        {err && <Notice tone="bad">{err}</Notice>}
        {pays.map((p, i) => (
          <div key={p.key} className="pay-row stack tight">
            <div className="seg wide" role="group" aria-label="How is the customer paying?">
              {[...methods, "credit"].map((mk) => (
                <button key={mk} type="button" aria-pressed={p.method === mk} onClick={() => set(p.key, { method: mk })}>{METHOD_LABELS[mk] || mk}</button>
              ))}
            </div>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input className="input num big" inputMode="decimal" aria-label="Amount" value={p.amount} autoFocus={i === pays.length - 1}
                     onChange={(e) => set(p.key, { amount: e.target.value })} onFocus={(e) => e.target.select()} />
              {pays.length > 1 && <button className="btn" aria-label="Remove this payment" onClick={() => setPays((ps) => ps.filter((x) => x.key !== p.key))}><I.x /></button>}
            </div>
            {p.method === "cash" && (
              <div className="row">{quickCash(Math.max(0, +(total - (sum - (Number(p.amount) || 0))).toFixed(2))).map((v) => (
                <button key={v} type="button" className="chip" onClick={() => set(p.key, { amount: String(v) })}>{money(v)}</button>
              ))}</div>
            )}
            {(p.method === "transfer" || p.method === "card") && (
              <input className="input" placeholder={p.method === "transfer" ? "Bank / sender name (optional)" : "POS slip number (optional)"}
                     aria-label="Payment reference" value={p.reference} onChange={(e) => set(p.key, { reference: e.target.value })} />
            )}
            {p.method === "credit" && (
              <input className="input" placeholder="Return receipt number, e.g. RET-00012" aria-label="Store credit receipt"
                     value={p.credit_ref} onChange={(e) => set(p.key, { credit_ref: e.target.value.toUpperCase() })} />
            )}
          </div>
        ))}
        {left > 0.004 && (
          <button className="btn small" style={{ alignSelf: "flex-start" }} onClick={() => setPays((ps) => [...ps, { key: newId(), method: methods.find((x) => x !== ps[ps.length - 1]?.method) || "cash", amount: String(left), reference: "", credit_ref: "" }])}>
            <I.plus />Split: pay the rest another way
          </button>
        )}
        <div className="due small">
          {change > 0 ? <><span>Give change</span><b className="num">{money(change)}</b></> : left > 0.004 ? <><span>Still to pay</span><b className="num bad-text">{money(left)}</b></> : <><span>Paid in full</span><b className="num ok-text">{money(sum)}</b></>}
        </div>
        {!online && <Notice tone="warn">You’re offline. The sale will be saved on this device and uploaded when the internet is back.</Notice>}
        <button className="btn go block charge" disabled={busy || !!problem} onClick={submit}>{busy ? "Saving…" : problem || "Complete sale"}</button>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ after the sale
function DoneScreen({ r, boot, online, money, onPrint, onNew, toast }: {
  r: ReceiptData; boot: Boot; online: boolean; money: (n: number) => string; onPrint: () => void; onNew: () => void;
  toast: (t: string, tone?: "bad") => void;
}) {
  const [phone, setPhone] = useState(r.customer_phone || "");
  const [email, setEmail] = useState("");
  const newBtn = useRef<HTMLButtonElement>(null);
  useEffect(() => { newBtn.current?.focus(); }, []);
  const wa = () => {
    const n = waNumber(phone, boot.receipt.currency);
    window.open(`https://wa.me/${n}?text=${encodeURIComponent(receiptText(r, money))}`, "_blank", "noopener");
  };
  const send = async (channel: "sms" | "email", to: string) => {
    try { toast((await api<{ message: string }>(`/pos/sales/${r.id}/send`, { body: { channel, to } })).message); }
    catch (e) { toast(errMsg(e), "bad"); }
  };
  return (
    <div className="done">
      <div className="done-head">
        <span className="done-mark"><I.check size={28} /></span>
        <div>
          <h1>{Number(r.change_given) > 0 ? <>Give change: <span className="num">{money(r.change_given)}</span></> : "Sale complete"}</h1>
          <p className="muted">{r.pending ? "Saved on this device. It will upload by itself." : `Receipt ${r.number}`} · {money(r.total)}</p>
        </div>
      </div>
      <div className="done-body">
        <div className="done-receipt"><Receipt r={r} /></div>
        <div className="stack done-actions">
          <button ref={newBtn} className="btn go block" onClick={onNew}>New sale</button>
          <button className="btn block" onClick={onPrint}><I.printer />Print receipt</button>
          <div className="panel panel-pad stack tight">
            <span className="label">Send the receipt</span>
            <input className="input" inputMode="tel" placeholder="Customer’s phone" aria-label="Customer phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
            <div className="row">
              <button className="btn small" disabled={!phone} onClick={wa}><I.message />WhatsApp</button>
              {!r.pending && online && <button className="btn small" disabled={!phone} onClick={() => send("sms", phone)}>Text message</button>}
            </div>
            {!r.pending && online && (
              <div className="row" style={{ flexWrap: "nowrap" }}>
                <input className="input" type="email" placeholder="or email address" aria-label="Customer email" value={email} onChange={(e) => setEmail(e.target.value)} />
                <button className="btn small" disabled={!email.includes("@")} onClick={() => send("email", email)}>Email</button>
              </div>
            )}
            <span className="meta">WhatsApp opens on this phone or computer with the receipt typed in; just press send.</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ menu: shift, cash in/out, close
function TillMenu({ which, setWhich, boot, shift, money, pending, sync, online, toast, print, onClosed, refreshCatalog }: {
  which: "menu" | "shift" | "cash" | "close"; setWhich: (w: null | "menu" | "shift" | "cash" | "close") => void; boot: Boot; shift: Shift;
  money: (n: number) => string; pending: number; sync: () => Promise<{ sent: number; failed: number; left: number } | null>; online: boolean;
  toast: (t: string, tone?: "bad") => void; print: (n: React.ReactNode, w?: number) => void; onClosed: () => void; refreshCatalog: () => void;
}) {
  const close = () => setWhich(null);
  const [detail, setDetail] = useState<Shift | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setDetail(await api<Shift>(`/pos/shifts/${shift.id}`)); setErr(null); }
    catch (e) { setErr(isNetworkError(e) ? "You’re offline. Shift totals need the internet." : errMsg(e)); }
  }, [shift.id]);
  useEffect(() => { if (which !== "menu") load(); }, [which, load]);

  if (which === "menu") {
    return (
      <Modal title="Till" onClose={close}>
        <div className="menu-list">
          <button onClick={() => setWhich("shift")}><I.receipt />Shift totals so far</button>
          <Link href="/pos/receipts"><I.history />Receipts and returns</Link>
          <button onClick={() => setWhich("cash")}><I.card />Cash in / cash out</button>
          <button onClick={async () => { const r = await sync(); refreshCatalog(); toast(r && r.sent ? `Uploaded ${r.sent} sale(s)` : online ? "Everything is up to date" : "Still offline"); close(); }}>
            <I.upload />Upload and refresh now{pending ? ` (${pending} waiting)` : ""}</button>
          <button onClick={() => setWhich("close")}><I.adjust />Close shift and count cash</button>
          <Link href="/"><I.back />Back to StockPilot</Link>
        </div>
      </Modal>
    );
  }
  if (which === "cash") return <CashMove shift={shift} money={money} onDone={() => { toast("Saved"); close(); }} onClose={close} />;
  if (which === "close") return <CloseShift shift={shift} detail={detail} err={err} money={money} pending={pending} sync={sync} print={print} boot={boot} onClose={close} onClosed={onClosed} />;
  return (
    <Modal title={`Shift ${shift.number}`} onClose={close} wide>
      {err ? <Notice tone="warn">{err}</Notice> : !detail?.summary ? <p className="muted">Loading…</p> : <ShiftReport s={detail} money={money} />}
    </Modal>
  );
}

function CashMove({ shift, money, onDone, onClose }: { shift: Shift; money: (n: number) => string; onDone: () => void; onClose: () => void }) {
  const [kind, setKind] = useState<"in" | "out">("out");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <Modal title="Cash in / cash out" onClose={onClose}>
      <form className="stack" onSubmit={async (e) => {
        e.preventDefault(); setErr(null);
        try { await api(`/pos/shifts/${shift.id}/cash`, { body: { kind, amount, reason } }); onDone(); }
        catch (e) { setErr(isNetworkError(e) ? "This needs the internet. Try again when you’re back online." : errMsg(e)); }
      }}>
        <p className="meta">Money that goes into or out of the drawer without a sale, so the cash count still adds up.</p>
        {err && <Notice tone="bad">{err}</Notice>}
        <div className="seg wide" role="group" aria-label="In or out">
          <button type="button" aria-pressed={kind === "out"} onClick={() => setKind("out")}>Cash out (paid from the drawer)</button>
          <button type="button" aria-pressed={kind === "in"} onClick={() => setKind("in")}>Cash in (added to the drawer)</button>
        </div>
        <Field label="Amount" htmlFor="ca"><input id="ca" className="input num big" inputMode="decimal" required value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
        <Field label="What for?" htmlFor="cr"><input id="cr" className="input" required placeholder={kind === "out" ? "e.g. Diesel for the generator" : "e.g. Change from the bank"} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <button className="btn primary block">Save {amount ? money(Number(amount)) : ""}</button>
      </form>
    </Modal>
  );
}

function CloseShift({ shift, detail, err, money, pending, sync, print, boot, onClose, onClosed }: {
  shift: Shift; detail: Shift | null; err: string | null; money: (n: number) => string; pending: number;
  sync: () => Promise<{ sent: number; failed: number; left: number } | null>; print: (n: React.ReactNode, w?: number) => void; boot: Boot;
  onClose: () => void; onClosed: () => void;
}) {
  const [counted, setCounted] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [e2, setE2] = useState<string | null>(null);
  const [closed, setClosed] = useState<Shift | null>(null);
  const expected = detail?.summary?.expected_cash ?? null;
  const diff = counted !== "" && expected !== null ? +(Number(counted) - Number(expected)).toFixed(2) : null;
  if (closed) {
    return (
      <Modal title={`Shift ${closed.number} closed`} onClose={onClosed} wide>
        <div className="stack">
          <ShiftReport s={closed} money={money} />
          <div className="row">
            <button className="btn" onClick={() => print(<div className="receipt w80"><div className="rc-biz rc-center">{boot.receipt.business_name}</div><div className="rc-center">Cash-up {closed.number}</div><ShiftReport s={closed} money={money} /></div>, boot.receipt.paper_width)}><I.printer />Print cash-up</button>
            <button className="btn primary" onClick={onClosed}>Done</button>
          </div>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title="Close shift" onClose={onClose} wide>
      <form className="stack" onSubmit={async (ev) => {
        ev.preventDefault(); setBusy(true); setE2(null);
        try { setClosed(await api<Shift>(`/pos/shifts/${shift.id}/close`, { body: { counted_cash: counted, note: note || null } })); }
        catch (e) { setE2(isNetworkError(e) ? "Closing a shift needs the internet." : errMsg(e)); }
        finally { setBusy(false); }
      }}>
        {pending > 0 && (
          <Notice tone="warn">{pending} sale(s) made offline haven’t uploaded yet, so the totals below don’t include them.{" "}
            <button type="button" className="btn link" onClick={() => sync()}>Upload now</button></Notice>
        )}
        {err && <Notice tone="warn">{err}</Notice>}
        {e2 && <Notice tone="bad">{e2}</Notice>}
        {detail?.summary && <ShiftReport s={detail} money={money} />}
        <Field label="Count the cash in the drawer" htmlFor="cc" hint="Count every note and coin, including the float you started with.">
          <input id="cc" className="input num big" inputMode="decimal" required value={counted} onChange={(e) => setCounted(e.target.value)} />
        </Field>
        {diff !== null && (
          <Notice tone={diff === 0 ? "ok" : "warn"}>{diff === 0 ? "The drawer matches exactly." : diff < 0 ? `The drawer is short by ${money(-diff)}.` : `The drawer is over by ${money(diff)}.`}</Notice>
        )}
        {diff !== null && diff !== 0 && (
          <Field label="What happened? (optional)" htmlFor="cn"><input id="cn" className="input" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        )}
        <button className="btn primary block" disabled={busy || counted === ""}>{busy ? "Closing…" : "Close shift"}</button>
      </form>
    </Modal>
  );
}
