from decimal import Decimal

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str
    password: str


class ProductIn(BaseModel):
    sku: str
    name: str
    category: str = "RAW"
    unit: str = "个"
    power_w: Decimal | None = None
    capacity_kwh: Decimal | None = None
    measure_unit: str | None = None
    standard_cost: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0.13")
    warranty_years: int | None = None
    serial_managed: bool = False


class PartnerIn(BaseModel):
    type: str = "CUSTOMER"
    code: str
    name: str
    contact: str = ""
    phone: str = ""
    tax_no: str = ""


class WarehouseIn(BaseModel):
    code: str
    name: str
    type: str = "RAW"


class ProjectIn(BaseModel):
    project_no: str
    name: str
    customer_id: int | None = None
    capacity_kw: Decimal | None = None
    address: str = ""


class BomItemIn(BaseModel):
    material_id: int
    qty_per: Decimal = Field(gt=0)
    loss_rate: Decimal = Decimal("0")


class BomIn(BaseModel):
    product_id: int
    version: str = "V1"
    name: str = ""
    items: list[BomItemIn]


class OrderItemIn(BaseModel):
    product_id: int
    qty: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    batch_no: str | None = None


class OrderIn(BaseModel):
    partner_id: int
    warehouse_id: int
    project_id: int | None = None
    remark: str = ""
    items: list[OrderItemIn]


class WorkOrderIn(BaseModel):
    product_id: int
    bom_id: int
    plan_qty: Decimal = Field(gt=0)
    material_warehouse_id: int
    finished_warehouse_id: int
    project_id: int | None = None


class WorkOrderOutputIn(BaseModel):
    product_id: int
    qty: Decimal = Field(gt=0)


class WorkOrderCompleteIn(BaseModel):
    outputs: list[WorkOrderOutputIn]
    labor_cost: Decimal = Decimal("0")
    overhead_cost: Decimal = Decimal("0")
