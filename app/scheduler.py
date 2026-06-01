"""
APScheduler による自動更新スケジューラー
WAM NETは3ヶ月ごとに更新されるので、毎月チェックして新データがあれば取得する
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import get_stats, log_update, upsert_offices
from app.fetcher import fetch_ichikawa_data

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Tokyo")


async def run_update(force: bool = False):
    """データ更新を実行する"""
    logger.info("データ更新開始...")
    try:
        # ZIPダウンロード前にスキップ判定（不要なダウンロードを回避）
        if not force:
            from app.fetcher import _quarter_candidates, _download_zip, FILE_CODES
            # 最新の利用可能な年月を先に確認
            for ym in _quarter_candidates():
                test = _download_zip(ym, list(FILE_CODES.values())[0])
                if test is not None:
                    stats = get_stats()
                    if stats.get("fetched_ym") == ym:
                        logger.info(f"既に最新データです: {ym}")
                        return {"status": "skip", "fetched_ym": ym}
                    break

        df, fetched_ym = fetch_ichikawa_data()

        if df.empty:
            log_update("", 0, 0, "no_data", "データが取得できませんでした")
            logger.warning("データが空でした")
            return {"status": "no_data", "message": "データが取得できませんでした"}

        a_count = len(df[df["service_type"] == "A型"])
        b_count = len(df[df["service_type"] == "B型"])

        upsert_offices(df, fetched_ym)
        log_update(fetched_ym, a_count, b_count, "success")

        logger.info(f"更新完了: {fetched_ym} A型={a_count} B型={b_count}")
        return {
            "status": "success",
            "fetched_ym": fetched_ym,
            "a_count": a_count,
            "b_count": b_count,
        }

    except Exception as e:
        logger.error(f"更新エラー: {e}", exc_info=True)
        log_update("", 0, 0, "error", str(e))
        return {"status": "error", "message": str(e)}


def start_scheduler():
    """スケジューラーを起動する（毎月1日 午前3時にチェック）"""
    scheduler.add_job(
        run_update,
        trigger=CronTrigger(day=1, hour=3, minute=0, timezone="Asia/Tokyo"),
        id="monthly_update",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("スケジューラー起動: 毎月1日 03:00 に自動更新")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
