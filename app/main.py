"""
市川市 就労継続支援A型・B型 市場調査ダッシュボード
FastAPI アプリケーション
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.database import (
    get_offices,
    get_stats,
    get_update_log,
    get_work_type_stats,
    init_db,
)
from app.scheduler import run_update, start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時
    init_db()
    start_scheduler()

    # 初回データ取得（DBが空なら）
    stats = get_stats()
    if stats["total"] == 0:
        logger.info("初回データ取得を開始します...")
        result = await run_update()
        logger.info(f"初回取得結果: {result}")

    yield

    # 終了時
    stop_scheduler()


app = FastAPI(
    title="市川市 就労支援事業所 市場調査",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


# ── API エンドポイント ──────────────────────────────────────


@app.get("/api/stats")
def api_stats():
    return get_stats()


@app.get("/api/offices")
def api_offices(service_type: Optional[str] = None):
    return get_offices(service_type)


@app.get("/api/work-types")
def api_work_types(service_type: Optional[str] = None):
    data = get_work_type_stats(service_type)
    return [{"work_type": k, "count": v} for k, v in data]


@app.get("/api/update-log")
def api_update_log():
    return get_update_log(20)


@app.post("/api/update")
async def api_update(force: bool = False):
    result = await run_update(force=force)
    return result


# ── フロントエンド ──────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("app/static/index.html", encoding="utf-8") as f:
        return f.read()
