from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import APP_NAME, APP_VERSION
from .db import init_db
from .errors import BusinessError
from .routers import api, web


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "光伏组件制造商 · 轻量 MES + 进销存 Demo。"
        "覆盖 BOM、工单领料、功率分档完工、序列号追溯、项目发货与毛利，"
        "并内置生产日报自动导入（RPA 场景）。"
    ),
    lifespan=lifespan,
)

app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)
app.include_router(web.router)
app.include_router(api.router)


@app.exception_handler(BusinessError)
async def business_error_handler(request: Request, exc: BusinessError):
    return JSONResponse(status_code=400, content={"detail": exc.message})


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "app": APP_NAME, "version": APP_VERSION}
