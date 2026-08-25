"""
精神科訪問看護ステーション 開業準備ダッシュボード
FastAPI アプリケーション
"""

import json as _json
import logging
import os
import threading
import urllib.request
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
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
    save_recruit,
    get_recruits,
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
        "from": "いっぽHP <noreply@ippo-kango.jp>",
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
            "User-Agent": "ippo-kango/1.0",
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


# ── 採用応募 API ─────────────────────────────────────

UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def _send_recruit_email(name: str, contact: str, qualification: str,
                        experience: str, employment_type: str, message: str,
                        file_name: str, file_path: str):
    """採用応募内容を Resend API 経由で通知"""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.warning("RESEND_API_KEY が未設定のためメール通知をスキップ")
        return "no_key"
    to_email = os.environ.get("GMAIL_USER", "ayumi.godo@gmail.com")
    email_data = {
        "from": "いっぽHP <noreply@ippo-kango.jp>",
        "to": [to_email],
        "subject": f"【いっぽ採用】応募 - {name}様",
        "text": (
            f"【いっぽ 採用応募】\n\n"
            f"お名前: {name}\n"
            f"連絡先: {contact}\n"
            f"保有資格: {qualification or '（未選択）'}\n"
            f"臨床経験: {experience or '（未選択）'}\n"
            f"希望雇用形態: {employment_type or '（未選択）'}\n"
            f"履歴書: {file_name or '（なし）'}\n\n"
            f"メッセージ:\n{message or '（なし）'}\n"
        ),
    }
    if file_path and file_name:
        try:
            import base64
            with open(file_path, "rb") as f:
                file_content = base64.b64encode(f.read()).decode("utf-8")
            email_data["attachments"] = [{"filename": file_name, "content": file_content}]
        except Exception as e:
            logger.error(f"添付ファイル読み込み失敗: {e}")
    payload = _json.dumps(email_data).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "ippo-kango/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        logger.info(f"採用応募通知メール送信完了: {name}")
        return "sent"
    except Exception as e:
        logger.error(f"採用メール送信失敗: {e}")
        return str(e)


@app.post("/api/recruit")
async def api_recruit(
    name: str = Form(...),
    contact: str = Form(...),
    qualification: str = Form(""),
    experience: str = Form(""),
    employment_type: str = Form(""),
    message: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    file_path_str = None
    file_original_name = None

    if file and file.filename:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail="PDF・Word形式のファイルのみアップロード可能です")

        contents = await file.read()
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="ファイルサイズは10MB以下にしてください")

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = f"{uuid.uuid4().hex}{ext}"
        save_path = UPLOAD_DIR / safe_name
        save_path.write_bytes(contents)

        file_path_str = str(save_path)
        file_original_name = file.filename

    save_recruit(name, contact, qualification, experience, employment_type,
                 message, file_path_str, file_original_name)

    threading.Thread(
        target=_send_recruit_email,
        args=(name, contact, qualification, experience, employment_type,
              message, file_original_name or "", file_path_str or ""),
        daemon=True,
    ).start()

    return {"ok": True}


@app.get("/api/recruits")
def api_recruits(request: Request):
    if not _is_local(request):
        raise HTTPException(status_code=403, detail="ローカル環境からのみアクセスできます")
    return get_recruits()


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


MAINTENANCE_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>訪問看護ステーション いっぽ</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><circle cx='50' cy='50' r='45' fill='%233a9d6e'/><text x='50' y='62' text-anchor='middle' font-size='40' fill='white' font-family='serif'>歩</text></svg>">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#e8f5e9,#f1f8e9);font-family:'Helvetica Neue',Arial,'Hiragino Kaku Gothic ProN',sans-serif;color:#2d5a3d}
.wrap{text-align:center;padding:2rem}
.logo{font-size:3rem;font-weight:700;color:#3a9d6e;margin-bottom:.5rem;letter-spacing:.1em}
.sub{font-size:1.1rem;color:#5a8a6a;margin-bottom:2.5rem}
.msg{font-size:1.4rem;font-weight:600;margin-bottom:1rem}
.detail{font-size:.95rem;color:#6a9a7a;line-height:1.8}
</style>
</head>
<body>
<div class="wrap">
<div class="logo">いっぽ</div>
<div class="sub">訪問看護ステーション</div>
<div class="msg">ホームページ準備中です</div>
<div class="detail">現在、サイトをリニューアル中です。<br>もうしばらくお待ちください。</div>
</div>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    host = request.headers.get("host", "")
    if "ippo-kango.jp" in host:
        with open("app/static/hp-ippo.html", encoding="utf-8") as f:
            return f.read()
    with open("app/static/strategy.html", encoding="utf-8") as f:
        return f.read()


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return """User-agent: *
Allow: /
Disallow: /strategy
Disallow: /houmon-simulator
Disallow: /contract-kanai
Disallow: /sougyou-plan
Disallow: /tel-kouseikyoku
Disallow: /itaku-tanka
Disallow: /api/

Sitemap: https://ippo-kango.jp/sitemap.xml
"""


@app.get("/sitemap.xml")
async def sitemap_xml():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://ippo-kango.jp/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://ippo-kango.jp/recruit</loc>
    <changefreq>weekly</changefreq>
    <priority>0.9</priority>
  </url>
</urlset>"""
    return Response(content=xml, media_type="application/xml")


@app.get("/googlefad0947a8a10a546.html", response_class=HTMLResponse)
async def google_verify():
    return "google-site-verification: googlefad0947a8a10a546.html"


@app.get("/recruit", response_class=HTMLResponse)
async def recruit():
    with open("app/static/recruit.html", encoding="utf-8") as f:
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
