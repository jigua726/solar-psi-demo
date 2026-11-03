"""领域模型：光伏组件制造商 · 轻量 MES + 进销存。

设计要点
--------
1. 库存流水（stock_ledger）是唯一真相，余额（stock_balances）是加速表。
2. 单据状态机：DRAFT -> APPROVED -> EXECUTED（采购收货 / 销售发货）。
3. 工单状态机：DRAFT -> RELEASED -> ISSUED -> COMPLETED。
4. 组件按功率档拆 SKU，成本按瓦数比例分摊。
5. 成品通过序列号（serial_numbers）一物一码追溯。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

QTY = Numeric(18, 4)
MONEY = Numeric(18, 2)
COST = Numeric(18, 4)
RATE = Numeric(8, 4)


class Role:
    ADMIN = "admin"
    SALES = "sales"
    PRODUCTION = "production"
    WAREHOUSE = "warehouse"


class WarehouseType:
    RAW = "RAW"
    FINISHED = "FINISHED"
    SITE = "SITE"


class ProductCategory:
    RAW = "RAW"
    SEMI = "SEMI"
    FINISHED = "FINISHED"


class PartnerType:
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"


class OrderType:
    PURCHASE = "PURCHASE"
    SALES = "SALES"


class OrderStatus:
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"


class WorkOrderStatus:
    DRAFT = "DRAFT"
    RELEASED = "RELEASED"
    ISSUED = "ISSUED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class SerialStatus:
    IN_STOCK = "IN_STOCK"
    SHIPPED = "SHIPPED"
    SCRAPPED = "SCRAPPED"


class BizType:
    OPENING = "OPENING"
    PURCHASE_IN = "PURCHASE_IN"
    PRODUCTION_ISSUE = "PRODUCTION_ISSUE"
    PRODUCTION_IN = "PRODUCTION_IN"
    SALES_OUT = "SALES_OUT"


class Direction:
    IN = "IN"
    OUT = "OUT"


class JobStatus:
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ProjectStatus:
    OPEN = "OPEN"
    DELIVERING = "DELIVERING"
    DONE = "DONE"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(100), default="")
    role: Mapped[str] = mapped_column(String(20), default=Role.SALES)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20), default=WarehouseType.RAW)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(20), default=ProductCategory.RAW)
    unit: Mapped[str] = mapped_column(String(20), default="个")
    # 组件单件功率（W），储能单件容量（kWh）
    power_w: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    capacity_kwh: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    measure_unit: Mapped[str | None] = mapped_column(String(10), nullable=True)  # W / MWh
    standard_cost: Mapped[Decimal] = mapped_column(COST, default=Decimal("0"))
    tax_rate: Mapped[Decimal] = mapped_column(RATE, default=Decimal("0.13"))
    warranty_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    serial_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Partner(Base):
    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(20), index=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    contact: Mapped[str] = mapped_column(String(50), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    tax_no: Mapped[str] = mapped_column(String(50), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"), nullable=True)
    capacity_kw: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ProjectStatus.OPEN)
    address: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Bom(Base):
    __tablename__ = "boms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    version: Mapped[str] = mapped_column(String(20), default="V1")
    name: Mapped[str] = mapped_column(String(100), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    items: Mapped[list["BomItem"]] = relationship(
        back_populates="bom", cascade="all, delete-orphan", order_by="BomItem.id"
    )
    product: Mapped["Product"] = relationship()


class BomItem(Base):
    __tablename__ = "bom_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty_per: Mapped[Decimal] = mapped_column(QTY)
    loss_rate: Mapped[Decimal] = mapped_column(RATE, default=Decimal("0"))
    unit: Mapped[str] = mapped_column(String(20), default="")

    bom: Mapped["Bom"] = relationship(back_populates="items")
    material: Mapped["Product"] = relationship()


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    order_type: Mapped[str] = mapped_column(String(20), index=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.DRAFT, index=True)
    order_date: Mapped[date] = mapped_column(Date, default=date.today)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    total_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    remark: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id"
    )
    partner: Mapped["Partner"] = relationship()
    warehouse: Mapped["Warehouse"] = relationship()
    project: Mapped["Project | None"] = relationship()


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    unit_price: Mapped[Decimal] = mapped_column(COST)
    amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    tax_rate: Mapped[Decimal] = mapped_column(RATE, default=Decimal("0.13"))
    batch_no: Mapped[str | None] = mapped_column(String(50), nullable=True)
    unit_cost: Mapped[Decimal | None] = mapped_column(COST, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wo_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    plan_qty: Mapped[Decimal] = mapped_column(QTY)
    material_warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    finished_warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=WorkOrderStatus.DRAFT, index=True)
    material_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    labor_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    overhead_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    total_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    product: Mapped["Product"] = relationship(foreign_keys=[product_id])
    bom: Mapped["Bom"] = relationship()
    materials: Mapped[list["WoMaterial"]] = relationship(
        back_populates="work_order", cascade="all, delete-orphan", order_by="WoMaterial.id"
    )
    outputs: Mapped[list["WoOutput"]] = relationship(
        back_populates="work_order", cascade="all, delete-orphan", order_by="WoOutput.id"
    )


class WoMaterial(Base):
    __tablename__ = "wo_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wo_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    required_qty: Mapped[Decimal] = mapped_column(QTY)
    issued_qty: Mapped[Decimal] = mapped_column(QTY)
    unit_cost: Mapped[Decimal] = mapped_column(COST)
    amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))

    work_order: Mapped["WorkOrder"] = relationship(back_populates="materials")
    material: Mapped["Product"] = relationship()


class WoOutput(Base):
    __tablename__ = "wo_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wo_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    power_w: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    qty: Mapped[Decimal] = mapped_column(QTY)
    unit_cost: Mapped[Decimal] = mapped_column(COST)
    amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))

    work_order: Mapped["WorkOrder"] = relationship(back_populates="outputs")
    product: Mapped["Product"] = relationship()


class StockLedger(Base):
    __tablename__ = "stock_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    batch_no: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String(5))
    qty: Mapped[Decimal] = mapped_column(QTY)
    unit_cost: Mapped[Decimal] = mapped_column(COST)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    biz_type: Mapped[str] = mapped_column(String(30), index=True)
    source_type: Mapped[str] = mapped_column(String(30), default="")
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_no: Mapped[str] = mapped_column(String(50), default="")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (Index("ix_ledger_wh_prod", "warehouse_id", "product_id"),)


class StockBalance(Base):
    __tablename__ = "stock_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("warehouse_id", "product_id", name="uq_balance_wh_prod"),
    )

    product: Mapped["Product"] = relationship()
    warehouse: Mapped["Warehouse"] = relationship()


class SerialNumber(Base):
    __tablename__ = "serial_numbers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sn: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    wo_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=SerialStatus.IN_STOCK, index=True)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    warranty_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    warranty_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(50))
    trigger_type: Mapped[str] = mapped_column(String(20), default="MANUAL")
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.RUNNING)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    success_rows: Mapped[int] = mapped_column(Integer, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    operator: Mapped[str] = mapped_column(String(50), default="")
    message: Mapped[str] = mapped_column(String(255), default="")
    errors: Mapped[list["ImportErrorRow"]] = relationship(
        back_populates="job_run", cascade="all, delete-orphan", order_by="ImportErrorRow.row_no"
    )


class ImportErrorRow(Base):
    __tablename__ = "import_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_run_id: Mapped[int] = mapped_column(ForeignKey("job_runs.id"), index=True)
    row_no: Mapped[int] = mapped_column(Integer)
    raw_data: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(String(500), default="")

    job_run: Mapped["JobRun"] = relationship(back_populates="errors")
