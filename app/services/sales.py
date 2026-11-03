from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import (
    Order,
    OrderItem,
    OrderStatus,
    OrderType,
    Product,
    Project,
    SerialNumber,
    SerialStatus,
)
from .inventory import D, q2, stock_out
from .numbering import next_no


def create_sales_order(
    db: Session,
    *,
    partner_id: int,
    warehouse_id: int,
    items: list[dict],
    project_id: int | None = None,
    operator_id: int | None = None,
    remark: str = "",
) -> Order:
    if not items:
        raise BusinessError("销售单至少需要一行明细")
    if project_id is not None and db.get(Project, project_id) is None:
        raise BusinessError("项目不存在")

    order = Order(
        order_no=next_no(db, Order, "order_no", "SO"),
        order_type=OrderType.SALES,
        partner_id=partner_id,
        warehouse_id=warehouse_id,
        project_id=project_id,
        status=OrderStatus.DRAFT,
        created_by=operator_id,
        remark=remark,
    )

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
            )
        )
        total += amount

    order.total_amount = q2(total)
    db.add(order)
    db.flush()
    return order


def approve_sales_order(db: Session, order: Order) -> Order:
    if order.order_type != OrderType.SALES:
        raise BusinessError("不是销售单")
    if order.status != OrderStatus.DRAFT:
        raise BusinessError(f"只有草稿可以审核，当前状态：{order.status}")
    order.status = OrderStatus.APPROVED
    db.flush()
    return order


def ship_sales_order(db: Session, order: Order, operator_id: int | None = None) -> Order:
    """发货：扣减库存、绑定序列号、计算出库成本。"""
    if order.order_type != OrderType.SALES:
        raise BusinessError("不是销售单")
    if order.status != OrderStatus.APPROVED:
        raise BusinessError(f"只有已审核单据可以发货，当前状态：{order.status}")

    total_cost = D(0)
    for item in order.items:
        product = db.get(Product, item.product_id)
        if product is None:
            raise BusinessError("商品不存在")
        qty = D(item.qty)

        serials: list[SerialNumber] = []
        if product.serial_managed:
            if qty != qty.to_integral_value():
                raise BusinessError(f"{product.sku} 数量必须为整数")
            serials = (
                db.query(SerialNumber)
                .filter(
                    SerialNumber.product_id == product.id,
                    SerialNumber.warehouse_id == order.warehouse_id,
                    SerialNumber.status == SerialStatus.IN_STOCK,
                )
                .order_by(SerialNumber.id)
                .limit(int(qty))
                .all()
            )
            if len(serials) < int(qty):
                raise BusinessError(
                    f"{product.sku} 可用序列号不足：需要 {int(qty)}，可用 {len(serials)}"
                )

        unit_cost = stock_out(
            db,
            warehouse_id=order.warehouse_id,
            product_id=product.id,
            qty=qty,
            biz_type="SALES_OUT",
            source_type="SALES_ORDER",
            source_id=order.id,
            source_no=order.order_no,
            project_id=order.project_id,
            operator_id=operator_id,
        )
        item.unit_cost = unit_cost
        total_cost += q2(qty * unit_cost)

        if product.serial_managed:
            warranty_start = date.today()
            warranty_end = None
            if product.warranty_years:
                warranty_end = warranty_start.replace(
                    year=warranty_start.year + product.warranty_years
                )
            for serial in serials:
                serial.status = SerialStatus.SHIPPED
                serial.project_id = order.project_id
                serial.order_id = order.id
                serial.warranty_start = serial.warranty_start or warranty_start
                serial.warranty_end = serial.warranty_end or warranty_end

    order.total_cost = q2(total_cost)
    order.status = OrderStatus.EXECUTED
    order.executed_at = datetime.utcnow()
    db.flush()
    return order
