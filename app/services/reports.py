from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    Order,
    OrderStatus,
    OrderType,
    Product,
    Project,
    StockBalance,
    StockLedger,
    Warehouse,
    WorkOrder,
    WorkOrderStatus,
)


def _money(value) -> float:
    return float(Decimal(str(value or 0)))


def stock_summary(db: Session) -> list[dict]:
    rows = (
        db.query(StockBalance, Product, Warehouse)
        .join(Product, Product.id == StockBalance.product_id)
        .join(Warehouse, Warehouse.id == StockBalance.warehouse_id)
        .filter(StockBalance.qty != 0)
        .order_by(Warehouse.code, Product.sku)
        .all()
    )
    result = []
    for balance, product, warehouse in rows:
        qty = Decimal(str(balance.qty))
        amount = Decimal(str(balance.amount))
        result.append(
            {
                "warehouse": warehouse.name,
                "warehouse_code": warehouse.code,
                "sku": product.sku,
                "product": product.name,
                "category": product.category,
                "unit": product.unit,
                "qty": _money(qty),
                "amount": round(_money(amount), 2),
                "avg_cost": round(_money(amount / qty), 4) if qty else 0,
            }
        )
    return result


def project_margin(db: Session) -> list[dict]:
    rows = (
        db.query(
            Order.project_id,
            func.sum(Order.total_amount),
            func.sum(Order.total_cost),
        )
        .filter(
            Order.order_type == OrderType.SALES,
            Order.status == OrderStatus.EXECUTED,
            Order.project_id.isnot(None),
        )
        .group_by(Order.project_id)
        .all()
    )
    projects = {p.id: p for p in db.query(Project).all()}
    result = []
    for project_id, revenue, cost in rows:
        project = projects.get(project_id)
        revenue = Decimal(str(revenue or 0))
        cost = Decimal(str(cost or 0))
        margin = revenue - cost
        result.append(
            {
                "project_no": project.project_no if project else f"#{project_id}",
                "project_name": project.name if project else "",
                "capacity_kw": _money(project.capacity_kw) if project else 0,
                "revenue": round(_money(revenue), 2),
                "cost": round(_money(cost), 2),
                "margin": round(_money(margin), 2),
                "margin_rate": round(_money(margin / revenue) * 100, 2) if revenue else 0,
            }
        )
    result.sort(key=lambda row: row["revenue"], reverse=True)
    return result


def work_order_cost(db: Session) -> list[dict]:
    rows = (
        db.query(WorkOrder, Product)
        .join(Product, Product.id == WorkOrder.product_id)
        .order_by(WorkOrder.id.desc())
        .all()
    )
    result = []
    for wo, product in rows:
        result.append(
            {
                "wo_no": wo.wo_no,
                "product": product.name,
                "plan_qty": _money(wo.plan_qty),
                "status": wo.status,
                "material_cost": round(_money(wo.material_cost), 2),
                "labor_cost": round(_money(wo.labor_cost), 2),
                "overhead_cost": round(_money(wo.overhead_cost), 2),
                "total_cost": round(_money(wo.total_cost), 2),
                "unit_cost": round(
                    _money(Decimal(str(wo.total_cost)) / Decimal(str(wo.plan_qty))), 4
                )
                if wo.plan_qty
                else 0,
            }
        )
    return result


def ledger_rows(db: Session, limit: int = 100) -> list[dict]:
    rows = (
        db.query(StockLedger, Product, Warehouse)
        .join(Product, Product.id == StockLedger.product_id)
        .join(Warehouse, Warehouse.id == StockLedger.warehouse_id)
        .order_by(StockLedger.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "time": ledger.created_at.strftime("%Y-%m-%d %H:%M"),
            "warehouse": warehouse.name,
            "sku": product.sku,
            "product": product.name,
            "direction": ledger.direction,
            "qty": _money(ledger.qty),
            "unit_cost": _money(ledger.unit_cost),
            "amount": round(_money(ledger.amount), 2),
            "biz_type": ledger.biz_type,
            "source_no": ledger.source_no,
            "batch_no": ledger.batch_no or "",
        }
        for ledger, product, warehouse in rows
    ]


def dashboard_stats(db: Session) -> dict:
    stock_value = db.query(func.sum(StockBalance.amount)).scalar() or 0
    return {
        "product_count": db.query(func.count(Product.id)).scalar() or 0,
        "warehouse_count": db.query(func.count(Warehouse.id)).scalar() or 0,
        "stock_value": round(_money(stock_value), 2),
        "open_work_orders": db.query(func.count(WorkOrder.id))
        .filter(WorkOrder.status.in_([WorkOrderStatus.RELEASED, WorkOrderStatus.ISSUED]))
        .scalar()
        or 0,
        "draft_orders": db.query(func.count(Order.id))
        .filter(Order.status == OrderStatus.DRAFT)
        .scalar()
        or 0,
        "project_count": db.query(func.count(Project.id)).scalar() or 0,
    }
