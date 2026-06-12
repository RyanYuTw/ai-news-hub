"""排程器：定時蒐集 + 到時自動發布（需求 1.8）。"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from .config import COLLECT_INTERVAL_MINUTES, PUBLISH_CHECK_SECONDS, TRANSLATE_INTERVAL_MINUTES
from .db import ArticleMedia, PublishJob, Session

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


def _cleanup_article_images(session, article_id: int) -> None:
    """若文章已無待執行的發布排程，刪除所有 variant 圖片檔及 DB 記錄。"""
    pending = (
        session.query(PublishJob)
        .filter(
            PublishJob.article_id == article_id,
            PublishJob.status.in_(["pending", "processing"]),
        )
        .count()
    )
    if pending:
        return
    images = (
        session.query(ArticleMedia)
        .filter(
            ArticleMedia.article_id == article_id,
            ArticleMedia.media_type == "image",
            ArticleMedia.variant.isnot(None),
        )
        .all()
    )
    removed = 0
    for img in images:
        if img.local_path:
            Path(img.local_path).unlink(missing_ok=True)
            removed += 1
        session.delete(img)
    if images:
        session.commit()
        log.info("文章 #%s 發布完畢，已刪除 %d 張 variant 圖片", article_id, removed)


def run_due_publish_jobs() -> None:
    """掃描到期的 pending 排程並發布。"""
    from .publishers import PublishError, publish

    now = dt.datetime.now()
    with Session() as session:
        jobs = (
            session.query(PublishJob)
            .filter(PublishJob.status == "pending", PublishJob.scheduled_at <= now)
            .all()
        )
        for job in jobs:
            job.status = "processing"
            session.commit()
            try:
                posted_url = publish(job.article, job.platform)
                job.status = "done"
                job.posted_url = posted_url
                job.result_message = "發布成功"
                log.info("已發布 job #%s -> %s", job.id, posted_url)
            except PublishError as exc:
                job.status = "failed"
                job.result_message = str(exc)[:2000]
                log.error("發布失敗 job #%s：%s", job.id, exc)
            except Exception as exc:  # noqa: BLE001
                job.status = "failed"
                job.result_message = f"未預期錯誤：{exc}"[:2000]
                log.exception("發布異常 job #%s", job.id)
            job.executed_at = dt.datetime.now()
            session.commit()
            _cleanup_article_images(session, job.article_id)


PDF_RETENTION_DAYS = 30


def cleanup_old_pdfs() -> None:
    """刪除 30 天以上的 PDF 檔案及對應的 article_media 記錄。"""
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
            if record.local_path:
                path = Path(record.local_path)
                if path.exists():
                    path.unlink()
                    removed_files += 1
            session.delete(record)
            removed_records += 1
        session.commit()
    log.info("PDF 清理完成：刪除 %d 筆記錄、%d 個檔案", removed_records, removed_files)


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Taipei")
    scheduler.add_job(
        run_collect,
        "interval",
        minutes=COLLECT_INTERVAL_MINUTES,
        id="collect",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_translate_one,
        "interval",
        minutes=TRANSLATE_INTERVAL_MINUTES,
        id="translate",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_due_publish_jobs,
        "interval",
        seconds=PUBLISH_CHECK_SECONDS,
        id="publish",
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
        "排程器啟動：每 %s 分鐘蒐集、每 %s 分鐘翻譯一篇、每 %s 秒檢查發布",
        COLLECT_INTERVAL_MINUTES, TRANSLATE_INTERVAL_MINUTES, PUBLISH_CHECK_SECONDS,
    )
    return scheduler
