"""生产：工单 -> 领料 -> 完工。

光伏制造的两个关键点：
1. 同一工单产出的组件按实测功率分档（545/550/555W），分别入不同 SKU。
2. 工单总成本按「数量 x 功率」的比例分摊到各功率档，尾差挂最后一行。
"""

from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal

from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import (
    BizType,
    Bom,
    Product,
    ProductCategory,
    SerialNumber,
    SerialStatus,
    WoMaterial,
    WoOutput,
    WorkOrder,
    WorkOrderStatus,
)
from .inventory import D, q2, q4, stock_in, stock_out
from .numbering import next_no


def create_work_order(
    db: Session,
    *,
    product_id: int,
    bom_id: int,
    plan_qty,
    material_warehouse_id: int,
    finished_warehouse_id: int,
    project_id: int | None = None,
    operator_id: int | None = None,
) -> WorkOrder:
    product = db.get(Product, product_id)
    if product is None or product.category != ProductCategory.FINISHED:
        raise BusinessError("工单成品必须是成品物料")
    bom = db.get(Bom, bom_id)
    if bom is None:
        raise BusinessError("BOM 不存在")
    if bom.product_id != product_id:
        raise BusinessError("BOM 与成品不匹配")

    plan_qty = D(plan_qty)
    if plan_qty <= 0:
        raise BusinessError("计划数量必须大于 0")

    wo = WorkOrder(
        wo_no=next_no(db, WorkOrder, "wo_no", "WO"),
        product_id=product_id,
        bom_id=bom_id,
        plan_qty=plan_qty,
        material_warehouse_id=material_warehouse_id,
        finished_warehouse_id=finished_warehouse_id,
        project_id=project_id,
        status=WorkOrderStatus.DRAFT,
        created_by=operator_id,
    )
    db.add(wo)
    db.flush()
    return wo


def release_work_order(db: Session, wo: WorkOrder) -> WorkOrder:
    if wo.status != WorkOrderStatus.DRAFT:
        raise BusinessError(f"只有草稿工单可以下达，当前状态：{wo.status}")
    wo.status = WorkOrderStatus.RELEASED
    db.flush()
    return wo


def issue_materials(db: Session, wo: WorkOrder, operator_id: int | None = None) -> WorkOrder:
    """按 BOM 展开领料，扣减原料库存并归集材料成本。"""
    if wo.status != WorkOrderStatus.RELEASED:
        raise BusinessError(f"只有已下达的工单可以领料，当前状态：{wo.status}")

    bom = db.get(Bom, wo.bom_id)
    if bom is None or not bom.items:
        raise BusinessError("BOM 没有明细，无法领料")

    total_material = D(0)
    for bom_item in bom.items:
        required = (
            D(wo.plan_qty)
            * D(bom_item.qty_per)
            * (D(1) + D(bom_item.loss_rate) / D(100))
        ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        if required <= 0:
            continue
        unit_cost = stock_out(
            db,
            warehouse_id=wo.material_warehouse_id,
            product_id=bom_item.material_id,
            qty=required,
            biz_type=BizType.PRODUCTION_ISSUE,
            source_type="WORK_ORDER",
            source_id=wo.id,
            source_no=wo.wo_no,
            operator_id=operator_id,
        )
        amount = q2(required * unit_cost)
        total_material += amount
        db.add(
            WoMaterial(
                wo_id=wo.id,
                material_id=bom_item.material_id,
                required_qty=required,
                issued_qty=required,
                unit_cost=unit_cost,
                amount=amount,
            )
        )

    wo.material_cost = q2(total_material)
    wo.status = WorkOrderStatus.ISSUED
    wo.issued_at = datetime.utcnow()
    db.flush()
    return wo


def complete_work_order(
    db: Session,
    wo: WorkOrder,
    outputs: list[dict],
    labor_cost=0,
    overhead_cost=0,
    operator_id: int | None = None,
) -> WorkOrder:
    """完工：按功率档入库、生成 SN、分摊成本。"""
    if wo.status != WorkOrderStatus.ISSUED:
        raise BusinessError(f"只有已领料的工单可以完工，当前状态：{wo.status}")
    if not outputs:
        raise BusinessError("至少需要一个完工产出")

    parsed: list[tuple[Product, Decimal]] = []
    for row in outputs:
        product = db.get(Product, row["product_id"])
        if product is None or product.category != ProductCategory.FINISHED:
            raise BusinessError("完工产出必须是成品")
        qty = D(row["qty"])
        if qty <= 0:
            raise BusinessError("完工数量必须大于 0")
        if product.serial_managed and qty != qty.to_integral_value():
            raise BusinessError("序列号管理的商品数量必须为整数")
        parsed.append((product, qty))

    total_cost = D(wo.material_cost) + D(labor_cost) + D(overhead_cost)
    if total_cost < 0:
        raise BusinessError("工单总成本不能为负")

    # 按「数量 x 单件功率」分摊；没有功率时退化为按数量分摊。
    weights = []
    for product, qty in parsed:
        power = D(product.power_w) if product.power_w is not None else D(0)
        weights.append(qty * power)
    if sum(weights) <= 0:
        weights = [qty for _, qty in parsed]
    total_weight = sum(weights)

    remaining = total_cost
    sn_seq = 0
    for index, (product, qty) in enumerate(parsed):
        if index == len(parsed) - 1:
            allocated = remaining
        else:
            allocated = (total_cost * weights[index] / total_weight).quantize(
                Decimal("0.01"), rounding=ROUND_DOWN
            )
            remaining -= allocated
        unit_cost = q4(allocated / qty) if qty > 0 else D(0)

        db.add(
            WoOutput(
                wo_id=wo.id,
                product_id=product.id,
                power_w=product.power_w,
                qty=qty,
                unit_cost=unit_cost,
                amount=q2(allocated),
            )
        )
        stock_in(
            db,
            warehouse_id=wo.finished_warehouse_id,
            product_id=product.id,
            qty=qty,
            unit_cost=unit_cost,
            biz_type=BizType.PRODUCTION_IN,
            source_type="WORK_ORDER",
            source_id=wo.id,
            source_no=wo.wo_no,
            batch_no=wo.wo_no,
            project_id=wo.project_id,
            operator_id=operator_id,
        )

        if product.serial_managed:
            warranty_start = date.today()
            warranty_end = None
            if product.warranty_years:
                warranty_end = warranty_start.replace(
                    year=warranty_start.year + product.warranty_years
                )
            for _ in range(int(qty)):
                sn_seq += 1
                db.add(
                    SerialNumber(
                        sn=f"SN-{wo.wo_no}-{sn_seq:05d}",
                        product_id=product.id,
                        wo_id=wo.id,
                        status=SerialStatus.IN_STOCK,
                        warehouse_id=wo.finished_warehouse_id,
                        warranty_start=warranty_start,
                        warranty_end=warranty_end,
                    )
                )

    wo.labor_cost = q2(labor_cost)
    wo.overhead_cost = q2(overhead_cost)
    wo.total_cost = q2(total_cost)
    wo.status = WorkOrderStatus.COMPLETED
    wo.completed_at = datetime.utcnow()
    db.flush()
    return wo
