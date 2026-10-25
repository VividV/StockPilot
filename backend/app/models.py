"""StockPilot database schema.

Conventions
-----------
* Every tenant-owned table carries ``org_id`` and every query is scoped by it.
* Business entities use UUID primary keys (not guessable across tenants).
  High-volume append-only tables (movements, audit log) use BIGINT identity.
* Quantities are NUMERIC(18,4) in the product's *base unit*; money NUMERIC(14,4).
* Stock is never overwritten: ``stock_balances`` is a cache that is only ever
  changed in the same transaction that inserts the matching ``stock_movements``
  row. ``stock_movements`` is immutable (enforced by a DB trigger, see migration).
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey, Identity, Index,
    Integer, Numeric, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

QTY = Numeric(18, 4)
MONEY = Numeric(14, 4)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


def pg_enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


# --------------------------------------------------------------------------- enums
class MovementType(str, enum.Enum):
    OPENING_BALANCE = "opening_balance"
    PURCHASE_RECEIPT = "purchase_receipt"
    SALE = "sale"
    ISSUE = "issue"
    RETURN = "return"
    ADJUSTMENT_IN = "adjustment_in"
    ADJUSTMENT_OUT = "adjustment_out"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    DAMAGED = "damaged"
    LOST = "lost"
    EXPIRED = "expired"
    PRODUCTION_CONSUMPTION = "production_consumption"
    PRODUCTION_OUTPUT = "production_output"


INBOUND_TYPES = {
    MovementType.OPENING_BALANCE, MovementType.PURCHASE_RECEIPT, MovementType.RETURN,
    MovementType.ADJUSTMENT_IN, MovementType.TRANSFER_IN, MovementType.PRODUCTION_OUTPUT,
}
OUTBOUND_TYPES = set(MovementType) - INBOUND_TYPES


class WarehouseType(str, enum.Enum):
    WAREHOUSE = "warehouse"
    STORE = "store"
    BRANCH = "branch"
    SITE = "site"
    VEHICLE = "vehicle"
    TECHNICIAN = "technician"
    PRODUCTION = "production"


class LocationKind(str, enum.Enum):
    DEFAULT = "default"  # the single "Main" area every warehouse gets
    ZONE = "zone"
    AISLE = "aisle"
    RACK = "rack"
    SHELF = "shelf"
    BIN = "bin"


class TransferStatus(str, enum.Enum):
    DRAFT = "draft"
    REQUESTED = "requested"
    APPROVED = "approved"
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class SerialStatus(str, enum.Enum):
    IN_STOCK = "in_stock"
    IN_TRANSIT = "in_transit"
    SOLD = "sold"
    ISSUED = "issued"
    WRITTEN_OFF = "written_off"


class OrderStatus(str, enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    PARTIAL = "partial"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PaymentStatus(str, enum.Enum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"


# --------------------------------------------------------------------------- tenancy & access
class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str] = mapped_column(String(50), default="other")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    # Feature modules + behaviour flags. See services/industry.py for defaults.
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()


class Permission(Base):
    """Global catalogue of permission codes (e.g. ``inventory.issue``)."""
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(String(255))


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(String(64), ForeignKey("permissions.code", ondelete="CASCADE"), primary_key=True)


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("org_id", "name"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(255))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()
    permissions: Mapped[list[RolePermission]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    @property
    def permission_codes(self) -> set[str]:
        return {p.permission_code for p in self.permissions}


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    email: Mapped[str] = mapped_column(String(255), unique=True)  # stored lower-case
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64))      # set while 2FA is being enabled or once enabled
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    recovery_codes: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))  # sha256 hashes, single use
    phone: Mapped[str | None] = mapped_column(String(32))            # E.164, for SMS / WhatsApp alerts
    # {"email": bool, "sms": bool, "whatsapp": bool, "types": [..alert types..]}
    notification_prefs: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    role: Mapped[Role] = relationship(lazy="joined")
    warehouse_links: Mapped[list[UserWarehouse]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    @property
    def warehouse_ids(self) -> set[uuid.UUID]:
        """Empty set = may work in every warehouse."""
        return {w.warehouse_id for w in self.warehouse_links}


class UserWarehouse(Base):
    """Restricts a user to specific warehouses/branches/sites."""
    __tablename__ = "user_warehouses"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="CASCADE"), primary_key=True)


class UserSession(Base):
    """Refresh-token sessions (hashed). Lets us revoke sessions / log out."""
    __tablename__ = "user_sessions"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = created_at()


class OneTimeToken(Base):
    """Password-reset and email-verification tokens (hashed, single use)."""
    __tablename__ = "one_time_tokens"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))  # password_reset | email_verify
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class DocumentSequence(Base):
    """Per-organisation running numbers: TRF-00001, PO-00001, ..."""
    __tablename__ = "document_sequences"
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    prefix: Mapped[str] = mapped_column(String(10), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, default=1)


# --------------------------------------------------------------------------- catalogue
class UnitOfMeasure(Base):
    __tablename__ = "units_of_measure"
    __table_args__ = (UniqueConstraint("org_id", "abbreviation"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(String(50))
    abbreviation: Mapped[str] = mapped_column(String(15))
    allow_decimals: Mapped[bool] = mapped_column(Boolean, default=False)


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("org_id", "name"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (Index("ix_suppliers_org_name", "org_id", "name"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(String(200))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (Index("ix_customers_org_name", "org_id", "name"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(String(200))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("org_id", "sku"),
        Index("uq_products_org_barcode", "org_id", "barcode", unique=True, postgresql_where=text("barcode IS NOT NULL")),
        Index("ix_products_org_name", "org_id", "name"),
        Index("ix_products_org_category", "org_id", "category_id"),
        # trigram indexes make "contains" search fast on 100k+ products (needs pg_trgm, created in 0001)
        Index("ix_products_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        Index("ix_products_sku_trgm", "sku", postgresql_using="gin", postgresql_ops={"sku": "gin_trgm_ops"}),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    sku: Mapped[str] = mapped_column(String(64))
    barcode: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"))
    brand: Mapped[str | None] = mapped_column(String(120))
    base_unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units_of_measure.id", ondelete="RESTRICT"))
    cost_price: Mapped[Decimal] = mapped_column(MONEY, default=0)
    selling_price: Mapped[Decimal] = mapped_column(MONEY, default=0)
    average_cost: Mapped[Decimal] = mapped_column(MONEY, default=0)  # moving weighted average, drives valuation
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=0)  # percent
    min_stock: Mapped[Decimal] = mapped_column(QTY, default=0)
    reorder_level: Mapped[Decimal] = mapped_column(QTY, default=0)
    reorder_qty: Mapped[Decimal] = mapped_column(QTY, default=0)
    preferred_supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"))
    image_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    track_batches: Mapped[bool] = mapped_column(Boolean, default=False)
    track_expiry: Mapped[bool] = mapped_column(Boolean, default=False)
    track_serials: Mapped[bool] = mapped_column(Boolean, default=False)
    # Variants: a parent ("T-shirt", has_variants=True) holds no stock. Each
    # size/colour is a normal product row with parent_id set, its own SKU,
    # barcode and stock, so the inventory engine treats it like any product.
    has_variants: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), index=True)
    variant_options: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))     # parent: {"Size": ["S","M"]}
    variant_attributes: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))  # child: {"Size": "S"}
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    base_unit: Mapped[UnitOfMeasure] = relationship(lazy="joined")
    category: Mapped[Category | None] = relationship(lazy="joined")
    preferred_supplier: Mapped[Supplier | None] = relationship(lazy="joined")


class ProductUnitConversion(Base):
    """1 <unit> = <factor> x product base unit. E.g. 1 carton = 24 pieces."""
    __tablename__ = "product_unit_conversions"
    __table_args__ = (UniqueConstraint("product_id", "unit_id"), CheckConstraint("factor > 0", name="ck_conversion_positive"))
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units_of_measure.id", ondelete="CASCADE"))
    factor: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[UnitOfMeasure] = relationship(lazy="joined")


# --------------------------------------------------------------------------- places
class Warehouse(Base):
    """A stock-holding site: warehouse, shop, branch, construction site, van..."""
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("org_id", "code"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(String(150))
    code: Mapped[str] = mapped_column(String(20))
    type: Mapped[WarehouseType] = mapped_column(pg_enum(WarehouseType, "warehouse_type"), default=WarehouseType.WAREHOUSE)
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()


class Location(Base):
    """Optional hierarchy inside a warehouse (aisle > rack > shelf > bin).
    Every warehouse always has exactly one ``default`` location called "Main",
    so small businesses never have to think about bins."""
    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "code"),
        Index("uq_locations_one_default", "warehouse_id", unique=True, postgresql_where=text("kind = 'default'")),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(40))
    kind: Mapped[LocationKind] = mapped_column(pg_enum(LocationKind, "location_kind"), default=LocationKind.BIN)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    warehouse: Mapped[Warehouse] = relationship(lazy="joined")


# --------------------------------------------------------------------------- stock
class StockBatch(Base):
    __tablename__ = "stock_batches"
    __table_args__ = (
        UniqueConstraint("org_id", "product_id", "batch_number"),
        Index("ix_batches_org_expiry", "org_id", "expires_on"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), index=True)
    batch_number: Mapped[str] = mapped_column(String(80))
    manufactured_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"))
    purchase_reference: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = created_at()


class SerialNumber(Base):
    __tablename__ = "serial_numbers"
    __table_args__ = (UniqueConstraint("org_id", "product_id", "serial"), Index("ix_serials_org_serial", "org_id", "serial"))
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), index=True)
    serial: Mapped[str] = mapped_column(String(120))
    status: Mapped[SerialStatus] = mapped_column(pg_enum(SerialStatus, "serial_status"), default=SerialStatus.IN_STOCK)
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="SET NULL"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_batches.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class StockBalance(Base):
    """Quantity on hand per product per location (+batch).
    Only ever modified by services/inventory.py under a row lock."""
    __tablename__ = "stock_balances"
    __table_args__ = (
        UniqueConstraint("org_id", "product_id", "location_id", "batch_id",
                         name="uq_stock_balance_key", postgresql_nulls_not_distinct=True),
        Index("ix_balances_org_location", "org_id", "location_id"),
        Index("ix_balances_org_product", "org_id", "product_id"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"))
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_batches.id", ondelete="RESTRICT"))
    quantity: Mapped[Decimal] = mapped_column(QTY, default=0)
    updated_at: Mapped[datetime] = updated_at()

    product: Mapped[Product] = relationship(lazy="joined")
    location: Mapped[Location] = relationship(lazy="joined")
    batch: Mapped[StockBatch | None] = relationship(lazy="joined")


class StockMovement(Base):
    """Immutable ledger. quantity is signed (+in / -out), in the base unit."""
    __tablename__ = "stock_movements"
    __table_args__ = (
        Index("ix_movements_org_created", "org_id", "created_at"),
        Index("ix_movements_org_product_created", "org_id", "product_id", "created_at"),
        Index("ix_movements_org_location_created", "org_id", "location_id", "created_at"),
        Index("ix_movements_org_reference", "org_id", "reference_type", "reference_id"),
        CheckConstraint("quantity <> 0", name="ck_movement_nonzero"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    org_id: Mapped[uuid.UUID] = org_fk()
    movement_type: Mapped[MovementType] = mapped_column(pg_enum(MovementType, "movement_type"))
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"))
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_batches.id", ondelete="RESTRICT"))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    balance_after: Mapped[Decimal] = mapped_column(QTY)
    unit_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    entered_quantity: Mapped[Decimal | None] = mapped_column(QTY)   # as typed by the user
    entered_unit: Mapped[str | None] = mapped_column(String(15))     # e.g. "ctn"
    reference_type: Mapped[str | None] = mapped_column(String(30))   # transfer | adjustment | purchase_order | sales_order | ...
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reference_no: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    counterparty: Mapped[str | None] = mapped_column(String(200))   # customer / supplier / person issued to
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()

    product: Mapped[Product] = relationship(lazy="joined")
    location: Mapped[Location] = relationship(lazy="joined")
    batch: Mapped[StockBatch | None] = relationship(lazy="joined")
    user: Mapped[User | None] = relationship(lazy="joined", foreign_keys=[created_by])


class MovementSerial(Base):
    """Which serial numbers moved in a movement -> per-serial history."""
    __tablename__ = "movement_serials"
    movement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stock_movements.id", ondelete="RESTRICT"), primary_key=True)
    serial_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("serial_numbers.id", ondelete="RESTRICT"), primary_key=True, index=True)


# --------------------------------------------------------------------------- documents
class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (UniqueConstraint("org_id", "number"), Index("ix_transfers_org_status", "org_id", "status"))
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    number: Mapped[str] = mapped_column(String(30))
    from_location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    to_location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    status: Mapped[TransferStatus] = mapped_column(pg_enum(TransferStatus, "transfer_status"), default=TransferStatus.DRAFT)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    items: Mapped[list[TransferItem]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="TransferItem.line_no")
    from_location: Mapped[Location] = relationship(foreign_keys=[from_location_id], lazy="joined")
    to_location: Mapped[Location] = relationship(foreign_keys=[to_location_id], lazy="joined")


class TransferItem(Base):
    __tablename__ = "transfer_items"
    __table_args__ = (CheckConstraint("requested_qty > 0", name="ck_transfer_item_qty"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    transfer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("transfers.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_batches.id", ondelete="RESTRICT"))
    requested_qty: Mapped[Decimal] = mapped_column(QTY)
    dispatched_qty: Mapped[Decimal] = mapped_column(QTY, default=0)
    received_qty: Mapped[Decimal] = mapped_column(QTY, default=0)
    # what was actually sent (batch split under FEFO + serials) so receiving mirrors it exactly
    dispatch_detail: Mapped[list] = mapped_column(JSONB, default=list)
    product: Mapped[Product] = relationship(lazy="joined")


class StockAdjustment(Base):
    __tablename__ = "stock_adjustments"
    __table_args__ = (UniqueConstraint("org_id", "number"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    number: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(String(40))  # count_correction | damaged | lost | expired | found | other
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    items: Mapped[list[StockAdjustmentItem]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class StockAdjustmentItem(Base):
    __tablename__ = "stock_adjustment_items"
    id: Mapped[uuid.UUID] = uuid_pk()
    adjustment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_adjustments.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"))
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_batches.id", ondelete="RESTRICT"))
    quantity_before: Mapped[Decimal] = mapped_column(QTY)
    quantity_change: Mapped[Decimal] = mapped_column(QTY)
    quantity_after: Mapped[Decimal] = mapped_column(QTY)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("org_id", "number"), Index("ix_po_org_status", "org_id", "status"))
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    number: Mapped[str] = mapped_column(String(30))
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="RESTRICT"))
    destination_location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    status: Mapped[OrderStatus] = mapped_column(pg_enum(OrderStatus, "order_status"), default=OrderStatus.DRAFT)
    order_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    expected_date: Mapped[date | None] = mapped_column(Date)
    supplier_reference: Mapped[str | None] = mapped_column(String(80))  # supplier's quote / invoice no.
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # completed or cancelled
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    items: Mapped[list[PurchaseOrderItem]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="PurchaseOrderItem.line_no")
    supplier: Mapped[Supplier] = relationship(lazy="joined")
    destination: Mapped[Location] = relationship(lazy="joined", foreign_keys=[destination_location_id])


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id: Mapped[uuid.UUID] = uuid_pk()
    __table_args__ = (CheckConstraint("ordered_qty > 0", name="ck_po_item_qty"),)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    ordered_qty: Mapped[Decimal] = mapped_column(QTY)                # base unit
    received_qty: Mapped[Decimal] = mapped_column(QTY, default=0)    # backorder = ordered - received (while open)
    unit_cost: Mapped[Decimal] = mapped_column(MONEY, default=0)     # per base unit
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=0)
    product: Mapped[Product] = relationship(lazy="joined")


class SalesOrder(Base):
    __tablename__ = "sales_orders"
    __table_args__ = (UniqueConstraint("org_id", "number"), Index("ix_so_org_status", "org_id", "status"))
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    number: Mapped[str] = mapped_column(String(30))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"))
    source_location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    status: Mapped[OrderStatus] = mapped_column(pg_enum(OrderStatus, "order_status"), default=OrderStatus.DRAFT)
    payment_status: Mapped[PaymentStatus] = mapped_column(pg_enum(PaymentStatus, "payment_status"), default=PaymentStatus.UNPAID)
    amount_paid: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default=text("0"))
    order_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    customer_reference: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    items: Mapped[list[SalesOrderItem]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="SalesOrderItem.line_no")
    customer: Mapped[Customer | None] = relationship(lazy="joined")
    source: Mapped[Location] = relationship(lazy="joined", foreign_keys=[source_location_id])


class SalesOrderItem(Base):
    __tablename__ = "sales_order_items"
    id: Mapped[uuid.UUID] = uuid_pk()
    __table_args__ = (CheckConstraint("ordered_qty > 0", name="ck_so_item_qty"),)
    sales_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    ordered_qty: Mapped[Decimal] = mapped_column(QTY)
    fulfilled_qty: Mapped[Decimal] = mapped_column(QTY, default=0)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, default=0)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=0)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=0)
    cost_of_goods: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default=text("0"))  # sum of unit_cost x qty actually shipped
    product: Mapped[Product] = relationship(lazy="joined")


# --------------------------------------------------------------------------- cross-cutting
class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_org_created", "org_id", "created_at"),
        Index("ix_audit_org_entity", "org_id", "entity_type", "entity_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60))         # e.g. product.create, auth.login
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(String(500))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = created_at()
    user: Mapped[User | None] = relationship(lazy="joined")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_org_unread", "org_id", "is_read", "created_at"),
        # one open alert per (type, entity) so we don't spam on every movement
        Index("uq_notifications_open", "org_id", "type", "entity_id", unique=True, postgresql_where=text("is_resolved = false")),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    type: Mapped[str] = mapped_column(String(40))  # low_stock | out_of_stock | expiring | expired | pending_transfer | failed_operation ...
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info | warning | critical
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when email/SMS copies were queued
    created_at: Mapped[datetime] = created_at()


class NotificationDelivery(Base):
    """Outbox for messages leaving the app: alert copies and account emails
    (password reset, verification). Sent by the worker with retries."""
    __tablename__ = "notification_deliveries"
    __table_args__ = (Index("ix_deliveries_pending", "status", "next_attempt_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    notification_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(20))   # email | sms | whatsapp
    recipient: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | sent | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (Index("ix_attachments_org_entity", "org_id", "entity_type", "entity_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(500))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()


class ProductComponent(Base):
    """Bill of materials: making 1 unit of product_id uses quantity of component_id."""
    __tablename__ = "product_components"
    __table_args__ = (UniqueConstraint("product_id", "component_id"), CheckConstraint("quantity > 0", name="ck_component_qty"),
                      CheckConstraint("product_id <> component_id", name="ck_component_not_self"))
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), index=True)
    component_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    component: Mapped[Product] = relationship(lazy="joined", foreign_keys=[component_id])


class ProductionRun(Base):
    __tablename__ = "production_runs"
    __table_args__ = (UniqueConstraint("org_id", "number"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    number: Mapped[str] = mapped_column(String(30))
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"))
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    unit_cost: Mapped[Decimal] = mapped_column(MONEY, default=0)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_batches.id", ondelete="RESTRICT"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    product: Mapped[Product] = relationship(lazy="joined")


# --------------------------------------------------------------------------- point of sale (Phase 3)
class PosShift(Base):
    """One cashier's session at a till: from opening the drawer to cashing up."""
    __tablename__ = "pos_shifts"
    __table_args__ = (
        UniqueConstraint("org_id", "number"),
        Index("uq_pos_shift_open_per_user", "org_id", "user_id", unique=True, postgresql_where=text("status = 'open'")),
        Index("ix_pos_shifts_org_opened", "org_id", "opened_at"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    number: Mapped[str] = mapped_column(String(30))
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | closed
    opening_float: Mapped[Decimal] = mapped_column(MONEY, default=0)
    counted_cash: Mapped[Decimal | None] = mapped_column(MONEY)
    expected_cash: Mapped[Decimal | None] = mapped_column(MONEY)      # snapshot at closing
    closing_summary: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    closing_note: Mapped[str | None] = mapped_column(Text)
    opened_at: Mapped[datetime] = created_at()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    location: Mapped[Location] = relationship(lazy="joined")
    user: Mapped[User | None] = relationship(lazy="joined", foreign_keys=[user_id])


class PosSale(Base):
    """A counter sale or a counter return (kind='return'). Amounts are positive
    for both; payments carry the sign (money in > 0, refunds < 0)."""
    __tablename__ = "pos_sales"
    __table_args__ = (
        UniqueConstraint("org_id", "number"),
        UniqueConstraint("org_id", "client_id", name="uq_pos_sales_client_id"),
        Index("ix_pos_sales_org_created", "org_id", "created_at"),
        Index("ix_pos_sales_org_location_created", "org_id", "location_id", "created_at"),
        Index("ix_pos_sales_shift", "shift_id"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    number: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(10), default="sale")  # sale | return
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # set by the till; makes offline sync safe to repeat
    shift_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("pos_shifts.id", ondelete="SET NULL"))
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"))
    customer_name: Mapped[str | None] = mapped_column(String(200))
    customer_phone: Mapped[str | None] = mapped_column(String(32))
    original_sale_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("pos_sales.id", ondelete="SET NULL"))
    subtotal: Mapped[Decimal] = mapped_column(MONEY, default=0)     # quantity x price before any discount
    discount: Mapped[Decimal] = mapped_column(MONEY, default=0)     # promotions + manual discounts
    tax: Mapped[Decimal] = mapped_column(MONEY, default=0)
    total: Mapped[Decimal] = mapped_column(MONEY, default=0)        # what the customer pays (or gets back)
    cost_of_goods: Mapped[Decimal] = mapped_column(MONEY, default=0)
    amount_tendered: Mapped[Decimal] = mapped_column(MONEY, default=0)
    change_given: Mapped[Decimal] = mapped_column(MONEY, default=0)
    credit_remaining: Mapped[Decimal] = mapped_column(MONEY, default=0)  # returns refunded as store credit
    offline: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_note: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    public_token: Mapped[str] = mapped_column(String(40), unique=True)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())  # till clock (offline sales)
    created_at: Mapped[datetime] = created_at()
    items: Mapped[list[PosSaleItem]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="PosSaleItem.line_no",
                                                    foreign_keys="PosSaleItem.sale_id")
    payments: Mapped[list[PosPayment]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="PosPayment.created_at",
                                                      foreign_keys="PosPayment.sale_id")
    location: Mapped[Location] = relationship(lazy="joined")
    user: Mapped[User | None] = relationship(lazy="joined", foreign_keys=[user_id])
    customer: Mapped[Customer | None] = relationship(lazy="joined")


