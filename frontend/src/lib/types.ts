export type UUID = string;

export interface Unit { id: UUID; name: string; abbreviation: string; allow_decimals: boolean }
export interface Ref { id: UUID; name: string }

export interface Product {
  id: UUID; sku: string; barcode: string | null; name: string; description: string | null;
  category: Ref | null; brand: string | null; base_unit: Unit;
  cost_price: string; selling_price: string; average_cost: string; tax_rate: string;
  min_stock: string; reorder_level: string; reorder_qty: string; preferred_supplier: Ref | null;
  image_url: string | null; is_active: boolean; track_batches: boolean; track_expiry: boolean;
  track_serials: boolean; has_variants: boolean; created_at: string;
  parent_id: UUID | null; variant_options: Record<string, string[]>; variant_attributes: Record<string, string>;
  variant_count: number | null;
  on_hand: string | null; stock_status: "in_stock" | "low" | "out" | null;
}

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }

export interface Movement {
  id: number; created_at: string; movement_type: string; product_id: UUID; product_name: string; sku: string;
  unit: string; location_id: UUID; location_name: string; warehouse_name: string; batch_number: string | null;
  quantity: string; balance_after: string; unit_cost: string | null; entered_quantity: string | null;
  entered_unit: string | null; reference_type: string | null; reference_id: UUID | null; reference_no: string | null;
  counterparty: string | null; note: string | null; user_name: string | null; serials: string[];
}

export interface LocationOption { id: UUID; label: string; warehouse_id: UUID; warehouse_name: string; warehouse_type: string; kind: string; name: string }

export interface Warehouse {
  id: UUID; name: string; code: string; type: string; address: string | null; is_active: boolean;
  default_location_id: UUID | null; location_count: number; stock_units: string; stock_value: string;
}

export interface StockRow {
  product_id: UUID; sku: string; barcode: string | null; product_name: string; unit: string; warehouse_id: UUID;
  warehouse_name: string; location_id: UUID; location_name: string; batch_id: UUID | null; batch_number: string | null;
  expires_on: string | null; quantity: string; value: string;
}

export interface Partner { id: UUID; name: string; contact_name: string | null; email: string | null; phone: string | null; address: string | null; notes: string | null; is_active: boolean; created_at: string }

export interface ProductDetail {
  product: Product; variants: Product[]; parent: { id: UUID; name: string; sku: string; variant_options: Record<string, string[]> } | null;
  matched_by: string | null; serial: { serial: string; status: string } | null; on_hand: string;
  locations: { location_id: UUID; location_name: string; warehouse_id: UUID; quantity: string;
    batches: { batch_id: UUID; batch_number: string; expires_on: string | null; quantity: string }[] }[];
  conversions: { id: UUID; unit: Unit; factor: string }[];
  serials_in_stock: { id: UUID; serial: string; location_id: UUID | null }[];
  recent_movements: Movement[];
}

export interface TransferItem { id: UUID; product_id: UUID; product_name: string; sku: string; unit: string; batch_id: UUID | null; requested_qty: string; dispatched_qty: string; received_qty: string; remaining_qty: string; available_qty: string | null }
export interface Transfer { id: UUID; number: string; status: string; from_location_id: UUID; from_name: string; to_location_id: UUID; to_name: string; notes: string | null; created_at: string; dispatched_at: string | null; received_at: string | null; items: TransferItem[] }

export interface Me {
  id: UUID; email: string; full_name: string; role: string; email_verified: boolean; totp_enabled: boolean;
  phone: string | null; notification_prefs: Record<string, unknown>; permissions: string[]; warehouse_ids: UUID[];
  organization: { id: UUID; name: string; industry: string; currency: string; settings: OrgSettings };
}
export interface OrgSettings { allow_negative_stock?: boolean; modules?: Record<string, boolean> }

export interface NotificationItem { id: UUID; type: string; severity: string; title: string; body: string | null; entity_type: string | null; entity_id: string | null; is_read: boolean; is_resolved: boolean; created_at: string }
