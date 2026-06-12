"""Web 管理介面（Flask）。

需求 1.8：分頁列表可查詢／編輯／刪除；點入文章可編輯、預覽、
設定上下架狀態、選擇發布平台與發布時間，到時由排程器自動發布。
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

import markdown as md
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, url_for
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from .config import MEDIA_DIR
from .db import Article, Platform, PublishJob, Session, SourceSite

log = logging.getLogger("web")

PER_PAGE = 20
STATUS_LABELS = {
    "draft": "待翻譯",
    "translated": "已翻譯",
    "online": "上架",
    "offline": "下架",
}


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "ai-news-hub-local-admin"  # 僅本機後台 flash 訊息用，非對外服務

    @app.template_filter("mdrender")
    def mdrender(text: str | None) -> str:
        return md.markdown(text or "", extensions=["extra", "nl2br"])

    @app.context_processor
    def inject_globals():
        return {"STATUS_LABELS": STATUS_LABELS}

    # ------------------------------------------------------------- 文章列表
    @app.route("/")
    def index():
        page = max(int(request.args.get("page", 1)), 1)
        q = request.args.get("q", "").strip()
        status = request.args.get("status", "").strip()
        with Session() as session:
            query = session.query(Article).options(joinedload(Article.source))
            if q:
                like = f"%{q}%"
                query = query.filter(
                    or_(Article.title.like(like), Article.title_zh.like(like))
                )
            if status in STATUS_LABELS:
                query = query.filter(Article.status == status)
            total = query.with_entities(func.count(Article.id)).scalar() or 0
            articles = (
                query.order_by(Article.created_at.desc())
                .offset((page - 1) * PER_PAGE)
                .limit(PER_PAGE)
                .all()
            )
        pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
        return render_template(
            "index.html",
            articles=articles, page=page, pages=pages, total=total,
            q=q, status=status,
        )

    # --------------------------------------------------------- 編輯／預覽
    @app.route("/article/<int:article_id>", methods=["GET", "POST"])
    def article_detail(article_id: int):
        with Session() as session:
            article = (
                session.query(Article)
                .options(joinedload(Article.source), joinedload(Article.media))
                .filter_by(id=article_id)
                .first()
            )
            if not article:
                abort(404)
            if request.method == "POST":
                article.title_zh = request.form.get("title_zh", "").strip() or None
                article.content_zh = request.form.get("content_zh", "").strip() or None
                article.content_research = request.form.get("content_research", "").strip() or None
                new_status = request.form.get("status", article.status)
                if new_status in STATUS_LABELS:
                    article.status = new_status
                session.commit()
                flash("已儲存")
                return redirect(url_for("article_detail", article_id=article_id))
            platforms = session.query(Platform).filter_by(enabled=True).all()
            jobs = (
                session.query(PublishJob)
                .filter_by(article_id=article_id)
                .order_by(PublishJob.scheduled_at.desc())
                .all()
            )
            _ = article.media, [j.platform for j in jobs]  # 預先載入關聯
        return render_template("edit.html", a=article, platforms=platforms, jobs=jobs)

    @app.route("/article/<int:article_id>/delete", methods=["POST"])
    def article_delete(article_id: int):
        with Session() as session:
            article = session.get(Article, article_id)
            if article:
                session.delete(article)
                session.commit()
                flash("文章已刪除")
        return redirect(url_for("index"))

    @app.route("/article/<int:article_id>/translate", methods=["POST"])
    def article_translate(article_id: int):
        from .translator import translate_article

        def worker():
            translate_article(article_id)

        threading.Thread(target=worker, daemon=True).start()
        flash("翻譯已在背景執行，稍後重新整理查看結果")
        return redirect(url_for("article_detail", article_id=article_id))

    # ------------------------------------------------------------- 發布排程
    @app.route("/article/<int:article_id>/schedule", methods=["POST"])
    def article_schedule(article_id: int):
        platform_ids = request.form.getlist("platform_ids")
        when_raw = request.form.get("scheduled_at", "").strip()
        if not platform_ids or not when_raw:
            flash("請選擇平台與發布時間")
            return redirect(url_for("article_detail", article_id=article_id))
        scheduled_at = dt.datetime.fromisoformat(when_raw)
        with Session() as session:
            article = session.get(Article, article_id)
            if not article:
                abort(404)
            if article.status != "online":
                flash("請先將文章設為「上架」再排程發布")
                return redirect(url_for("article_detail", article_id=article_id))
            for pid in platform_ids:
                session.add(
                    PublishJob(
                        article_id=article_id,
                        platform_id=int(pid),
                        scheduled_at=scheduled_at,
                    )
                )
            session.commit()
        flash(f"已排程 {len(platform_ids)} 個平台於 {scheduled_at:%Y-%m-%d %H:%M} 發布")
        return redirect(url_for("article_detail", article_id=article_id))

    @app.route("/job/<int:job_id>/cancel", methods=["POST"])
    def job_cancel(job_id: int):
        with Session() as session:
            job = session.get(PublishJob, job_id)
            if job and job.status == "pending":
                job.status = "canceled"
                session.commit()
                flash("排程已取消")
            article_id = job.article_id if job else None
        if article_id:
            return redirect(url_for("article_detail", article_id=article_id))
        return redirect(url_for("index"))

    # ------------------------------------------------------------- 來源管理
    @app.route("/sources", methods=["GET", "POST"])
    def sources():
        with Session() as session:
            if request.method == "POST":
                name = request.form.get("name", "").strip()
                url = request.form.get("url", "").strip()
                type_ = request.form.get("type", "rss")
                if name and url and type_ in ("rss", "html", "arxiv_api"):
                    session.add(
                        SourceSite(name=name, url=url, type=type_, is_default=False)
                    )
                    session.commit()
                    flash("已新增自訂來源")
                return redirect(url_for("sources"))
            items = session.query(SourceSite).order_by(SourceSite.id).all()
        return render_template("sources.html", items=items)

    @app.route("/sources/<int:source_id>/toggle", methods=["POST"])
    def source_toggle(source_id: int):
        with Session() as session:
            s = session.get(SourceSite, source_id)
            if s:
                s.enabled = not s.enabled
                session.commit()
        return redirect(url_for("sources"))

    @app.route("/sources/<int:source_id>/delete", methods=["POST"])
    def source_delete(source_id: int):
        with Session() as session:
            s = session.get(SourceSite, source_id)
            if s and not s.is_default:
                session.delete(s)
                session.commit()
                flash("已刪除來源")
            elif s:
                flash("預設來源不可刪除，可改為停用")
        return redirect(url_for("sources"))

    # ------------------------------------------------------------- 平台管理
    @app.route("/platforms", methods=["GET", "POST"])
    def platforms():
        with Session() as session:
            if request.method == "POST":
                name = request.form.get("name", "").strip()
                cred_key = request.form.get("credential_key", "").strip().upper()
                webhook = request.form.get("webhook_url", "").strip()
                if name and cred_key:
                    session.add(
                        Platform(
                            name=name, type="custom", credential_key=cred_key,
                            enabled=True,
                            config={"webhook_url": webhook} if webhook else None,
                        )
                    )
                    session.commit()
                    flash("已新增自訂平台")
                return redirect(url_for("platforms"))
            items = session.query(Platform).order_by(Platform.id).all()
        return render_template("platforms.html", items=items)

    @app.route("/platforms/<int:platform_id>/toggle", methods=["POST"])
    def platform_toggle(platform_id: int):
        with Session() as session:
            p = session.get(Platform, platform_id)
            if p:
                p.enabled = not p.enabled
                session.commit()
        return redirect(url_for("platforms"))

    # --------------------------------------------------------- 媒體檔案服務
    @app.route("/media/<path:filename>")
    def media_file(filename: str):
        return send_from_directory(MEDIA_DIR, filename)

    # -------------------------------------------------- 手動觸發圖片生成
    @app.route("/article/<int:article_id>/generate_images", methods=["POST"])
    def article_generate_images(article_id: int):
        from .image_processor import generate_article_images

        def worker():
            generate_article_images(article_id)

        threading.Thread(target=worker, daemon=True).start()
        flash("圖片生成已在背景執行，稍後重新整理查看結果")
        return redirect(url_for("article_detail", article_id=article_id))

    # ------------------------------------------------------------- 手動觸發
    @app.route("/collect", methods=["POST"])
    def collect_now():
        from .scheduler import run_collect_and_translate

        threading.Thread(target=run_collect_and_translate, daemon=True).start()
        flash("蒐集與翻譯已在背景執行")
        return redirect(url_for("index"))

    return app
