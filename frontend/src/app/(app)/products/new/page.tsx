"use client";
import { useRouter } from "next/navigation";
import { ProductForm } from "@/components/productform";
import { NoAccess, PageHead, useToast } from "@/components/ui";
import { useAuth } from "@/lib/auth";

export default function NewProduct() {
  const { can } = useAuth();
  const router = useRouter();
  const toast = useToast();
  if (!can("products.create")) return <NoAccess />;
  return (
    <div className="form-panel">
      <PageHead title="Add product" sub="Only the name and code are required. Everything else can be filled in later." />
      <div className="panel panel-pad">
        <ProductForm mode={{ kind: "create" }} onSaved={(p) => { toast(`Added ${p.name}`); router.push(`/products/${p.id}`); }} />
      </div>
    </div>
  );
}
