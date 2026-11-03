from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import (
    Order,
    OrderItem,
    OrderStatus,
    OrderType,
    Product,
)
from .inventory import D, q2, stock_in
from .numbering import next_no


def _build_items(db: Session, order: Order, items: list[dict]) -> Decimal:
    if not items:
        raise BusinessError("单据至少需要一行明细")
    total = D(0)
    for row in items:
        product = db.get(Product, row["product_id"])
        if product is None:
            raise BusinessError("商品不存在")
        qty = D(row["qty"])
        price = D(row["unit_price"])
        if qty <= 0:
            raise BusinessError("数量必须大于 0")
        if price < 0:
            raise BusinessError("单价不能为负")
        amount = q2(qty * price)
        order.items.append(
            OrderItem(
                product_id=product.id,
                qty=qty,
                unit_price=price,
                amount=amount,
                tax_rate=product.tax_rate,
                batch_no=row.get("batch_no") or None,
            )
        )
        total += amount
    return q2(total)


def create_purchase_order(
    db: Session,
    *,
    partner_id: int,
    warehouse_id: int,
    items: list[dict],
    operator_id: int | None = None,
    remark: str = "",
) -> Order:
    order = Order(
        order_no=next_no(db, Order, "order_no", "PO"),
        order_type=OrderType.PURCHASE,
        partner_id=partner_id,
        warehouse_id=warehouse_id,
        status=OrderStatus.DRAFT,
        created_by=operator_id,
        remark=remark,
    )
    order.total_amount = _build_items(db, order, items)
    db.add(order)
    db.flush()
    return order


def approve_purchase(db: Session, order: Order) -> Order:
    if order.order_type != OrderType.PURCHASE:
        raise BusinessError("不是采购单")
    if order.status != OrderStatus.DRAFT:
        raise BusinessError(f"只有草稿可以审核，当前状态：{order.status}")
    order.status = OrderStatus.APPROVED
    db.flush()
    return order


def receive_purchase(db: Session, order: Order, operator_id: int | None = None) -> Order:
    """收货：按行入库，批次号默认取单号。同一单据只能执行一次。"""
    if order.order_type != OrderType.PURCHASE:
        raise BusinessError("不是采购单")
    if order.status != OrderStatus.APPROVED:
        raise BusinessError(f"只有已审核单据可以收货，当前状态：{order.status}")

    for item in order.items:
        stock_in(
            db,
            warehouse_id=order.warehouse_id,
            product_id=item.product_id,
            qty=item.qty,
            unit_cost=item.unit_price,
            biz_type="PURCHASE_IN",
            source_type="PURCHASE_ORDER",
            source_id=order.id,
            source_no=order.order_no,
            batch_no=item.batch_no or order.order_no,
            operator_id=operator_id,
        )

    order.status = OrderStatus.EXECUTED
    order.executed_at = datetime.utcnow()
    db.flush()
    return order
