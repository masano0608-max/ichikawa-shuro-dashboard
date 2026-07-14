"""
精神科訪問看護ステーション 開業準備ダッシュボード
FastAPI アプリケーション
"""

import json as _json
import logging
import os
import threading
import urllib.request
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.database import (
    init_db,
    get_houmon_offices,
    get_houmon_stats,
    get_houmon_update_log,
    update_houmon_memo,
    update_houmon_favorite,
    kv_get,
    kv_set,
    save_contact,
    get_contacts,
)
from app.scheduler import run_houmon_update, start_scheduler, stop_scheduler
from app.fetcher_houmon import fetch_houmon_comparison

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

    # 訪問看護の初回データ取得
    houmon_stats = get_houmon_stats()
    if houmon_stats["total"] == 0:
        logger.info("訪問看護 初回データ取得を開始します...")
        result = await run_houmon_update()
        logger.info(f"訪問看護 初回取得結果: {result}")

    yield

    # 終了時
    stop_scheduler()


app = FastAPI(
    title="精神科訪問看護ステーション 開業準備ダッシュボード",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


# ── API エンドポイント ──────────────────────────────────────


class MemoRequest(BaseModel):
    memo: str

class FavoriteRequest(BaseModel):
    is_favorite: bool

class ContactRequest(BaseModel):
    name: str
    contact: str
    type: str = ""
    message: str = ""


# ── 訪問看護 API エンドポイント ─────────────────────────────


@app.get("/api/houmon/stats")
def api_houmon_stats():
    return get_houmon_stats()


@app.get("/api/houmon/offices")
def api_houmon_offices(category: Optional[str] = None, favorite_only: bool = False):
    return get_houmon_offices(category, favorite_only)


@app.patch("/api/houmon/offices/{office_id}/memo")
def api_houmon_update_memo(office_id: int, body: MemoRequest):
    update_houmon_memo(office_id, body.memo)
    return {"ok": True}


@app.patch("/api/houmon/offices/{office_id}/favorite")
def api_houmon_update_favorite(office_id: int, body: FavoriteRequest):
    update_houmon_favorite(office_id, body.is_favorite)
    return {"ok": True}


@app.post("/api/houmon/update")
async def api_houmon_update(force: bool = False):
    result = await run_houmon_update(force=force)
    return result


@app.get("/api/houmon/cities")
def api_houmon_cities():
    data = fetch_houmon_comparison()
    return data


@app.get("/api/houmon/update-log")
def api_houmon_update_log():
    return get_houmon_update_log(20)


# ── お問い合わせ API ─────────────────────────────────────


def _send_contact_email(name: str, contact: str, type_: str, message: str):
    """お問い合わせ内容を Resend API 経由で通知"""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.warning("RESEND_API_KEY が未設定のためメール通知をスキップ")
        return "no_key"
    to_email = os.environ.get("GMAIL_USER", "ayumi.godo@gmail.com")
    payload = _json.dumps({
        "from": "いっぽHP <onboarding@resend.dev>",
        "to": [to_email],
        "subject": f"【いっぽHP】{type_} - {name}様",
        "text": (
            f"【いっぽ HP お問い合わせ】\n\n"
            f"お名前: {name}\n"
            f"連絡先: {contact}\n"
            f"種類: {type_}\n"
            f"メッセージ:\n{message or '（なし）'}\n"
        ),
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = resp.read().decode()
        logger.info(f"お問い合わせ通知メール送信完了: {name}")
        return "sent"
    except Exception as e:
        logger.error(f"メール送信失敗: {e}")
        return str(e)


@app.post("/api/contact")
def api_contact(body: ContactRequest):
    save_contact(body.name, body.contact, body.type, body.message)
    threading.Thread(
        target=_send_contact_email,
        args=(body.name, body.contact, body.type, body.message),
        daemon=True,
    ).start()
    return {"ok": True}


@app.get("/api/contacts")
def api_contacts(request: Request):
    if not _is_local(request):
        raise HTTPException(status_code=403, detail="ローカル環境からのみアクセスできます")
    return get_contacts()


# ── ガントチャート同期 API ─────────────────────────────────


@app.get("/api/gantt")
def api_gantt_get():
    data = kv_get("gantt")
    if data:
        return JSONResponse(content={"data": data["value"], "updated_at": data["updated_at"]})
    return JSONResponse(content={"data": None, "updated_at": None})


class GanttSaveRequest(BaseModel):
    data: str


@app.put("/api/gantt")
def api_gantt_save(body: GanttSaveRequest):
    kv_set("gantt", body.data)
    return {"ok": True}


# ── フロントエンド ──────────────────────────────────────────


def _is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    host = request.headers.get("host", "")
    if "ippo-kango.jp" in host:
        with open("app/static/hp-ippo.html", encoding="utf-8") as f:
            return f.read()
    with open("app/static/strategy.html", encoding="utf-8") as f:
        return f.read()


@app.get("/hp-ippo", response_class=HTMLResponse)
async def hp_ippo():
    with open("app/static/hp-ippo.html", encoding="utf-8") as f:
        return f.read()


@app.get("/houmon-simulator", response_class=HTMLResponse)
async def houmon_simulator(request: Request):
    if not _is_local(request):
        raise HTTPException(status_code=403, detail="ローカル環境からのみアクセスできます")
    with open("app/static/houmon-simulator.html", encoding="utf-8") as f:
        return f.read()


@app.get("/itaku-tanka", response_class=HTMLResponse)
async def itaku_tanka(request: Request):
    if not _is_local(request):
        raise HTTPException(status_code=403, detail="ローカル環境からのみアクセスできます")
    with open("app/static/itaku-tanka.html", encoding="utf-8") as f:
        return f.read()


@app.get("/strategy", response_class=HTMLResponse)
async def strategy():
    with open("app/static/strategy.html", encoding="utf-8") as f:
        return f.read()


@app.get("/contract-kanai", response_class=HTMLResponse)
async def contract_kanai(request: Request):
    if not _is_local(request):
        raise HTTPException(status_code=403, detail="ローカル環境からのみアクセスできます")
    with open("app/static/contract-kanai.html", encoding="utf-8") as f:
        return f.read()


@app.get("/sougyou-plan", response_class=HTMLResponse)
async def sougyou_plan(request: Request):
    if not _is_local(request):
        raise HTTPException(status_code=403, detail="ローカル環境からのみアクセスできます")
    with open("app/static/sougyou-plan.html", encoding="utf-8") as f:
        return f.read()


@app.get("/tel-kouseikyoku", response_class=HTMLResponse)
async def tel_kouseikyoku(request: Request):
    if not _is_local(request):
        raise HTTPException(status_code=403, detail="ローカル環境からのみアクセスできます")
    with open("app/static/tel-kouseikyoku.html", encoding="utf-8") as f:
        return f.read()
