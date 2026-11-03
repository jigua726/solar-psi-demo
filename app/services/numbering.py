from datetime import datetime

from sqlalchemy.orm import Session


def next_no(db: Session, model, field_name: str, prefix: str) -> str:
    """生成业务单号：PO20261103-0001。演示级实现，生产建议用独立序列表 + 行锁。"""
    today = datetime.now().strftime("%Y%m%d")
    like = f"{prefix}{today}-%"
    column = getattr(model, field_name)
    count = db.query(model).filter(column.like(like)).count()
    return f"{prefix}{today}-{count + 1:04d}"
