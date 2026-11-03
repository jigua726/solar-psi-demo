"""演示数据：一套光伏组件制造 + 工商业分布式项目的完整场景。

用法：
    python -m app.seed            # 空库时初始化
    python -m app.seed --reset    # 清空并重建
"""

import argparse
import sys
from datetime import date
from decimal import Decimal

from .config import BASE_DIR
from .db import Base, SessionLocal, engine
from .models import (
    BizType,
    Bom,
    BomItem,
    Partner,
    PartnerType,
    Product,
    ProductCategory,
    Project,
    SerialNumber,
    SerialStatus,
    User,
    Warehouse,
    WarehouseType,
)
from .security import hash_password
from .services import procurement, production, sales
from .services.inventory import D, stock_in


def _make_products(db):
    products = {}

    raw_specs = [
        ("CELL-182", "182mm 单晶电池片", "片", "0.85"),
        ("GLASS-32", "3.2mm 镀膜光伏玻璃", "片", "80"),
        ("EVA-001", "EVA 封装胶膜", "片", "7.5"),
        ("BACK-001", "背板", "片", "22"),
        ("FRAME-001", "铝合金边框", "套", "32"),
        ("JBOX-001", "接线盒", "个", "11"),
        ("RIBBON-001", "互联焊带", "kg", "60"),
        ("SILICA-001", "密封硅胶", "kg", "28"),
    ]
    for sku, name, unit, cost in raw_specs:
        product = Product(
            sku=sku,
            name=name,
            category=ProductCategory.RAW,
            unit=unit,
            standard_cost=Decimal(cost),
        )
        db.add(product)
        products[sku] = product

    for power in (545, 550, 555):
        product = Product(
            sku=f"MOD-{power}",
            name=f"单晶光伏组件 {power}W",
            category=ProductCategory.FINISHED,
            unit="块",
            power_w=Decimal(power),
            measure_unit="W",
            standard_cost=Decimal("380"),
            tax_rate=Decimal("0.13"),
            warranty_years=25,
            serial_managed=True,
        )
        db.add(product)
        products[f"MOD-{power}"] = product

    ess = Product(
        sku="ESS-100",
        name="工商业储能柜 100kWh",
        category=ProductCategory.FINISHED,
        unit="套",
        capacity_kwh=Decimal("100"),
        measure_unit="kWh",
        standard_cost=Decimal("78000"),
        tax_rate=Decimal("0.13"),
        warranty_years=10,
        serial_managed=True,
    )
    db.add(ess)
    products["ESS-100"] = ess

    inverter = Product(
        sku="INV-100",
        name="组串式逆变器 100kW",
        category=ProductCategory.FINISHED,
        unit="台",
        power_w=Decimal("100000"),
        measure_unit="W",
        standard_cost=Decimal("14500"),
        tax_rate=Decimal("0.13"),
        warranty_years=5,
        serial_managed=False,
    )
    db.add(inverter)
    products["INV-100"] = inverter
    db.flush()
    return products


def _opening_stock(db, product, warehouse, qty, unit_cost, batch="OPEN-2026"):
    stock_in(
        db,
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty=D(qty),
        unit_cost=D(unit_cost),
        biz_type=BizType.OPENING,
        source_type="OPENING",
        source_no=batch,
        batch_no=batch,
    )
    if product.serial_managed:
        start = date.today()
        end = None
        if product.warranty_years:
            end = start.replace(year=start.year + product.warranty_years)
        for index in range(int(qty)):
            db.add(
                SerialNumber(
                    sn=f"SN-OPEN-{product.sku}-{index + 1:04d}",
                    product_id=product.id,
                    status=SerialStatus.IN_STOCK,
                    warehouse_id=warehouse.id,
                    warranty_start=start,
                    warranty_end=end,
                )
            )


