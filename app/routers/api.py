from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from ..config import ACCESS_TOKEN_EXPIRE_MINUTES
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
    Product,
    Project,
    Role,
    SerialNumber,
    User,
    Warehouse,
    WorkOrder,
)
from ..schemas import (
    BomIn,
    LoginIn,
    OrderIn,
    PartnerIn,
    ProductIn,
    ProjectIn,
    WarehouseIn,
    WorkOrderCompleteIn,
    WorkOrderIn,
)
from ..security import (
    COOKIE_NAME,
    authenticate,
    create_access_token,
    ensure_role,
    get_current_user,
)
from ..services import automation, procurement, production, reports, sales

router = APIRouter(tags=["api"])


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------
def product_out(product: Product) -> dict:
    return {
        "id": product.id,
        "sku": product.sku,
        "name": product.name,
        "category": product.category,
        "unit": product.unit,
        "power_w": float(product.power_w) if product.power_w is not None else None,
        "capacity_kwh": float(product.capacity_kwh)
        if product.capacity_kwh is not None
        else None,
        "measure_unit": product.measure_unit,
        "standard_cost": float(product.standard_cost or 0),
        "tax_rate": float(product.tax_rate or 0),
        "warranty_years": product.warranty_years,
        "serial_managed": product.serial_managed,
    }


def order_out(order: Order) -> dict:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "order_type": order.order_type,
        "status": order.status,
        "partner": order.partner.name if order.partner else "",
        "warehouse": order.warehouse.name if order.warehouse else "",
        "project_no": order.project.project_no if order.project else None,
        "total_amount": float(order.total_amount or 0),
        "total_cost": float(order.total_cost or 0),
        "order_date": order.order_date.isoformat() if order.order_date else None,
        "remark": order.remark,
        "items": [
            {
                "product_id": item.product_id,
                "sku": item.product.sku if item.product else "",
                "name": item.product.name if item.product else "",
                "qty": float(item.qty),
                "unit_price": float(item.unit_price),
                "amount": float(item.amount or 0),
                "batch_no": item.batch_no,
                "unit_cost": float(item.unit_cost) if item.unit_cost is not None else None,
            }
            for item in order.items
        ],
    }


def wo_out(wo: WorkOrder) -> dict:
    return {
        "id": wo.id,
        "wo_no": wo.wo_no,
        "status": wo.status,
        "product": wo.product.name if wo.product else "",
        "plan_qty": float(wo.plan_qty),
        "material_cost": float(wo.material_cost or 0),
        "labor_cost": float(wo.labor_cost or 0),
        "overhead_cost": float(wo.overhead_cost or 0),
        "total_cost": float(wo.total_cost or 0),
        "materials": [
            {
                "material_id": row.material_id,
                "sku": row.material.sku if row.material else "",
                "name": row.material.name if row.material else "",
                "qty": float(row.issued_qty),
                "unit_cost": float(row.unit_cost),
                "amount": float(row.amount),
            }
            for row in wo.materials
        ],
        "outputs": [
            {
                "product_id": row.product_id,
                "sku": row.product.sku if row.product else "",
                "power_w": float(row.power_w) if row.power_w is not None else None,
                "qty": float(row.qty),
                "unit_cost": float(row.unit_cost),
                "amount": float(row.amount),
            }
            for row in wo.outputs
        ],
    }


def job_out(job: JobRun) -> dict:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "total_rows": job.total_rows,
        "success_rows": job.success_rows,
        "failed_rows": job.failed_rows,
        "message": job.message,
        "operator": job.operator,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "errors": [
            {
                "row_no": error.row_no,
                "raw_data": error.raw_data,
                "error_message": error.error_message,
            }
            for error in job.errors
        ],
    }


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------
@router.post("/api/auth/login")
def login(payload: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = authenticate(db, payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user.id, "username": user.username, "role": user.role},
    }


@router.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "role": user.role}


