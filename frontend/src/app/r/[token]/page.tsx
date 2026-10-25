"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { I } from "@/components/icons";
import { Receipt } from "@/components/receipt";
import type { ReceiptData } from "@/lib/pos";

/** The link customers get by WhatsApp, SMS or email. No sign-in. */
export default function PublicReceipt() {
  const { token } = useParams<{ token: string }>();
  const [r, setR] = useState<ReceiptData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    fetch(`/api/public/receipts/${encodeURIComponent(token)}`)
      .then(async (res) => { if (!res.ok) throw new Error(res.status === 404 ? "This receipt link isn’t valid." : "Couldn’t load the receipt. Try again."); setR(await res.json()); })
      .catch((e) => setErr(e.message));
  }, [token]);
  return (
    <main className="public-receipt">
      {err ? <p className="muted">{err}</p> : !r ? <p className="muted">Loading your receipt…</p> : (
        <>
          <Receipt r={r} />
          <button className="btn no-print" onClick={() => window.print()}><I.printer />Print or save as PDF</button>
        </>
      )}
    </main>
  );
}