class PosSaleItem(Base):
    __tablename__ = "pos_sale_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_pos_item_qty"), Index("ix_pos_items_product", "product_id"))
    id: Mapped[uuid.UUID] = uuid_pk()
    sale_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("pos_sales.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    list_price: Mapped[Decimal] = mapped_column(MONEY, default=0)   # catalogue price at the time
    unit_price: Mapped[Decimal] = mapped_column(MONEY, default=0)   # price charged per unit, before discounts
    discount: Mapped[Decimal] = mapped_column(MONEY, default=0)     # whole line
    promotion_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("promotions.id", ondelete="SET NULL"))
    promotion_name: Mapped[str | None] = mapped_column(String(120))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=0)
    tax: Mapped[Decimal] = mapped_column(MONEY, default=0)
    total: Mapped[Decimal] = mapped_column(MONEY, default=0)        # what the customer pays for the line, tax included
    cost_of_goods: Mapped[Decimal] = mapped_column(MONEY, default=0)
    returned_qty: Mapped[Decimal] = mapped_column(QTY, default=0)
    original_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("pos_sale_items.id", ondelete="SET NULL"))
    restocked: Mapped[bool] = mapped_column(Boolean, default=True)  # returns: went back on the shelf
    stock_deducted: Mapped[bool] = mapped_column(Boolean, default=True)
    serials: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    product: Mapped[Product] = relationship(lazy="joined")


