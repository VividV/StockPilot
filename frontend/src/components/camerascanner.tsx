"use client";
import { useEffect, useRef, useState } from "react";
import { I } from "./icons";

declare global {
  interface Window { BarcodeDetector?: any }
}

const FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "qr_code", "itf", "data_matrix"];

/**
 * Live camera barcode reader. Uses the browser's own detector where it exists
 * (Chrome on Android) and falls back to ZXing (Safari on iPhone, Firefox…).
 * Calls onCode once with the first code read, then stops the camera.
 */
export function CameraScanner({ onCode, onClose }: { onCode: (code: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const done = useRef(false);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let raf = 0;
    let zxingControls: { stop: () => void } | null = null;
    let stopped = false;
    const finish = (code: string) => {
      if (done.current) return;
      done.current = true;
      if (navigator.vibrate) navigator.vibrate(60);
      onCode(code.trim());
    };

    (async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        setErr("This browser can’t use the camera. Open StockPilot over https, or type the code instead.");
        return;
      }
      try {
        if (window.BarcodeDetector) {
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
          if (stopped || !video.current) return;
          video.current.srcObject = stream;
          await video.current.play();
          const supported: string[] = (await window.BarcodeDetector.getSupportedFormats?.()) || FORMATS;
          const detector = new window.BarcodeDetector({ formats: FORMATS.filter((f) => supported.includes(f)) });
          const tick = async () => {
            if (stopped || done.current || !video.current) return;
            try {
              const found = await detector.detect(video.current);
              if (found.length) return finish(found[0].rawValue);
            } catch { /* frame not ready */ }
            raf = requestAnimationFrame(tick);
          };
          tick();
        } else {
          const { BrowserMultiFormatReader } = await import("@zxing/browser");
          if (stopped || !video.current) return;
          const reader = new BrowserMultiFormatReader();
          zxingControls = await reader.decodeFromConstraints({ video: { facingMode: "environment" }, audio: false }, video.current,
            (result) => { if (result) finish(result.getText()); });
        }
      } catch (e: any) {
        setErr(e?.name === "NotAllowedError"
          ? "Camera access was blocked. Allow the camera for this site in your browser settings, or type the code instead."
          : "The camera couldn’t start. Type the code instead.");
      }
    })();
    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
      zxingControls?.stop();
      stream?.getTracks().forEach((t) => t.stop());
      const v = video.current;
      if (v?.srcObject) (v.srcObject as MediaStream).getTracks().forEach((t) => t.stop());
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="stack tight">
      {err ? <div className="notice warn">{err}</div> : (
        <div style={{ position: "relative", borderRadius: 10, overflow: "hidden", background: "#000" }}>
          <video ref={video} muted playsInline style={{ width: "100%", maxHeight: 340, display: "block", objectFit: "cover" }} />
          <div aria-hidden="true" style={{ position: "absolute", left: "12%", right: "12%", top: "50%", height: 2, background: "var(--signal)", boxShadow: "0 0 8px var(--signal)" }} />
        </div>
      )}
      <div className="spread">
        <span className="meta">{err ? "" : "Hold the barcode inside the frame"}</span>
        <button type="button" className="btn small" onClick={onClose}>Close camera</button>
      </div>
    </div>
  );
}

/** A small camera button that opens the scanner under a field. */
export function ScanButton({ onCode, label = "Scan with camera" }: { onCode: (c: string) => void; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="btn small" aria-label={label} title={label} onClick={() => setOpen((o) => !o)} aria-pressed={open}><I.camera /></button>
      {open && <div style={{ flexBasis: "100%" }}><CameraScanner onCode={(c) => { setOpen(false); onCode(c); }} onClose={() => setOpen(false)} /></div>}
    </>
  );
}
