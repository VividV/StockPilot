import { Mark } from "@/components/icons";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="auth">
      <aside className="auth-side">
        <div className="row" style={{ gap: 10 }}>
          <span className="brand-mark" style={{ width: 32, height: 32, borderRadius: 8, background: "var(--signal)", display: "grid", placeItems: "center" }}><Mark /></span>
          <span className="brand-name" style={{ color: "#fff" }}>StockPilot</span>
        </div>
        <div>
          <p className="big">Know what’s on the shelf.</p>
          <p style={{ marginTop: 16, maxWidth: "38ch", color: "#b5c3d6" }}>
            Receive, sell, move and count stock from any phone. Every change is recorded with who did it and when.
          </p>
          <div className="tag" aria-hidden="true">
            <span className="qty">24<span className="u" style={{ color: "#5c4a00" }}>pcs</span></span>
            <span style={{ fontWeight: 600 }}>Paracetamol 500mg · Main Store · reorder at 30</span>
          </div>
        </div>
        <p style={{ fontSize: 13, color: "#8496ad" }}>Retail · wholesale · clinics · sites · workshops · kitchens</p>
      </aside>
      <main className="auth-main">{children}</main>
    </div>
  );
}
