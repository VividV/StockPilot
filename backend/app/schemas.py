import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import LocationKind, MovementType, TransferStatus, WarehouseType

T = TypeVar("T")

Str200 = Field(min_length=1, max_length=200)


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class Message(BaseModel):
    message: str


# --------------------------------------------------------------------- auth
class SignupIn(BaseModel):
    organization_name: str = Str200
    industry: str = "other"
    currency: str = Field("USD", min_length=3, max_length=3)
    full_name: str = Str200
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    timezone: str | None = Field(None, max_length=64)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class TokenOut(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"
    expires_in: int | None = None
    two_factor_required: bool = False
    challenge_token: str | None = None   # send with the 6-digit code to /api/auth/login/2fa


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


class OrgOut(ORM):
    id: uuid.UUID
    name: str
    industry: str
    currency: str
    settings: dict


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    email_verified: bool = False
    totp_enabled: bool = False
    phone: str | None = None
    notification_prefs: dict = {}
    role: str
    permissions: list[str]
    warehouse_ids: list[uuid.UUID]
    organization: OrgOut


# --------------------------------------------------------------------- catalogue
class UnitIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    abbreviation: str = Field(min_length=1, max_length=15)
    allow_decimals: bool = False


class UnitOut(ORM):
    id: uuid.UUID
    name: str
    abbreviation: str
    allow_decimals: bool


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: uuid.UUID | None = None


class CategoryOut(ORM):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None


class ConversionIn(BaseModel):
    unit_id: uuid.UUID
    factor: Decimal = Field(gt=0)


class ConversionOut(ORM):
    id: uuid.UUID
    unit: UnitOut
    factor: Decimal


class ProductBase(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    barcode: str | None = Field(None, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    category_id: uuid.UUID | None = None
    brand: str | None = Field(None, max_length=120)
    base_unit_id: uuid.UUID | None = None
    cost_price: Decimal = Field(Decimal(0), ge=0)
    selling_price: Decimal = Field(Decimal(0), ge=0)
    tax_rate: Decimal = Field(Decimal(0), ge=0, le=100)
    min_stock: Decimal = Field(Decimal(0), ge=0)
    reorder_level: Decimal = Field(Decimal(0), ge=0)
    reorder_qty: Decimal = Field(Decimal(0), ge=0)
    preferred_supplier_id: uuid.UUID | None = None
    image_url: str | None = Field(None, max_length=500)
    is_active: bool = True
    track_batches: bool | None = None
    track_expiry: bool | None = None
    track_serials: bool = False
    has_variants: bool = False

    @field_validator("sku", "barcode", "name", mode="before")
    @classmethod
    def strip(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("image_url")
    @classmethod
    def safe_url(cls, v):
        if v and not v.startswith(("https://", "http://", "/")):
            raise ValueError("Image must be a web address")
        return v


class ProductCreate(ProductBase):
    opening_quantity: Decimal | None = Field(None, gt=0, description="Optional: stock already on the shelf")
    opening_location_id: uuid.UUID | None = None
    variant_options: dict[str, list[str]] | None = Field(
        None, description='Create sizes/colours at once, e.g. {"Size": ["S","M"], "Colour": ["Red"]}')


class ProductUpdate(BaseModel):
    sku: str | None = Field(None, min_length=1, max_length=64)
    barcode: str | None = Field(None, max_length=64)
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    category_id: uuid.UUID | None = None
    brand: str | None = None
    cost_price: Decimal | None = Field(None, ge=0)
    selling_price: Decimal | None = Field(None, ge=0)
    tax_rate: Decimal | None = Field(None, ge=0, le=100)
    min_stock: Decimal | None = Field(None, ge=0)
    reorder_level: Decimal | None = Field(None, ge=0)
    reorder_qty: Decimal | None = Field(None, ge=0)
    preferred_supplier_id: uuid.UUID | None = None
    image_url: str | None = None
    is_active: bool | None = None
    track_batches: bool | None = None
    track_expiry: bool | None = None
    track_serials: bool | None = None
    apply_to_variants: bool = Field(False, description="Copy price, category, reorder levels… to every variant")


class Ref(ORM):
    id: uuid.UUID
    name: str


class ProductOut(ORM):
    id: uuid.UUID
    sku: str
    barcode: str | None
    name: str
    description: str | None
    category: Ref | None
    brand: str | None
    base_unit: UnitOut
    cost_price: Decimal
    selling_price: Decimal
    average_cost: Decimal
    tax_rate: Decimal
    min_stock: Decimal
    reorder_level: Decimal
    reorder_qty: Decimal
    preferred_supplier: Ref | None
    image_url: str | None
    is_active: bool
    track_batches: bool
    track_expiry: bool
    track_serials: bool
    has_variants: bool
    parent_id: uuid.UUID | None = None
    variant_options: dict = {}
    variant_attributes: dict = {}
    created_at: datetime
    on_hand: Decimal | None = None
    stock_status: str | None = None  # in_stock | low | out
    variant_count: int | None = None


# --------------------------------------------------------------------- places
class WarehouseIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    code: str | None = Field(None, max_length=20)
    type: WarehouseType = WarehouseType.WAREHOUSE
    address: str | None = None
    is_active: bool = True


class WarehouseUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    code: str | None = Field(None, max_length=20)
    type: WarehouseType | None = None
    address: str | None = None
    is_active: bool | None = None


class WarehouseOut(ORM):
    id: uuid.UUID
    name: str
    code: str
    type: WarehouseType
    address: str | None
    is_active: bool
    default_location_id: uuid.UUID | None = None
    location_count: int = 0
    stock_units: Decimal = Decimal(0)
    stock_value: Decimal = Decimal(0)


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(None, max_length=40)
    kind: LocationKind = LocationKind.BIN
    parent_id: uuid.UUID | None = None


class LocationOut(ORM):
    id: uuid.UUID
    warehouse_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    code: str
    kind: LocationKind
    is_active: bool
    full_name: str | None = None


# --------------------------------------------------------------------- partners
class PartnerIn(BaseModel):
    name: str = Str200
    contact_name: str | None = Field(None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    address: str | None = None
    notes: str | None = None
    is_active: bool = True

    @field_validator("email", mode="before")
    @classmethod
    def empty_email(cls, v):
        return v or None


class PartnerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    contact_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    address: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class PartnerOut(ORM):
    id: uuid.UUID
    name: str
    contact_name: str | None
    email: str | None
    phone: str | None
    address: str | None
    notes: str | None
    is_active: bool
    created_at: datetime


# --------------------------------------------------------------------- inventory
Qty = Field(gt=0, max_digits=18, decimal_places=4)


class ReceiveIn(BaseModel):
    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: Decimal = Qty
    unit_id: uuid.UUID | None = None
    unit_cost: Decimal | None = Field(None, ge=0)
    batch_number: str | None = Field(None, max_length=80)
    expires_on: date | None = None
    manufactured_on: date | None = None
    serials: list[str] | None = None
    supplier_id: uuid.UUID | None = None
    reference_no: str | None = Field(None, max_length=40)
    note: str | None = Field(None, max_length=1000)
    type: Literal["purchase_receipt", "return", "adjustment_in", "production_output"] = "purchase_receipt"


class IssueIn(BaseModel):
    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: Decimal = Qty
    unit_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    serials: list[str] | None = None
    customer_id: uuid.UUID | None = None
    issued_to: str | None = Field(None, max_length=200)
    reference_no: str | None = Field(None, max_length=40)
    note: str | None = Field(None, max_length=1000)
    type: Literal["issue", "sale", "damaged", "lost", "expired", "production_consumption"] = "issue"


class AdjustLine(BaseModel):
    product_id: uuid.UUID
    location_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    counted_qty: Decimal | None = Field(None, ge=0)
    change: Decimal | None = None
    serials: list[str] | None = None


class AdjustIn(BaseModel):
    reason: Literal["count_correction", "found", "damaged", "lost", "expired", "other"]
    notes: str | None = Field(None, max_length=1000)
    lines: list[AdjustLine] = Field(min_length=1, max_length=500)


class OpeningLine(BaseModel):
    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: Decimal = Qty
    unit_cost: Decimal | None = Field(None, ge=0)
    batch_number: str | None = None
    expires_on: date | None = None
    serials: list[str] | None = None


class OpeningStockIn(BaseModel):
    lines: list[OpeningLine] = Field(min_length=1, max_length=1000)


class TransferLine(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Qty
    unit_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    serials: list[str] | None = None


class TransferIn(BaseModel):
    from_location_id: uuid.UUID
    to_location_id: uuid.UUID
    items: list[TransferLine] = Field(min_length=1, max_length=500)
    notes: str | None = Field(None, max_length=1000)
    submit: bool = False


class QuickTransferIn(BaseModel):
    from_location_id: uuid.UUID
    to_location_id: uuid.UUID
    product_id: uuid.UUID
    quantity: Decimal = Qty
    unit_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    serials: list[str] | None = None
    notes: str | None = Field(None, max_length=1000)


class ReceiveTransferIn(BaseModel):
    quantities: dict[uuid.UUID, Decimal] | None = None  # item id -> qty; omit = receive everything


class BatchOut(ORM):
    id: uuid.UUID
    product_id: uuid.UUID
    batch_number: str
    manufactured_on: date | None
    expires_on: date | None
    purchase_reference: str | None


class MovementOut(BaseModel):
    id: int
    created_at: datetime
    movement_type: MovementType
    product_id: uuid.UUID
    product_name: str
    sku: str
    unit: str
    location_id: uuid.UUID
    location_name: str
    warehouse_name: str
    batch_number: str | None
    quantity: Decimal
    balance_after: Decimal
    unit_cost: Decimal | None
    entered_quantity: Decimal | None
    entered_unit: str | None
    reference_type: str | None
    reference_id: uuid.UUID | None
    reference_no: str | None
    counterparty: str | None
    note: str | None
    user_name: str | None
    serials: list[str] = []


class OperationResult(BaseModel):
    message: str
    movements: list[MovementOut]
    on_hand: Decimal


class StockRow(BaseModel):
    product_id: uuid.UUID
    sku: str
    barcode: str | None
    product_name: str
    unit: str
    warehouse_id: uuid.UUID
    warehouse_name: str
    location_id: uuid.UUID
    location_name: str
    batch_id: uuid.UUID | None
    batch_number: str | None
    expires_on: date | None
    quantity: Decimal
    value: Decimal


class TransferItemOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    sku: str
    unit: str
    batch_id: uuid.UUID | None
    requested_qty: Decimal
    dispatched_qty: Decimal
    received_qty: Decimal
    remaining_qty: Decimal
    available_qty: Decimal | None = None


class TransferOut(BaseModel):
    id: uuid.UUID
    number: str
    status: TransferStatus
    from_location_id: uuid.UUID
    from_name: str
    to_location_id: uuid.UUID
    to_name: str
    notes: str | None
    created_at: datetime
    dispatched_at: datetime | None
    received_at: datetime | None
    items: list[TransferItemOut]


class AdjustmentOut(BaseModel):
    id: uuid.UUID
    number: str
    reason: str
    notes: str | None
    created_at: datetime
    lines: int


# --------------------------------------------------------------------- admin
class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Str200
    password: str = Field(min_length=10, max_length=128)
    role_id: uuid.UUID
    warehouse_ids: list[uuid.UUID] = []


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    role_id: uuid.UUID | None = None
    is_active: bool | None = None
    warehouse_ids: list[uuid.UUID] | None = None
    password: str | None = Field(None, min_length=10, max_length=128)
    reset_two_factor: bool = False


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role_id: uuid.UUID
    role: str
    is_active: bool
    email_verified: bool = False
    totp_enabled: bool = False
    warehouse_ids: list[uuid.UUID]
    last_login_at: datetime | None
    created_at: datetime


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = None
    permissions: list[str]


class RoleOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    is_system: bool
    permissions: list[str]
    user_count: int = 0


class SettingsIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    currency: str | None = Field(None, min_length=3, max_length=3)
    allow_negative_stock: bool | None = None
    modules: dict[str, bool] | None = None
    timezone: str | None = Field(None, max_length=64)
    pos: dict | None = None  # counter-sales settings, see services/pos.py DEFAULTS


class NotificationOut(ORM):
    id: uuid.UUID
    type: str
    severity: str
    title: str
    body: str | None
    entity_type: str | None
    entity_id: str | None
    is_read: bool
    is_resolved: bool
    created_at: datetime


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    user_name: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    summary: str | None
    before: dict | None
    after: dict | None
    ip_address: str | None