# ---------------------------------------------------------------------------
# 基础资料
# ---------------------------------------------------------------------------
@router.get("/api/products")
def list_products(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [product_out(p) for p in db.query(Product).order_by(Product.sku).all()]


@router.post("/api/products", status_code=201)
def create_product(
    payload: ProductIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN)
    if db.query(Product).filter(Product.sku == payload.sku).first():
        raise BusinessError("SKU 已存在")
    product = Product(**payload.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product_out(product)


@router.get("/api/partners")
def list_partners(
    partner_type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Partner)
    if partner_type:
        query = query.filter(Partner.type == partner_type)
    return [
        {
            "id": p.id,
            "type": p.type,
            "code": p.code,
            "name": p.name,
            "contact": p.contact,
            "phone": p.phone,
        }
        for p in query.order_by(Partner.code).all()
    ]


@router.post("/api/partners", status_code=201)
def create_partner(
    payload: PartnerIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN)
    if db.query(Partner).filter(Partner.code == payload.code).first():
        raise BusinessError("往来单位编码已存在")
    partner = Partner(**payload.model_dump())
    db.add(partner)
    db.commit()
    db.refresh(partner)
    return {"id": partner.id, "code": partner.code, "name": partner.name}


@router.get("/api/warehouses")
def list_warehouses(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return [
        {"id": w.id, "code": w.code, "name": w.name, "type": w.type}
        for w in db.query(Warehouse).order_by(Warehouse.code).all()
    ]


@router.post("/api/warehouses", status_code=201)
def create_warehouse(
    payload: WarehouseIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN)
    if db.query(Warehouse).filter(Warehouse.code == payload.code).first():
        raise BusinessError("仓库编码已存在")
    warehouse = Warehouse(**payload.model_dump())
    db.add(warehouse)
    db.commit()
    db.refresh(warehouse)
    return {"id": warehouse.id, "code": warehouse.code, "name": warehouse.name}


@router.get("/api/projects")
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [
        {
            "id": p.id,
            "project_no": p.project_no,
            "name": p.name,
            "customer_id": p.customer_id,
            "capacity_kw": float(p.capacity_kw) if p.capacity_kw is not None else None,
            "status": p.status,
        }
        for p in db.query(Project).order_by(Project.project_no).all()
    ]


@router.post("/api/projects", status_code=201)
def create_project(
    payload: ProjectIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN)
    if db.query(Project).filter(Project.project_no == payload.project_no).first():
        raise BusinessError("项目号已存在")
    project = Project(**payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "project_no": project.project_no, "name": project.name}


@router.get("/api/boms")
def list_boms(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    result = []
    for bom in db.query(Bom).order_by(Bom.id.desc()).all():
        result.append(
            {
                "id": bom.id,
                "product_id": bom.product_id,
                "product": bom.product.name if bom.product else "",
                "product_sku": bom.product.sku if bom.product else "",
                "version": bom.version,
                "name": bom.name,
                "items": [
                    {
                        "material_id": item.material_id,
                        "sku": item.material.sku,
                        "name": item.material.name,
                        "qty_per": float(item.qty_per),
                        "loss_rate": float(item.loss_rate),
                        "unit": item.unit or item.material.unit,
                    }
                    for item in bom.items
                ],
            }
        )
    return result


@router.post("/api/boms", status_code=201)
def create_bom(
    payload: BomIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN, Role.PRODUCTION)
    product = db.get(Product, payload.product_id)
    if product is None:
        raise BusinessError("成品不存在")
    if not payload.items:
        raise BusinessError("BOM 至少需要一行")
    bom = Bom(product_id=product.id, version=payload.version, name=payload.name or product.name)
    for row in payload.items:
        material = db.get(Product, row.material_id)
        if material is None:
            raise BusinessError(f"原材料不存在：{row.material_id}")
        bom.items.append(
            BomItem(
                material_id=material.id,
                qty_per=row.qty_per,
                loss_rate=row.loss_rate,
                unit=material.unit,
            )
        )
    db.add(bom)
    db.commit()
    db.refresh(bom)
    return {"id": bom.id, "items": len(bom.items)}


# ---------------------------------------------------------------------------
# 采购
# ---------------------------------------------------------------------------
@router.get("/api/purchase-orders")
def list_purchase_orders(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    rows = (
        db.query(Order)
        .filter(Order.order_type == OrderType.PURCHASE)
        .order_by(Order.id.desc())
        .all()
    )
    return [order_out(order) for order in rows]


@router.post("/api/purchase-orders", status_code=201)
def create_purchase_order(
    payload: OrderIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN, Role.WAREHOUSE, Role.PRODUCTION)
    order = procurement.create_purchase_order(
        db,
        partner_id=payload.partner_id,
        warehouse_id=payload.warehouse_id,
        items=[item.model_dump() for item in payload.items],
        operator_id=user.id,
        remark=payload.remark,
    )
    db.commit()
    db.refresh(order)
    return order_out(order)


@router.get("/api/purchase-orders/{order_id}")
def get_purchase_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = db.get(Order, order_id)
    if order is None or order.order_type != OrderType.PURCHASE:
        raise HTTPException(404, "采购单不存在")
    return order_out(order)


@router.post("/api/purchase-orders/{order_id}/approve")
def approve_purchase_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN, Role.WAREHOUSE)
    order = db.get(Order, order_id)
    if order is None or order.order_type != OrderType.PURCHASE:
        raise HTTPException(404, "采购单不存在")
    procurement.approve_purchase(db, order)
    db.commit()
    return order_out(order)


@router.post("/api/purchase-orders/{order_id}/receive")
def receive_purchase_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN, Role.WAREHOUSE)
    order = db.get(Order, order_id)
    if order is None or order.order_type != OrderType.PURCHASE:
        raise HTTPException(404, "采购单不存在")
    procurement.receive_purchase(db, order, operator_id=user.id)
    db.commit()
    return order_out(order)


# ---------------------------------------------------------------------------
# 生产
# ---------------------------------------------------------------------------
@router.get("/api/work-orders")
def list_work_orders(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(WorkOrder).order_by(WorkOrder.id.desc()).all()
    return [wo_out(wo) for wo in rows]


@router.post("/api/work-orders", status_code=201)
def create_work_order(
    payload: WorkOrderIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN, Role.PRODUCTION)
    wo = production.create_work_order(
        db,
        product_id=payload.product_id,
        bom_id=payload.bom_id,
        plan_qty=payload.plan_qty,
        material_warehouse_id=payload.material_warehouse_id,
        finished_warehouse_id=payload.finished_warehouse_id,
        project_id=payload.project_id,
        operator_id=user.id,
    )
    db.commit()
    db.refresh(wo)
    return wo_out(wo)


@router.get("/api/work-orders/{wo_id}")
def get_work_order(
    wo_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    wo = db.get(WorkOrder, wo_id)
    if wo is None:
        raise HTTPException(404, "工单不存在")
    return wo_out(wo)


@router.post("/api/work-orders/{wo_id}/release")
def release_work_order(
    wo_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ensure_role(user, Role.ADMIN, Role.PRODUCTION)
    wo = db.get(WorkOrder, wo_id)
    if wo is None:
        raise HTTPException(404, "工单不存在")
    production.release_work_order(db, wo)
    db.commit()
    return wo_out(wo)


@router.post("/api/work-orders/{wo_id}/issue")
def issue_work_order(
    wo_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ensure_role(user, Role.ADMIN, Role.PRODUCTION)
    wo = db.get(WorkOrder, wo_id)
    if wo is None:
        raise HTTPException(404, "工单不存在")
    production.issue_materials(db, wo, operator_id=user.id)
    db.commit()
    return wo_out(wo)


@router.post("/api/work-orders/{wo_id}/complete")
def complete_work_order(
    wo_id: int,
    payload: WorkOrderCompleteIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN, Role.PRODUCTION)
    wo = db.get(WorkOrder, wo_id)
    if wo is None:
        raise HTTPException(404, "工单不存在")
    production.complete_work_order(
        db,
        wo,
        outputs=[output.model_dump() for output in payload.outputs],
        labor_cost=payload.labor_cost,
        overhead_cost=payload.overhead_cost,
        operator_id=user.id,
    )
    db.commit()
    return wo_out(wo)


# ---------------------------------------------------------------------------
# 销售
# ---------------------------------------------------------------------------
@router.get("/api/sales-orders")
def list_sales_orders(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    rows = (
        db.query(Order)
        .filter(Order.order_type == OrderType.SALES)
        .order_by(Order.id.desc())
        .all()
    )
    return [order_out(order) for order in rows]


@router.post("/api/sales-orders", status_code=201)
def create_sales_order(
    payload: OrderIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN, Role.SALES)
    order = sales.create_sales_order(
        db,
        partner_id=payload.partner_id,
        warehouse_id=payload.warehouse_id,
        items=[item.model_dump() for item in payload.items],
        project_id=payload.project_id,
        operator_id=user.id,
        remark=payload.remark,
    )
    db.commit()
    db.refresh(order)
    return order_out(order)


@router.get("/api/sales-orders/{order_id}")
def get_sales_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = db.get(Order, order_id)
    if order is None or order.order_type != OrderType.SALES:
        raise HTTPException(404, "销售单不存在")
    return order_out(order)


@router.post("/api/sales-orders/{order_id}/approve")
def approve_sales_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN, Role.SALES)
    order = db.get(Order, order_id)
    if order is None or order.order_type != OrderType.SALES:
        raise HTTPException(404, "销售单不存在")
    sales.approve_sales_order(db, order)
    db.commit()
    return order_out(order)


@router.post("/api/sales-orders/{order_id}/ship")
def ship_sales_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_role(user, Role.ADMIN, Role.WAREHOUSE, Role.SALES)
    order = db.get(Order, order_id)
    if order is None or order.order_type != OrderType.SALES:
        raise HTTPException(404, "销售单不存在")
    sales.ship_sales_order(db, order, operator_id=user.id)
    db.commit()
    return order_out(order)


# ---------------------------------------------------------------------------
# 库存
# ---------------------------------------------------------------------------
@router.get("/api/stock/balances")
def stock_balances(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return reports.stock_summary(db)


@router.get("/api/stock/ledger")
def stock_ledger(
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return reports.ledger_rows(db, limit=min(limit, 500))


@router.get("/api/serials")
def list_serials(
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.query(SerialNumber).order_by(SerialNumber.id.desc()).limit(min(limit, 500)).all()
    return [
        {
            "sn": row.sn,
            "product_id": row.product_id,
            "status": row.status,
            "warehouse_id": row.warehouse_id,
            "project_id": row.project_id,
            "warranty_end": row.warranty_end.isoformat() if row.warranty_end else None,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 报表
# ---------------------------------------------------------------------------
@router.get("/api/reports/stock-summary")
def report_stock_summary(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return reports.stock_summary(db)


@router.get("/api/reports/project-margin")
def report_project_margin(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return reports.project_margin(db)


@router.get("/api/reports/wo-cost")
def report_wo_cost(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return reports.work_order_cost(db)


@router.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return reports.dashboard_stats(db)


# ---------------------------------------------------------------------------
# 自动化
# ---------------------------------------------------------------------------
@router.post("/api/automation/production-report")
async def import_production_report(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    content = await file.read()
    if not content:
        raise BusinessError("文件为空")
    job = automation.import_production_report(db, content, operator=user.username)
    db.commit()
    db.refresh(job)
    return job_out(job)


@router.get("/api/automation/jobs")
def list_jobs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(JobRun).order_by(JobRun.id.desc()).limit(50).all()
    return [job_out(job) for job in rows]


@router.get("/api/automation/jobs/{job_id}")
def get_job(
    job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return job_out(job)


@router.get("/api/automation/template")
def download_template(user: User = Depends(get_current_user)):
    return Response(
        content=automation.template_csv().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="production_report_template.csv"'},
    )
