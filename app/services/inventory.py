"""库存引擎：流水是唯一真相，余额是加速表。

- 出库成本使用移动加权平均（按 仓库 + 商品 维度）。
- 批次只做追溯，不参与计价（与 README 中的成本法说明一致）。
- 所有库存变动必须经过 stock_in / stock_out，禁止直接改余额。
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import (
    BizType,
    Direction,
    StockBalance,
    StockLedger,
)


def D(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def q2(value) -> Decimal:
    return D(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def q4(value) -> Decimal:
    return D(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def get_balance(
    db: Session, warehouse_id: int, product_id: int, for_update: bool = False
) -> StockBalance:
    query = db.query(StockBalance).filter(
        StockBalance.warehouse_id == warehouse_id,
        StockBalance.product_id == product_id,
    )
    if for_update:
        query = query.with_for_update()
    balance = query.one_or_none()
    if balance is None:
        balance = StockBalance(
            warehouse_id=warehouse_id,
            product_id=product_id,
            qty=D(0),
            amount=D(0),
        )
        db.add(balance)
        db.flush()
    return balance


def stock_in(
    db: Session,
    *,
    warehouse_id: int,
    product_id: int,
    qty,
    unit_cost,
    biz_type: str,
    source_type: str = "",
    source_id: int | None = None,
    source_no: str = "",
    batch_no: str | None = None,
    project_id: int | None = None,
    operator_id: int | None = None,
) -> StockLedger:
    qty = D(qty)
    if qty <= 0:
        raise BusinessError("入库数量必须大于 0")
    unit_cost = q4(unit_cost)
    if unit_cost < 0:
        raise BusinessError("入库成本不能为负")

    amount = q2(qty * unit_cost)
    balance = get_balance(db, warehouse_id, product_id)
    balance.qty = D(balance.qty) + qty
    balance.amount = D(balance.amount) + amount
    balance.updated_at = datetime.utcnow()

    ledger = StockLedger(
        warehouse_id=warehouse_id,
        product_id=product_id,
        batch_no=batch_no,
        direction=Direction.IN,
        qty=qty,
        unit_cost=unit_cost,
        amount=amount,
        biz_type=biz_type,
        source_type=source_type,
        source_id=source_id,
        source_no=source_no,
        project_id=project_id,
        operator_id=operator_id,
    )
    db.add(ledger)
    db.flush()
    return ledger


def stock_out(
    db: Session,
    *,
    warehouse_id: int,
    product_id: int,
    qty,
    biz_type: str,
    source_type: str = "",
    source_id: int | None = None,
    source_no: str = "",
    batch_no: str | None = None,
    project_id: int | None = None,
    operator_id: int | None = None,
) -> Decimal:
    """扣减库存并返回本次出库的移动加权单位成本。"""
    qty = D(qty)
    if qty <= 0:
        raise BusinessError("出库数量必须大于 0")

    balance = get_balance(db, warehouse_id, product_id, for_update=True)
    available = D(balance.qty)
    if available < qty:
        raise BusinessError(
            f"库存不足：商品 {product_id} 现存 {available}，需要 {qty}"
        )

    average_cost = (D(balance.amount) / available) if available > 0 else D(0)
    amount = q2(qty * average_cost)
    balance.qty = available - qty
    balance.amount = D(balance.amount) - amount
    balance.updated_at = datetime.utcnow()

    ledger = StockLedger(
        warehouse_id=warehouse_id,
        product_id=product_id,
        batch_no=batch_no,
        direction=Direction.OUT,
        qty=qty,
        unit_cost=q4(average_cost),
        amount=amount,
        biz_type=biz_type,
        source_type=source_type,
        source_id=source_id,
        source_no=source_no,
        project_id=project_id,
        operator_id=operator_id,
    )
    db.add(ledger)
    db.flush()
    return q4(average_cost)