def build(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if db.query(User).count():
            print("数据库已有数据，跳过初始化。需要重建请执行：python -m app.seed --reset")
            return

        # 用户
        users = {}
        for username, password, full_name, role in [
            ("admin", "admin123", "王经理", "admin"),
            ("sales", "sales123", "李销售", "sales"),
            ("prod", "prod123", "张生产", "production"),
            ("wh", "wh123", "赵仓管", "warehouse"),
        ]:
            user = User(
                username=username,
                password_hash=hash_password(password),
                full_name=full_name,
                role=role,
            )
            db.add(user)
            users[username] = user
        db.flush()
        admin = users["admin"]

        # 仓库
        raw_wh = Warehouse(code="WH-RAW", name="原料仓", type=WarehouseType.RAW)
        fg_wh = Warehouse(code="WH-FG", name="成品仓", type=WarehouseType.FINISHED)
        site_wh = Warehouse(code="WH-SITE", name="项目现场仓", type=WarehouseType.SITE)
        db.add_all([raw_wh, fg_wh, site_wh])

        # 往来单位
        c1 = Partner(type=PartnerType.CUSTOMER, code="C001", name="华能新能源", contact="刘工")
        c2 = Partner(type=PartnerType.CUSTOMER, code="C002", name="正泰安能", contact="陈工")
        c3 = Partner(type=PartnerType.CUSTOMER, code="C003", name="天合富家", contact="周工")
        s1 = Partner(type=PartnerType.SUPPLIER, code="S001", name="隆基绿能", contact="马经理")
        s2 = Partner(type=PartnerType.SUPPLIER, code="S002", name="通威股份", contact="孙经理")
        s3 = Partner(type=PartnerType.SUPPLIER, code="S003", name="阳光电源", contact="吴经理")
        db.add_all([c1, c2, c3, s1, s2, s3])

        # 项目
        prj1 = Project(
            project_no="PRJ-2026-001",
            name="嘉兴工业园 500kW 分布式光伏",
            customer_id=c1.id,
            capacity_kw=D(500),
            address="浙江省嘉兴市",
        )
        prj2 = Project(
            project_no="PRJ-2026-002",
            name="苏州物流园 300kW 光储项目",
            customer_id=c2.id,
            capacity_kw=D(300),
            address="江苏省苏州市",
        )
        db.add_all([prj1, prj2])
        db.flush()

        products = _make_products(db)

        # 组件 BOM：以 550W 为基准型号，产出按功率分档
        bom = Bom(product_id=products["MOD-550"].id, version="V1", name="550W 组件标准 BOM")
        for sku, qty_per, loss in [
            ("CELL-182", "72", "0.5"),
            ("GLASS-32", "1", "0.3"),
            ("EVA-001", "2", "0.5"),
            ("BACK-001", "1", "0.2"),
            ("FRAME-001", "1", "0.2"),
            ("JBOX-001", "1", "0.1"),
            ("RIBBON-001", "0.6", "1.0"),
            ("SILICA-001", "0.05", "1.0"),
        ]:
            bom.items.append(
                BomItem(
                    material_id=products[sku].id,
                    qty_per=D(qty_per),
                    loss_rate=D(loss),
                    unit=products[sku].unit,
                )
            )
        db.add(bom)

        # 期初库存
        for sku, qty, cost in [
            ("CELL-182", 200000, "0.85"),
            ("GLASS-32", 5000, "80"),
            ("EVA-001", 10000, "7.5"),
            ("BACK-001", 5000, "22"),
            ("FRAME-001", 5000, "32"),
            ("JBOX-001", 5000, "11"),
            ("RIBBON-001", 1000, "60"),
            ("SILICA-001", 200, "28"),
        ]:
            _opening_stock(db, products[sku], raw_wh, qty, cost)
        for sku, qty, cost in [("ESS-100", 20, "78000"), ("INV-100", 30, "14500")]:
            _opening_stock(db, products[sku], fg_wh, qty, cost)

        # 已收货采购单：电池片 50000 片
        po = procurement.create_purchase_order(
            db,
            partner_id=s1.id,
            warehouse_id=raw_wh.id,
            items=[
                {"product_id": products["CELL-182"].id, "qty": D(50000), "unit_price": D("0.86")}
            ],
            operator_id=admin.id,
            remark="9 月电池片补库",
        )
        procurement.approve_purchase(db, po)
        procurement.receive_purchase(db, po, operator_id=admin.id)

        # 草稿采购单：玻璃
        procurement.create_purchase_order(
            db,
            partner_id=s2.id,
            warehouse_id=raw_wh.id,
            items=[
                {"product_id": products["GLASS-32"].id, "qty": D(2000), "unit_price": D("82")}
            ],
            operator_id=admin.id,
            remark="待审核：玻璃备货",
        )

        # 工单 1：1000 块组件，已领料，等待演示完工
        wo1 = production.create_work_order(
            db,
            product_id=products["MOD-550"].id,
            bom_id=bom.id,
            plan_qty=D(1000),
            material_warehouse_id=raw_wh.id,
            finished_warehouse_id=fg_wh.id,
            project_id=prj1.id,
            operator_id=admin.id,
        )
        production.release_work_order(db, wo1)
        production.issue_materials(db, wo1, operator_id=admin.id)

        # 工单 2：500 块组件，留给自动化导入演示
        wo2 = production.create_work_order(
            db,
            product_id=products["MOD-550"].id,
            bom_id=bom.id,
            plan_qty=D(500),
            material_warehouse_id=raw_wh.id,
            finished_warehouse_id=fg_wh.id,
            project_id=prj2.id,
            operator_id=admin.id,
        )
        production.release_work_order(db, wo2)
        production.issue_materials(db, wo2, operator_id=admin.id)

        # 已发货销售单：储能柜 5 套（产生项目毛利）
        so1 = sales.create_sales_order(
            db,
            partner_id=c2.id,
            warehouse_id=fg_wh.id,
            items=[{"product_id": products["ESS-100"].id, "qty": D(5), "unit_price": D(95000)}],
            project_id=prj2.id,
            operator_id=admin.id,
            remark="储能柜首批交付",
        )
        sales.approve_sales_order(db, so1)
        sales.ship_sales_order(db, so1, operator_id=admin.id)

        # 草稿销售单：组件 200 块（演示审核 + 发货）
        sales.create_sales_order(
            db,
            partner_id=c1.id,
            warehouse_id=fg_wh.id,
            items=[{"product_id": products["MOD-550"].id, "qty": D(200), "unit_price": D(429)}],
            project_id=prj1.id,
            operator_id=admin.id,
            remark="待发货：组件首批",
        )

        db.commit()

        # 自动化演示文件
        samples = BASE_DIR / "samples"
        samples.mkdir(exist_ok=True)
        today = date.today().isoformat()
        csv_content = (
            "wo_no,sku,qty,defect_qty,report_date\n"
            f"{wo2.wo_no},MOD-545,150,3,{today}\n"
            f"{wo2.wo_no},MOD-550,200,2,{today}\n"
            f"{wo2.wo_no},MOD-555,150,1,{today}\n"
        )
        (samples / "production_report.csv").write_text(csv_content, encoding="utf-8-sig")

        print("演示数据初始化完成")
        print("  管理员: admin / admin123")
        print("  销售:   sales / sales123")
        print("  生产:   prod  / prod123")
        print("  仓管:   wh    / wh123")
        print(f"  自动化样例: {samples / 'production_report.csv'}")
        print(f"  待完工工单: {wo1.wo_no}（1000 块）")
        print(f"  待导入工单: {wo2.wo_no}（500 块）")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化演示数据")
    parser.add_argument("--reset", action="store_true", help="清空并重建数据库")
    args = parser.parse_args()
    build(reset=args.reset)


if __name__ == "__main__":
    sys.exit(main())
