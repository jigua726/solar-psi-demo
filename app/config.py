import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'psi.db'}")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-secret-change-me-32bytes-minimum")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

APP_NAME = "光伏组件制造 · 轻量 MES + 进销存 Demo"
APP_VERSION = "0.1.0"
