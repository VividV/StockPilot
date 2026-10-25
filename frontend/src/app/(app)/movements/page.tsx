"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
// Search results link here; history lives on the Inventory page.
export default function Movements() {
  const router = useRouter();
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("q") || "";
    router.replace(`/inventory?tab=movements&q=${encodeURIComponent(q)}`);
  }, [router]);
  return null;
}
