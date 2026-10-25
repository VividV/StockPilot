"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

/** Load data from the API; re-runs when `key` changes. */
export function useApi<T>(path: string | null, query?: Record<string, unknown>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const key = path ? path + JSON.stringify(query || {}) : null;
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (!path) return;
    const n = ++seq.current;
    setLoading(true);
    try {
      const d = await api<T>(path, { query });
      if (n === seq.current) { setData(d); setError(null); }
    } catch (e) {
      if (n === seq.current) setError(e instanceof Error ? e.message : "Could not load");
    } finally {
      if (n === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => { load(); }, [load]);
  return { data, error, loading, reload: load, setData };
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
