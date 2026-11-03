"""核心业务测试：移动加权成本、工单分档分摊、序列号绑定、自动化导入。"""

import uuid
from decimal import Decimal

from app.models import (
    Bom,
    BomItem,
    OrderStatus,
    Product,
    ProductCategory,
    SerialNumber,
    SerialStatus,
    Warehouse,
    WarehouseType,
    WorkOrder,
    WorkOrderStatus,
)
from app.services import automation, production, sales
from app.services.inventory import stock_in


def uid() -> str:
    return uuid.uuid4().hex[:8].upper()


def make_product(db, category, name, power=None, serial=False, unit="个"):
    product = Product(
        sku=f"{name}-{uid()}",
        name=name,
        category=category,
        unit=unit,
        power_w=power,
        serial_managed=serial,
    )
    db.add(product)
    db.flush()
    return product


def make_warehouse(db, type_=WarehouseType.RAW):
    warehouse = Warehouse(code=f"WH-{uid()}", name="测试仓", type=type_)
    db.add(warehouse)
    db.flush()
    return warehouse


def test_moving_average_cost(db):
    warehouse = make_warehouse(db)
    product = make_product(db, ProductCategory.RAW, "测试原料")
    stock_in(
        db,
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty=100,
        unit_cost=10,
        biz_type="OPENING",
    )
    stock_in(
        db,
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty=100,
        unit_cost=12,
        biz_type="PURCHASE_IN",
    )
    from app.services.inventory import get_balance, stock_out

    unit_cost = stock_out(
        db,
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty=150,
        biz_type="SALES_OUT",
    )
    assert unit_cost == Decimal("11.0000")

    balance = get_balance(db, warehouse.id, product.id)
    assert Decimal(balance.qty) == Decimal("50")
    assert Decimal(balance.amount) == Decimal("550.00")
    db.rollback()


def test_work_order_cost_allocation(db):
    raw_wh = make_warehouse(db, WarehouseType.RAW)
    fg_wh = make_warehouse(db, WarehouseType.FINISHED)
    material = make_product(db, ProductCategory.RAW, "电池片")
    product_a = make_product(db, ProductCategory.FINISHED, "组件A", power=Decimal("545"))
    product_b = make_product(db, ProductCategory.FINISHED, "组件B", power=Decimal("550"))

    bom = Bom(product_id=product_a.id, version="V1")
    bom.items.append(
        BomItem(material_id=material.id, qty_per=Decimal("2"), loss_rate=Decimal("0"))
    )
    db.add(bom)
    db.flush()

    stock_in(
        db,
        warehouse_id=raw_wh.id,
        product_id=material.id,
        qty=100,
        unit_cost=10,
        biz_type="OPENING",
    )

    wo = production.create_work_order(
        db,
        product_id=product_a.id,
        bom_id=bom.id,
        plan_qty=10,
        material_warehouse_id=raw_wh.id,
        finished_warehouse_id=fg_wh.id,
    )
    production.release_work_order(db, wo)
    production.issue_materials(db, wo)

    assert Decimal(wo.material_cost) == Decimal("200.00")

    production.complete_work_order(
        db,
        wo,
        outputs=[
            {"product_id": product_a.id, "qty": 6},
            {"product_id": product_b.id, "qty": 4},
        ],
        labor_cost=100,
        overhead_cost=50,
    )

    assert wo.status == WorkOrderStatus.COMPLETED
    assert Decimal(wo.total_cost) == Decimal("350.00")
    total_allocated = sum(Decimal(row.amount) for row in wo.outputs)
    assert total_allocated == Decimal("350.00")

    # 成本按瓦数比例分摊：总瓦数越高分到的成本越多；每瓦成本应一致
    rows = {row.product_id: row for row in wo.outputs}
    assert rows[product_a.id].amount > rows[product_b.id].amount
    per_watt_a = rows[product_a.id].unit_cost / Decimal("545")
    per_watt_b = rows[product_b.id].unit_cost / Decimal("550")
    assert abs(per_watt_a - per_watt_b) < Decimal("0.001")
    db.rollback()


def test_sales_ship_binds_serial_numbers(db):
    fg_wh = make_warehouse(db, WarehouseType.FINISHED)
    product = make_product(
        db, ProductCategory.FINISHED, "组件SN", power=Decimal("550"), serial=True, unit="块"
    )
    stock_in(
        db,
        warehouse_id=fg_wh.id,
        product_id=product.id,
        qty=10,
        unit_cost=100,
        biz_type="OPENING",
    )
    for index in range(10):
        db.add(
            SerialNumber(
                sn=f"SN-TEST-{uid()}-{index}",
                product_id=product.id,
                status=SerialStatus.IN_STOCK,
                warehouse_id=fg_wh.id,
            )
        )
    db.flush()

    from app.models import Partner, PartnerType, Project

    customer = Partner(type=PartnerType.CUSTOMER, code=f"C-{uid()}", name="测试客户")
    project = Project(project_no=f"PRJ-{uid()}", name="测试项目", customer_id=None)
    db.add_all([customer, project])
    db.flush()

    order = sales.create_sales_order(
        db,
        partner_id=customer.id,
        warehouse_id=fg_wh.id,
        items=[{"product_id": product.id, "qty": 3, "unit_price": 500}],
        project_id=project.id,
    )
    sales.approve_sales_order(db, order)
    sales.ship_sales_order(db, order)

    assert order.status == OrderStatus.EXECUTED
    shipped = (
        db.query(SerialNumber)
        .filter(SerialNumber.product_id == product.id, SerialNumber.status == SerialStatus.SHIPPED)
        .count()
    )
    assert shipped == 3
    from app.services.inventory import get_balance

    assert Decimal(get_balance(db, fg_wh.id, product.id).qty) == Decimal("7")
    db.rollback()


def test_automation_import_production_report(db):
    raw_wh = make_warehouse(db, WarehouseType.RAW)
    fg_wh = make_warehouse(db, WarehouseType.FINISHED)
    material = make_product(db, ProductCategory.RAW, "玻璃")
    product = make_product(
        db, ProductCategory.FINISHED, "组件自动", power=Decimal("550"), serial=True, unit="块"
    )
    bom = Bom(product_id=product.id, version="V1")
    bom.items.append(
        BomItem(material_id=material.id, qty_per=Decimal("1"), loss_rate=Decimal("0"))
    )
    db.add(bom)
    db.flush()

    stock_in(
        db,
        warehouse_id=raw_wh.id,
        product_id=material.id,
        qty=100,
        unit_cost=5,
        biz_type="OPENING",
    )
    wo = production.create_work_order(
        db,
        product_id=product.id,
        bom_id=bom.id,
        plan_qty=10,
        material_warehouse_id=raw_wh.id,
        finished_warehouse_id=fg_wh.id,
    )
    production.release_work_order(db, wo)
    production.issue_materials(db, wo)

    content = (
        "wo_no,sku,qty,defect_qty,report_date\n"
        f"{wo.wo_no},{product.sku},10,0,2026-11-03\n"
    ).encode("utf-8")

    job = automation.import_production_report(db, content, operator="tester")
    assert job.status == "SUCCESS"
    assert job.success_rows == 1

    db.refresh(wo)
    assert wo.status == WorkOrderStatus.COMPLETED
    assert (
        db.query(SerialNumber).filter(SerialNumber.wo_id == wo.id).count() == 10
    )

    # 幂等：同一工单重复导入应失败
    again = automation.import_production_report(db, content, operator="tester")
    assert again.status == "FAILED"
    assert again.failed_rows == 1
    db.rollback()
