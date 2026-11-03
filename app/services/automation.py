"""自动化：生产日报导入（RPA 场景）。

流程：上传 CSV -> 解析 -> 逐行校验 -> 按工单分组完工 -> 执行日志 + 异常行报告。
幂等性：同一工单只会被完工一次（工单状态从 ISSUED 变为 COMPLETED 后再次导入会报错）。
"""

import csv
import io
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from ..models import (
    ImportErrorRow,
    JobRun,
    JobStatus,
    Product,
    ProductCategory,
    WorkOrder,
    WorkOrderStatus,
)
from .production import complete_work_order

REQUIRED_COLUMNS = ("wo_no", "sku", "qty")


def template_csv() -> str:
    return (
        "wo_no,sku,qty,defect_qty,report_date\n"
        "WO20261103-0001,MOD-550,600,5,2026-11-03\n"
    )


def import_production_report(db: Session, content: bytes, operator: str = "") -> JobRun:
    job = JobRun(
        job_type="PRODUCTION_REPORT_IMPORT",
        trigger_type="MANUAL",
        status=JobStatus.RUNNING,
        operator=operator,
    )
    db.add(job)
    db.flush()

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("gbk", errors="replace")

    try:
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = reader.fieldnames or []
        rows = [
            row for row in reader if any((value or "").strip() for value in row.values())
        ]
    except Exception as exc:  # noqa: BLE001
        job.status = JobStatus.FAILED
        job.message = f"文件解析失败：{exc}"
        job.finished_at = datetime.utcnow()
        db.flush()
        return job

    job.total_rows = len(rows)
    missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if missing:
        job.status = JobStatus.FAILED
        job.message = f"缺少列：{', '.join(missing)}"
        job.finished_at = datetime.utcnow()
        db.flush()
        return job

    errors: list[tuple[int, dict, str]] = []
    bad_wo_ids: set[int] = set()
    groups: dict[int, dict] = {}
    seen: set[tuple[int, str]] = set()

    for index, row in enumerate(rows, start=2):
        wo: WorkOrder | None = None
        try:
            wo_no = (row.get("wo_no") or "").strip()
            sku = (row.get("sku") or "").strip()
            if not wo_no or not sku:
                raise ValueError("工单号与 SKU 不能为空")
            wo = db.query(WorkOrder).filter(WorkOrder.wo_no == wo_no).one_or_none()
            if wo is None:
                raise ValueError(f"工单不存在：{wo_no}")
            if wo.status != WorkOrderStatus.ISSUED:
                raise ValueError(f"工单状态为 {wo.status}，只有已领料工单可完工")
            product = db.query(Product).filter(Product.sku == sku).one_or_none()
            if product is None:
                raise ValueError(f"商品不存在：{sku}")
            if product.category != ProductCategory.FINISHED:
                raise ValueError("只能导入成品")
            try:
                qty = Decimal(str((row.get("qty") or "").strip()))
            except InvalidOperation as exc:
                raise ValueError("数量格式不正确") from exc
            if qty <= 0:
                raise ValueError("数量必须大于 0")
            if product.serial_managed and qty != qty.to_integral_value():
                raise ValueError("序列号管理的商品数量必须为整数")
            key = (wo.id, product.id)
            if key in seen:
                raise ValueError("同一工单同一 SKU 重复出现")
            seen.add(key)
            groups.setdefault(wo.id, {"wo": wo, "rows": []})["rows"].append(
                (index, product, qty, row)
            )
        except Exception as exc:  # noqa: BLE001
            if wo is not None:
                bad_wo_ids.add(wo.id)
            errors.append((index, row, str(exc)))

    success_rows = 0
    for wo_id, group in groups.items():
        wo = group["wo"]
        if wo_id in bad_wo_ids:
            for index, _, _, row in group["rows"]:
                errors.append((index, row, "同一工单存在错误行，整单跳过"))
            continue
        outputs = [{"product_id": product.id, "qty": qty} for _, product, qty, _ in group["rows"]]
        try:
            with db.begin_nested():
                complete_work_order(db, wo, outputs)
            success_rows += len(group["rows"])
        except Exception as exc:  # noqa: BLE001
            for index, _, _, row in group["rows"]:
                errors.append((index, row, f"完工失败：{exc}"))

    job.success_rows = success_rows
    job.failed_rows = len(errors)
    for index, row, message in errors:
        job.errors.append(
            ImportErrorRow(
                row_no=index,
                raw_data=json.dumps(row, ensure_ascii=False, default=str),
                error_message=message[:500],
            )
        )

    if not rows:
        job.status = JobStatus.FAILED
        job.message = "文件没有数据行"
    elif not errors:
        job.status = JobStatus.SUCCESS
        job.message = f"成功完工 {success_rows} 行"
    elif success_rows == 0:
        job.status = JobStatus.FAILED
        job.message = f"全部失败：{len(errors)} 行"
    else:
        job.status = JobStatus.PARTIAL
        job.message = f"部分成功：成功 {success_rows} 行，失败 {len(errors)} 行"

    job.finished_at = datetime.utcnow()
    db.flush()
    return job