class PosPayment(Base):
    __tablename__ = "pos_payments"
    __table_args__ = (Index("ix_pos_payments_shift", "shift_id"), Index("ix_pos_payments_org_created", "org_id", "created_at"))
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    sale_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("pos_sales.id", ondelete="CASCADE"), index=True)
    shift_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("pos_shifts.id", ondelete="SET NULL"))
    method: Mapped[str] = mapped_column(String(20))   # cash | transfer | card | other | credit
    amount: Mapped[Decimal] = mapped_column(MONEY)    # > 0 money in, < 0 refund
    reference: Mapped[str | None] = mapped_column(String(80))
    credit_sale_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("pos_sales.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()


class PosCashMovement(Base):
    """Cash put into or taken out of the drawer that is not a sale (change float top-up, paying for diesel...)."""
    __tablename__ = "pos_cash_movements"
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("pos_shifts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(5))      # in | out
    amount: Mapped[Decimal] = mapped_column(MONEY)
    reason: Mapped[str] = mapped_column(String(200))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    user: Mapped[User | None] = relationship(lazy="joined")


class Promotion(Base):
    """Automatic price cuts applied at the till: 10% off a category, ₦500 off an item, 3+ for 15% off..."""
    __tablename__ = "promotions"
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10))     # percent | amount (off each item)
    value: Mapped[Decimal] = mapped_column(MONEY)
    scope: Mapped[str] = mapped_column(String(10), default="all")  # all | category | products
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"))
    product_ids: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    min_qty: Mapped[Decimal] = mapped_column(QTY, default=1)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()


# --------------------------------------------------------------------------- subscriptions (Phase 3)
class Subscription(Base):
    """One per organisation. Only enforced when BILLING_ENABLED is on."""
    __tablename__ = "subscriptions"
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    plan: Mapped[str] = mapped_column(String(20), default="trial")
    status: Mapped[str] = mapped_column(String(20), default="trialing")  # trialing | active | past_due | expired | cancelled
    interval: Mapped[str] = mapped_column(String(10), default="monthly")  # monthly | yearly
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    billing_email: Mapped[str | None] = mapped_column(String(255))
    # reusable card authorisation from Paystack, for automatic renewals
    authorization: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class BillingPayment(Base):
    __tablename__ = "billing_payments"
    __table_args__ = (Index("ix_billing_payments_org", "org_id", "created_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    reference: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(10), default="checkout")  # checkout | renewal
    plan: Mapped[str] = mapped_column(String(20))
    interval: Mapped[str] = mapped_column(String(10))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | success | failed
    channel: Mapped[str | None] = mapped_column(String(20))
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
