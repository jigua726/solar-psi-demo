$ErrorActionPreference = "Stop"

$python = ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "未找到虚拟环境，请先执行：python -m venv .venv && .venv\Scripts\pip install -r requirements.txt"
}

& $python -m app.seed
& $python -m uvicorn app.main:app --reload --port 8000
