#!/usr/bin/env bash
set -euo pipefail

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
else
  echo "未找到虚拟环境，请先执行：python -m venv .venv && pip install -r requirements.txt"
  exit 1
fi

"$PYTHON" -m app.seed
"$PYTHON" -m uvicorn app.main:app --reload --port 8000
