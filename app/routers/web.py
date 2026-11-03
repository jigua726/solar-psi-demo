from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import BusinessError
from ..models import (
    Bom,
    BomItem,
    JobRun,
    Order,
    OrderStatus,
    OrderType,
    Partner,
    PartnerType,
    Product,
    ProductCategory,
    Project,
    Role,
    SerialNumber,
    User,
    Warehouse,
    WorkOrder,
    WorkOrderStatus,
)
from ..security import (
    COOKIE_NAME,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    authenticate,
    create_access_token,
    ensure_role,
    get_web_user,
)
from ..services import automation, procurement, production, reports, sales

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def page(request: Request, name: str, user: User | None = None, **context):
    data = {
        "user": user,
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
        **context,
    }
    return templates.TemplateResponse(request, name, data)


def redirect(url: str, msg: str | None = None, err: str | None = None):
    params = []
    if msg:
        params.append(f"msg={quote(msg)}")
    if err:
        params.append(f"err={quote(err)}")
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{'&'.join(params)}"
    return RedirectResponse(url, status_code=303)


def dec(value: str | None, default: Decimal | None = None) -> Decimal | None:
    if value is None:
        return default
    value = value.strip()
    if not value:
        return default
    return Decimal(value)


def parse_lines(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        rows.append([part.strip() for part in line.replace("，", ",").split(",")])
    return rows


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
@router.get("/login")
def login_page(request: Request):
    return page(request, "login.html")


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate(db, username, password)
    if user is None:
        return page(request, "login.html", err="用户名或密码错误")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        create_access_token(user),
        httponly=True,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# 概览
# ---------------------------------------------------------------------------
@router.get("/")
def dashboard(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    stats = reports.dashboard_stats(db)
    jobs = db.query(JobRun).order_by(JobRun.id.desc()).limit(5).all()
    open_wos = (
        db.query(WorkOrder)
        .filter(WorkOrder.status.in_([WorkOrderStatus.RELEASED, WorkOrderStatus.ISSUED]))
        .order_by(WorkOrder.id.desc())
        .limit(5)
        .all()
    )
    return page(request, "dashboard.html", user, stats=stats, jobs=jobs, wos=open_wos)


# ---------------------------------------------------------------------------
# 基础资料
# ---------------------------------------------------------------------------
@router.get("/products")
def products_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    products = db.query(Product).order_by(Product.sku).all()
    return page(request, "products.html", user, products=products)


@router.post("/products")
def products_create(
    request: Request,
    sku: str = Form(...),
    name: str = Form(...),
    category: str = Form("RAW"),
    unit: str = Form("个"),
    power_w: str = Form(""),
    measure_unit: str = Form(""),
    standard_cost: str = Form("0"),
    warranty_years: str = Form(""),
    serial_managed: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN)
        if db.query(Product).filter(Product.sku == sku).first():
            raise BusinessError("SKU 已存在")
        product = Product(
            sku=sku.strip(),
            name=name.strip(),
            category=category,
            unit=unit.strip() or "个",
            power_w=dec(power_w),
            measure_unit=measure_unit.strip() or None,
            standard_cost=dec(standard_cost, Decimal("0")),
            warranty_years=int(warranty_years) if warranty_years.strip() else None,
            serial_managed=bool(serial_managed),
        )
        db.add(product)
        db.commit()
        return redirect("/products", msg=f"物料 {sku} 已创建")
    except BusinessError as exc:
        db.rollback()
        return redirect("/products", err=exc.message)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return redirect("/products", err=str(exc))


@router.get("/partners")
def partners_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    partners = db.query(Partner).order_by(Partner.type, Partner.code).all()
    return page(request, "partners.html", user, partners=partners)


@router.post("/partners")
def partners_create(
    request: Request,
    type: str = Form("CUSTOMER"),
    code: str = Form(...),
    name: str = Form(...),
    contact: str = Form(""),
    phone: str = Form(""),
    tax_no: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN)
        if db.query(Partner).filter(Partner.code == code).first():
            raise BusinessError("编码已存在")
        db.add(
            Partner(
                type=type,
                code=code.strip(),
                name=name.strip(),
                contact=contact,
                phone=phone,
                tax_no=tax_no,
            )
        )
        db.commit()
        return redirect("/partners", msg=f"往来单位 {name} 已创建")
    except BusinessError as exc:
        db.rollback()
        return redirect("/partners", err=exc.message)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return redirect("/partners", err=str(exc))


@router.get("/warehouses")
def warehouses_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    warehouses = db.query(Warehouse).order_by(Warehouse.code).all()
    return page(request, "warehouses.html", user, warehouses=warehouses)


@router.post("/warehouses")
def warehouses_create(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    type: str = Form("RAW"),
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN)
        if db.query(Warehouse).filter(Warehouse.code == code).first():
            raise BusinessError("仓库编码已存在")
        db.add(Warehouse(code=code.strip(), name=name.strip(), type=type))
        db.commit()
        return redirect("/warehouses", msg=f"仓库 {name} 已创建")
    except BusinessError as exc:
        db.rollback()
        return redirect("/warehouses", err=exc.message)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return redirect("/warehouses", err=str(exc))


@router.get("/projects")
def projects_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    projects = db.query(Project).order_by(Project.project_no).all()
    customers = db.query(Partner).filter(Partner.type == PartnerType.CUSTOMER).all()
    return page(request, "projects.html", user, projects=projects, customers=customers)


@router.post("/projects")
def projects_create(
    request: Request,
    project_no: str = Form(...),
    name: str = Form(...),
    customer_id: str = Form(""),
    capacity_kw: str = Form(""),
    address: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN)
        if db.query(Project).filter(Project.project_no == project_no).first():
            raise BusinessError("项目号已存在")
        db.add(
            Project(
                project_no=project_no.strip(),
                name=name.strip(),
                customer_id=int(customer_id) if customer_id else None,
                capacity_kw=dec(capacity_kw),
                address=address,
            )
        )
        db.commit()
        return redirect("/projects", msg=f"项目 {name} 已创建")
    except BusinessError as exc:
        db.rollback()
        return redirect("/projects", err=exc.message)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return redirect("/projects", err=str(exc))


@router.get("/boms")
def boms_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    boms = db.query(Bom).order_by(Bom.id.desc()).all()
    finished = db.query(Product).filter(Product.category == ProductCategory.FINISHED).all()
    materials = db.query(Product).filter(Product.category != ProductCategory.FINISHED).all()
    return page(
        request, "boms.html", user, boms=boms, finished=finished, materials=materials
    )


@router.post("/boms")
def boms_create(
    request: Request,
    product_id: int = Form(...),
    version: str = Form("V1"),
    lines: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN, Role.PRODUCTION)
        product = db.get(Product, product_id)
        if product is None:
            raise BusinessError("成品不存在")
        bom = Bom(product_id=product.id, version=version.strip() or "V1", name=product.name)
        for parts in parse_lines(lines):
            if len(parts) < 2:
                raise BusinessError(f"BOM 行格式错误：{','.join(parts)}")
            material = db.query(Product).filter(Product.sku == parts[0]).first()
            if material is None:
                raise BusinessError(f"原材料不存在：{parts[0]}")
            bom.items.append(
                BomItem(
                    material_id=material.id,
                    qty_per=Decimal(parts[1]),
                    loss_rate=Decimal(parts[2]) if len(parts) > 2 and parts[2] else Decimal("0"),
                    unit=material.unit,
                )
            )
        if not bom.items:
            raise BusinessError("BOM 至少需要一行")
        db.add(bom)
        db.commit()
        return redirect("/boms", msg=f"BOM 已创建（{len(bom.items)} 行）")
    except BusinessError as exc:
        db.rollback()
        return redirect("/boms", err=exc.message)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return redirect("/boms", err=str(exc))


# ---------------------------------------------------------------------------
# 采购
# ---------------------------------------------------------------------------
@router.get("/purchase")
def purchase_list(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    orders = (
        db.query(Order)
        .filter(Order.order_type == OrderType.PURCHASE)
        .order_by(Order.id.desc())
        .all()
    )
    return page(request, "purchase_list.html", user, orders=orders)


@router.get("/purchase/new")
def purchase_new(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    suppliers = db.query(Partner).filter(Partner.type == PartnerType.SUPPLIER).all()
    warehouses = db.query(Warehouse).all()
    return page(
        request, "purchase_new.html", user, suppliers=suppliers, warehouses=warehouses
    )


@router.post("/purchase/new")
def purchase_create(
    request: Request,
    partner_id: int = Form(...),
    warehouse_id: int = Form(...),
    remark: str = Form(""),
    lines: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN, Role.WAREHOUSE, Role.PRODUCTION)
        items = []
        for parts in parse_lines(lines):
            if len(parts) < 3:
                raise BusinessError(f"明细格式错误：{','.join(parts)}")
            product = db.query(Product).filter(Product.sku == parts[0]).first()
            if product is None:
                raise BusinessError(f"物料不存在：{parts[0]}")
            items.append(
                {
                    "product_id": product.id,
                    "qty": Decimal(parts[1]),
                    "unit_price": Decimal(parts[2]),
                    "batch_no": parts[3] if len(parts) > 3 and parts[3] else None,
                }
            )
        order = procurement.create_purchase_order(
            db,
            partner_id=partner_id,
            warehouse_id=warehouse_id,
            items=items,
            operator_id=user.id,
            remark=remark,
        )
        db.commit()
        return redirect(f"/purchase/{order.id}", msg=f"采购单 {order.order_no} 已创建")
    except BusinessError as exc:
        db.rollback()
        return redirect("/purchase/new", err=exc.message)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return redirect("/purchase/new", err=str(exc))


@router.get("/purchase/{order_id}")
def purchase_detail(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    order = db.get(Order, order_id)
    if order is None or order.order_type != OrderType.PURCHASE:
        return redirect("/purchase", err="采购单不存在")
    return page(request, "purchase_detail.html", user, order=order)


@router.post("/purchase/{order_id}/approve")
def purchase_approve(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN, Role.WAREHOUSE)
        order = db.get(Order, order_id)
        if order is None:
            raise BusinessError("采购单不存在")
        procurement.approve_purchase(db, order)
        db.commit()
        return redirect(f"/purchase/{order_id}", msg="审核通过")
    except BusinessError as exc:
        db.rollback()
        return redirect(f"/purchase/{order_id}", err=exc.message)


@router.post("/purchase/{order_id}/receive")
def purchase_receive(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN, Role.WAREHOUSE)
        order = db.get(Order, order_id)
        if order is None:
            raise BusinessError("采购单不存在")
        procurement.receive_purchase(db, order, operator_id=user.id)
        db.commit()
        return redirect(f"/purchase/{order_id}", msg="收货完成，原料已入库")
    except BusinessError as exc:
        db.rollback()
        return redirect(f"/purchase/{order_id}", err=exc.message)


# ---------------------------------------------------------------------------
# 生产
# ---------------------------------------------------------------------------
@router.get("/work-orders")
def wo_list(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    work_orders = db.query(WorkOrder).order_by(WorkOrder.id.desc()).all()
    return page(request, "wo_list.html", user, work_orders=work_orders)


@router.get("/work-orders/new")
def wo_new(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    products = db.query(Product).filter(Product.category == ProductCategory.FINISHED).all()
    boms = db.query(Bom).all()
    warehouses = db.query(Warehouse).all()
    projects = db.query(Project).all()
    return page(
        request,
        "wo_new.html",
        user,
        products=products,
        boms=boms,
        warehouses=warehouses,
        projects=projects,
    )


@router.post("/work-orders/new")
def wo_create(
    request: Request,
    product_id: int = Form(...),
    bom_id: int = Form(...),
    plan_qty: str = Form(...),
    material_warehouse_id: int = Form(...),
    finished_warehouse_id: int = Form(...),
    project_id: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN, Role.PRODUCTION)
        wo = production.create_work_order(
            db,
            product_id=product_id,
            bom_id=bom_id,
            plan_qty=Decimal(plan_qty),
            material_warehouse_id=material_warehouse_id,
            finished_warehouse_id=finished_warehouse_id,
            project_id=int(project_id) if project_id else None,
            operator_id=user.id,
        )
        db.commit()
        return redirect(f"/work-orders/{wo.id}", msg=f"工单 {wo.wo_no} 已创建")
    except BusinessError as exc:
        db.rollback()
        return redirect("/work-orders/new", err=exc.message)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return redirect("/work-orders/new", err=str(exc))


@router.get("/work-orders/{wo_id}")
def wo_detail(
    wo_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    wo = db.get(WorkOrder, wo_id)
    if wo is None:
        return redirect("/work-orders", err="工单不存在")
    return page(request, "wo_detail.html", user, wo=wo)


@router.post("/work-orders/{wo_id}/release")
def wo_release(
    wo_id: int, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    try:
        ensure_role(user, Role.ADMIN, Role.PRODUCTION)
        wo = db.get(WorkOrder, wo_id)
        if wo is None:
            raise BusinessError("工单不存在")
        production.release_work_order(db, wo)
        db.commit()
        return redirect(f"/work-orders/{wo_id}", msg="工单已下达")
    except BusinessError as exc:
        db.rollback()
        return redirect(f"/work-orders/{wo_id}", err=exc.message)


@router.post("/work-orders/{wo_id}/issue")
def wo_issue(
    wo_id: int, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    try:
        ensure_role(user, Role.ADMIN, Role.PRODUCTION)
        wo = db.get(WorkOrder, wo_id)
        if wo is None:
            raise BusinessError("工单不存在")
        production.issue_materials(db, wo, operator_id=user.id)
        db.commit()
        return redirect(f"/work-orders/{wo_id}", msg="领料完成，材料成本已归集")
    except BusinessError as exc:
        db.rollback()
        return redirect(f"/work-orders/{wo_id}", err=exc.message)


@router.post("/work-orders/{wo_id}/complete")
def wo_complete(
    wo_id: int,
    request: Request,
    lines: str = Form(""),
    labor_cost: str = Form("0"),
    overhead_cost: str = Form("0"),
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN, Role.PRODUCTION)
        wo = db.get(WorkOrder, wo_id)
        if wo is None:
            raise BusinessError("工单不存在")
        outputs = []
        for parts in parse_lines(lines):
            if len(parts) < 2:
                raise BusinessError(f"产出格式错误：{','.join(parts)}")
            product = db.query(Product).filter(Product.sku == parts[0]).first()
            if product is None:
                raise BusinessError(f"成品不存在：{parts[0]}")
            outputs.append({"product_id": product.id, "qty": Decimal(parts[1])})
        production.complete_work_order(
            db,
            wo,
            outputs=outputs,
            labor_cost=Decimal(labor_cost or "0"),
            overhead_cost=Decimal(overhead_cost or "0"),
            operator_id=user.id,
        )
        db.commit()
        return redirect(f"/work-orders/{wo_id}", msg="完工入库完成，成本已按功率分摊")
    except BusinessError as exc:
        db.rollback()
        return redirect(f"/work-orders/{wo_id}", err=exc.message)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return redirect(f"/work-orders/{wo_id}", err=str(exc))


# ---------------------------------------------------------------------------
# 销售
# ---------------------------------------------------------------------------
@router.get("/sales")
def sales_list(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    orders = (
        db.query(Order)
        .filter(Order.order_type == OrderType.SALES)
        .order_by(Order.id.desc())
        .all()
    )
    return page(request, "sales_list.html", user, orders=orders)


@router.get("/sales/new")
def sales_new(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    customers = db.query(Partner).filter(Partner.type == PartnerType.CUSTOMER).all()
    projects = db.query(Project).all()
    warehouses = db.query(Warehouse).all()
    return page(
        request,
        "sales_new.html",
        user,
        customers=customers,
        projects=projects,
        warehouses=warehouses,
    )


@router.post("/sales/new")
def sales_create(
    request: Request,
    partner_id: int = Form(...),
    warehouse_id: int = Form(...),
    project_id: str = Form(""),
    remark: str = Form(""),
    lines: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN, Role.SALES)
        items = []
        for parts in parse_lines(lines):
            if len(parts) < 3:
                raise BusinessError(f"明细格式错误：{','.join(parts)}")
            product = db.query(Product).filter(Product.sku == parts[0]).first()
            if product is None:
                raise BusinessError(f"商品不存在：{parts[0]}")
            items.append(
                {
                    "product_id": product.id,
                    "qty": Decimal(parts[1]),
                    "unit_price": Decimal(parts[2]),
                }
            )
        order = sales.create_sales_order(
            db,
            partner_id=partner_id,
            warehouse_id=warehouse_id,
            items=items,
            project_id=int(project_id) if project_id else None,
            operator_id=user.id,
            remark=remark,
        )
        db.commit()
        return redirect(f"/sales/{order.id}", msg=f"销售单 {order.order_no} 已创建")
    except BusinessError as exc:
        db.rollback()
        return redirect("/sales/new", err=exc.message)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return redirect("/sales/new", err=str(exc))


@router.get("/sales/{order_id}")
def sales_detail(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    order = db.get(Order, order_id)
    if order is None or order.order_type != OrderType.SALES:
        return redirect("/sales", err="销售单不存在")
    return page(request, "sales_detail.html", user, order=order)


@router.post("/sales/{order_id}/approve")
def sales_approve(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN, Role.SALES)
        order = db.get(Order, order_id)
        if order is None:
            raise BusinessError("销售单不存在")
        sales.approve_sales_order(db, order)
        db.commit()
        return redirect(f"/sales/{order_id}", msg="审核通过")
    except BusinessError as exc:
        db.rollback()
        return redirect(f"/sales/{order_id}", err=exc.message)


@router.post("/sales/{order_id}/ship")
def sales_ship(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        ensure_role(user, Role.ADMIN, Role.WAREHOUSE, Role.SALES)
        order = db.get(Order, order_id)
        if order is None:
            raise BusinessError("销售单不存在")
        sales.ship_sales_order(db, order, operator_id=user.id)
        db.commit()
        return redirect(f"/sales/{order_id}", msg="发货完成，序列号已绑定项目")
    except BusinessError as exc:
        db.rollback()
        return redirect(f"/sales/{order_id}", err=exc.message)


# ---------------------------------------------------------------------------
# 库存 / 报表 / 自动化
# ---------------------------------------------------------------------------
@router.get("/stock")
def stock_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    balances = reports.stock_summary(db)
    ledger = reports.ledger_rows(db, limit=50)
    serials = db.query(SerialNumber).order_by(SerialNumber.id.desc()).limit(20).all()
    return page(
        request, "stock.html", user, balances=balances, ledger=ledger, serials=serials
    )


@router.get("/reports")
def reports_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    return page(
        request,
        "reports.html",
        user,
        margins=reports.project_margin(db),
        wo_costs=reports.work_order_cost(db),
        stock=reports.stock_summary(db),
    )


@router.get("/automation")
def automation_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_web_user)
):
    jobs = db.query(JobRun).order_by(JobRun.id.desc()).limit(30).all()
    return page(request, "automation.html", user, jobs=jobs)


@router.post("/automation/upload")
async def automation_upload(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    try:
        content = await file.read()
        if not content:
            raise BusinessError("文件为空")
        job = automation.import_production_report(db, content, operator=user.username)
        db.commit()
        return redirect(f"/automation/jobs/{job.id}", msg=f"导入完成：{job.message}")
    except BusinessError as exc:
        db.rollback()
        return redirect("/automation", err=exc.message)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return redirect("/automation", err=str(exc))


@router.get("/automation/jobs/{job_id}")
def automation_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_web_user),
):
    job = db.get(JobRun, job_id)
    if job is None:
        return redirect("/automation", err="任务不存在")
    return page(request, "job_detail.html", user, job=job)
