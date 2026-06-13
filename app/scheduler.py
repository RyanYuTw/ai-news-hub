"""排程器：定時蒐集 + 到時自動發布"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import COLLECT_CRON, TRANSLATE_CRON
from app.db import ArticleMedia, Session

log = logging.getLogger("scheduler")


def run_collect() -> None:
    from .collectors import collect_all

    results = collect_all()
    log.info("蒐集完成：%s", results)


def run_translate_one() -> None:
    """每次翻譯一篇最舊的待翻譯文章。"""
    from .translator import translate_pending

    try:
        translated = translate_pending(limit=1)
        if translated:
            log.info("翻譯完成 1 篇")
    except Exception as exc:  # noqa: BLE001 — 無 API key 時僅記錄
        log.warning("翻譯略過：%s", exc)



PDF_RETENTION_DAYS = 30


def cleanup_old_pdfs() -> None:
    """刪除 30 天以上的 PDF 檔案（本機 + Drive）及對應的 article_media 記錄。"""
    from . import gdrive

    cutoff = dt.datetime.now() - dt.timedelta(days=PDF_RETENTION_DAYS)
    with Session() as session:
        old_records = (
            session.query(ArticleMedia)
            .filter(ArticleMedia.media_type == "pdf", ArticleMedia.created_at < cutoff)
            .all()
        )
        removed_files = 0
        removed_records = 0
        for record in old_records:
            if record.local_path and not record.local_path.startswith("http"):
                path = Path(record.local_path)
                if path.exists():
                    path.unlink()
                    removed_files += 1
            if record.gdrive_file_id:
                gdrive.delete_file(record.gdrive_file_id)
                removed_files += 1
            session.delete(record)
            removed_records += 1
        session.commit()
    log.info("PDF 清理完成：刪除 %d 筆記錄、%d 個檔案", removed_records, removed_files)


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Taipei")
    scheduler.add_job(
        run_collect,
        CronTrigger.from_crontab(COLLECT_CRON),
        id="collect",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_translate_one,
        CronTrigger.from_crontab(TRANSLATE_CRON),
        id="translate",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        cleanup_old_pdfs,
        "cron",
        hour=3,
        minute=0,
        id="pdf_cleanup",
        max_instances=1,
    )
    scheduler.start()
    log.info(
        "排程器啟動：蒐集排程 (%s)、翻譯排程 (%s)",
        COLLECT_CRON, TRANSLATE_CRON,
    )
    return scheduler
