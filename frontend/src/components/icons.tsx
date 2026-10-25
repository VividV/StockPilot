// Inline stroke icons (no icon library dependency).
type P = { size?: number };
const S = ({ d, size = 18 }: { d: string } & P) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9}
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);

export const I = {
  dashboard: (p: P) => <S {...p} d="M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z" />,
  inventory: (p: P) => <S {...p} d="M21 8l-9-5-9 5 9 5 9-5zM3 8v8l9 5 9-5V8M12 13v8" />,
  products: (p: P) => <S {...p} d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8zM7.5 7.5h.01" />,
  transfers: (p: P) => <S {...p} d="M7 7h13l-4-4M17 17H4l4 4" />,
  warehouses: (p: P) => <S {...p} d="M3 21V8l9-5 9 5v13M7 21v-8h10v8M7 17h10" />,
  suppliers: (p: P) => <S {...p} d="M1 6h13v10H1zM14 9h4l3 3v4h-7M5.5 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM17.5 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z" />,
  customers: (p: P) => <S {...p} d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8" />,
  reports: (p: P) => <S {...p} d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  settings: (p: P) => <S {...p} d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />,
  purchases: (p: P) => <S {...p} d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4zM3 6h18M16 10a4 4 0 0 1-8 0" />,
  sales: (p: P) => <S {...p} d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />,
  production: (p: P) => <S {...p} d="M2 20h20M4 20V10l5 3V10l5 3V10l5 3v7M17 4h3v6" />,
  label: (p: P) => <S {...p} d="M3 5h18v14H3zM7 9v6M10 9v6M13 9v6M16 9v6" />,
  upload: (p: P) => <S {...p} d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />,
  search: (p: P) => <S {...p} d="M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3" />,
  bell: (p: P) => <S {...p} d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0" />,
  plus: (p: P) => <S {...p} d="M12 5v14M5 12h14" />,
  receive: (p: P) => <S {...p} d="M12 3v12M7 10l5 5 5-5M4 21h16" />,
  issue: (p: P) => <S {...p} d="M12 21V9M7 14l5-5 5 5M4 3h16" />,
  adjust: (p: P) => <S {...p} d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" />,
  scan: (p: P) => <S {...p} d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 8v8M11 8v8M15 8v8M18 8v8" />,
  x: (p: P) => <S {...p} d="M18 6 6 18M6 6l12 12" />,
  logout: (p: P) => <S {...p} d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />,
  more: (p: P) => <S {...p} d="M4 6h16M4 12h16M4 18h16" />,
  history: (p: P) => <S {...p} d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l4 2" />,
  camera: (p: P) => <S {...p} d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z" />,
  download: (p: P) => <S {...p} d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />,
  till: (p: P) => <S {...p} d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4zM3 6h18M8 11h8M8 15h5" />,
  basket: (p: P) => <S {...p} d="M5 11h14l-1.5 9h-11zM9 11 12 4l3 7M3 11h18" />,
  printer: (p: P) => <S {...p} d="M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v8H6z" />,
  message: (p: P) => <S {...p} d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.6 8.6 0 0 1-3.8-.9L3 21l1.9-5.2A8.4 8.4 0 1 1 21 11.5z" />,
  receipt: (p: P) => <S {...p} d="M5 2v20l2-1.5L9 22l2-1.5 2 1.5 2-1.5 2 1.5 2-1.5V2l-2 1.5L15 2l-2 1.5L11 2 9 3.5 7 2zM9 8h6M9 12h6M9 16h4" />,
  cloudOff: (p: P) => <S {...p} d="M22.6 17.4A5 5 0 0 0 18 10h-1.3A8 8 0 0 0 9 4.4M5 5a8 8 0 0 0 4 15h9a5 5 0 0 0 1.7-.3M1 1l22 22" />,
  card: (p: P) => <S {...p} d="M3 5h18a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zM1 10h22M5 15h4" />,
  trend: (p: P) => <S {...p} d="M23 6l-9.5 9.5-5-5L1 18M17 6h6v6" />,
  minus: (p: P) => <S {...p} d="M5 12h14" />,
  tag: (p: P) => <S {...p} d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8zM7.5 7.5h.01" />,
  back: (p: P) => <S {...p} d="M19 12H5M12 19l-7-7 7-7" />,
  check: (p: P) => <S {...p} d="M20 6 9 17l-5-5" />,
};

export function Mark() {
  // A stacked pallet seen from the side: the product in one shape.
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="4" width="16" height="6" rx="1" fill="#13233a" />
      <rect x="4" y="12" width="16" height="6" rx="1" fill="#13233a" opacity=".6" />
      <rect x="3" y="19.5" width="18" height="2" rx="1" fill="#13233a" />
    </svg>
  );
}
