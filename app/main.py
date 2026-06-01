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
from pydantic import BaseModel

from app.database import (
    get_offices,
    get_stats,
    get_update_log,
    get_work_type_stats,
    init_db,
    update_memo,
    update_favorite,
)
from app.scheduler import run_update, start_scheduler, stop_scheduler
from app.comparison import fetch_city_comparison, get_city_comparison

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


class MemoRequest(BaseModel):
    memo: str

class FavoriteRequest(BaseModel):
    is_favorite: bool


@app.get("/api/offices")
def api_offices(service_type: Optional[str] = None, favorite_only: bool = False):
    return get_offices(service_type, favorite_only)


@app.patch("/api/offices/{office_id}/memo")
def api_update_memo(office_id: int, body: MemoRequest):
    update_memo(office_id, body.memo)
    return {"ok": True}


@app.patch("/api/offices/{office_id}/favorite")
def api_update_favorite(office_id: int, body: FavoriteRequest):
    update_favorite(office_id, body.is_favorite)
    return {"ok": True}


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


@app.get("/api/neighboring-cities")
def api_neighboring_cities():
    data = get_city_comparison()
    if not data:
        # キャッシュがなければ同期取得（初回のみ遅い）
        data = fetch_city_comparison()
    return data


@app.post("/api/neighboring-cities/refresh")
async def api_neighboring_cities_refresh():
    import asyncio
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, fetch_city_comparison)
    return {"ok": True, "count": len(data)}


# ── フロントエンド ──────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("app/static/index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/simulator", response_class=HTMLResponse)
async def simulator():
    with open("app/static/simulator.html", encoding="utf-8") as f:
        return f.read()
