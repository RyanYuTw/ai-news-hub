"""Web 管理介面（Flask）。
"""
from __future__ import annotations

import logging
import threading

import markdown as md
import nh3
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, url_for
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from .config import MEDIA_DIR
from .db import Article, Session, SourceSite

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
    import os, secrets as _secrets
    _fsk = os.environ.get("FLASK_SECRET_KEY")
    if not _fsk:
        log.warning("FLASK_SECRET_KEY 未設定，session 將於每次重啟後失效；請在 ~/.ai_news_hub/credentials 填入固定金鑰")
        _fsk = _secrets.token_hex(32)
    app.secret_key = _fsk

    _ALLOWED_TAGS = {
        "p","b","i","strong","em","ul","ol","li","code","pre",
        "h1","h2","h3","h4","blockquote","a","br","hr","table",
        "thead","tbody","tr","th","td","del","s",
    }
    _ALLOWED_ATTRS: dict[str, set[str]] = {"a": {"href", "title"}}

    @app.template_filter("mdrender")
    def mdrender(text: str | None) -> str:
        raw = md.markdown(text or "", extensions=["extra", "nl2br"])
        return nh3.clean(raw, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS)

    @app.template_filter("safe_url")
    def safe_url(url: str | None) -> str:
        from urllib.parse import urlparse
        if not url:
            return "#"
        try:
            scheme = urlparse(url).scheme.lower()
        except Exception:
            return "#"
        return url if scheme in ("http", "https") else "#"

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
                going_online = (
                    new_status == "online" and article.status != "online"
                    and new_status in STATUS_LABELS
                )
                if new_status in STATUS_LABELS:
                    article.status = new_status
                if going_online:
                    from . import gdrive
                    drive_ids = [
                        m.gdrive_file_id for m in article.media
                        if m.media_type == "image" and m.gdrive_file_id
                    ]
                    for m in article.media:
                        if m.media_type == "image" and m.gdrive_file_id:
                            m.gdrive_file_id = None
                    session.commit()
                    for fid in drive_ids:
                        gdrive.delete_file(fid)
                else:
                    session.commit()
                flash("已儲存")
                return redirect(url_for("article_detail", article_id=article_id))
            _ = article.media  # 預先載入關聯
        return render_template("edit.html", a=article)

    @app.route("/article/<int:article_id>/delete", methods=["POST"])
    def article_delete(article_id: int):
        from . import gdrive
        from .db import ArticleMedia
        with Session() as session:
            article = session.get(Article, article_id)
            if article:
                drive_ids = [
                    m.gdrive_file_id for m in article.media if m.gdrive_file_id
                ]
                session.delete(article)
                session.commit()
                for fid in drive_ids:
                    gdrive.delete_file(fid)
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

    # --------------------------------------------------------- 媒體檔案服務
    @app.route("/media/<path:filename>")
    def media_file(filename: str):
        return send_from_directory(MEDIA_DIR, filename)

    @app.route("/media/<int:media_id>/delete", methods=["POST"])
    def media_delete(media_id: int):
        from pathlib import Path
        from . import gdrive
        from .db import ArticleMedia
        with Session() as session:
            m = session.get(ArticleMedia, media_id)
            if not m:
                abort(404)
            article_id = m.article_id
            if m.gdrive_file_id:
                gdrive.delete_file(m.gdrive_file_id)
            elif m.local_path:
                Path(m.local_path).unlink(missing_ok=True)
            session.delete(m)
            session.commit()
            flash("圖片已刪除")
        return redirect(url_for("article_detail", article_id=article_id))

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
