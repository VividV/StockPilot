"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { MovementTable } from "@/components/movements";
import { Loading, PageHead } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import type { Movement } from "@/lib/types";

export default function SerialPage() {
  const { id } = useParams<{ id: string }>();
  const { data } = useApi<{ serial: string; status: string; product_id: string; product_name: string; history: Movement[] }>(`/serials/${id}/history`);
  if (!data) return <Loading />;
  return (
    <>
      <PageHead title={`Serial ${data.serial}`} sub={<><Link href={`/products/${data.product_id}`}>{data.product_name}</Link> · <span className={`badge ${data.status === "in_stock" ? "ok" : "out"}`}>{data.status.replace("_", " ")}</span></>} />
      <div className="panel"><MovementTable rows={data.history} showProduct={false} /></div>
    </>
  );
}
