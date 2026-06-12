"""資料蒐集模組。

需求對應：
- 1.1 來源有 RSS 用 RSS（feedparser），無 RSS 解析網頁（BeautifulSoup），arXiv 用官方 API
- 1.2 嘗試三次抓全文，失敗則以摘要記錄（is_fulltext=0）
- 1.4 PDF 轉為 md/txt 寫入資料庫；以 url_hash 去重；attribution 標註資料來源
- 1.7 圖片/影音存入 article_media 並標註來源
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import logging
import time

import re

import feedparser
import requests
from bs4 import BeautifulSoup

from .config import FULLTEXT_MAX_ATTEMPTS, MEDIA_DIR
from .db import Article, ArticleMedia, Session, SourceSite

log = logging.getLogger("collector")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 AINewsHub/1.0"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en,zh-TW;q=0.8"}
TIMEOUT = 30


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    return resp


def pdf_to_markdown(data: bytes) -> str:
    """PDF 轉為 Markdown 純文字（需求 1.4）。"""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def html_to_markdownish(soup: BeautifulSoup) -> str:
    """從文章頁面抽取主要內容並轉為近似 Markdown 的純文字。"""
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    main = (
        soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find("main")
        or soup.body
        or soup
    )
    parts: list[str] = []
    for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            parts.append("#" * int(el.name[1]) + " " + text)
        elif el.name == "li":
            parts.append("- " + text)
        elif el.name == "blockquote":
            parts.append("> " + text)
        else:
            parts.append(text)
    return "\n\n".join(parts)


def _ojs_download_url(url: str) -> str | None:
    """OJS viewer URL → 實際 PDF 下載 URL。
    /article/view/{article}/{galley} → /article/download/{article}/{galley}
    """
    m = re.search(r"(/article)/view(/\d+/\d+)", url)
    if m:
        return url[: m.start()] + "/article/download" + m.group(2)
    return None


def _pdf_url_from_script(soup: BeautifulSoup, base_url: str) -> str | None:
    """從 PDF.js viewer 的 <script> 中萃取原始 PDF URL。"""
    for script in soup.find_all("script"):
        text = script.string or ""
        # 常見模式：DEFAULT_URL = "...", file: "...", pdfUrl: "..."
        m = re.search(r"""(?:DEFAULT_URL|['"]\s*file['"]|pdfUrl)\s*[=:]\s*['"]([^'"]+\.pdf[^'"]*)['"]""", text)
        if m:
            return requests.compat.urljoin(base_url, m.group(1))
    return None


def extract_pdf_link(soup: BeautifulSoup, base_url: str) -> str | None:
    """從頁面中找 PDF 下載連結（支援 Springer、JAIR/OJS 等學術出版商）。"""
    # Springer: <a data-article-pdf="true" href="/content/pdf/...">
    tag = soup.find("a", attrs={"data-article-pdf": "true"})
    if tag and tag.get("href"):
        return requests.compat.urljoin(base_url, tag["href"])
    # 通用：class 含 pdf、連結文字恰為 "pdf"，或 href 以 .pdf 結尾
    # 涵蓋：JAIR (class=galley-link…pdf)、arXiv (<a>pdf</a>)、Elsevier 等
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        cls = " ".join(a.get("class") or []).lower()
        text = a.get_text(strip=True).lower()
        if "pdf" in cls or text == "pdf" or (href.lower().endswith(".pdf") and "pdf" in text):
            full_href = requests.compat.urljoin(base_url, href)
            return _ojs_download_url(full_href) or full_href
    # PDF.js viewer 頁面：嘗試 OJS URL 轉換或從 <script> 萃取
    if soup.find(id="outerContainer") or soup.find(id="viewerContainer"):
        return _ojs_download_url(base_url) or _pdf_url_from_script(soup, base_url)
    return None


def _download_to_viewer_url(url: str) -> str:
    """將 OJS /article/download/{a}/{g} 轉回 /article/view/{a}/{g}（viewer 頁面）。"""
    m = re.search(r"(/article)/download(/\d+/\d+)", url)
    if m:
        return url[: m.start()] + "/article/view" + m.group(2)
    return url


def _save_pdf(data: bytes, pdf_url: str, article_url: str) -> dict:
    """將 PDF bytes 寫入 media_files，回傳 media dict。url 存 viewer 頁面網址。"""
    filename = url_hash(pdf_url) + ".pdf"
    path = MEDIA_DIR / filename
    path.write_bytes(data)
    return {
        "media_type": "pdf",
        "url": _download_to_viewer_url(pdf_url),
        "local_path": str(path),
        "attribution": f"PDF 來源：{article_url}",
    }


AUTHOR_SELECTORS = [
    '[data-test="author-name"]',   # Springer
    '[itemprop="author"]',         # schema.org
    ".author-name",
    ".c-author-list__item",
    ".contrib-author",
]


def extract_authors_from_soup(soup: BeautifulSoup) -> str | None:
    """從 HTML 頁面萃取作者名單（補充 RSS 未提供作者的情況）。"""
    for sel in AUTHOR_SELECTORS:
        els = soup.select(sel)
        if els:
            names = [e.get_text(strip=True) for e in els if e.get_text(strip=True)]
            if names:
                return ", ".join(names[:20])
    return None


def fetch_fulltext(url: str) -> tuple[str | None, int, list[dict], str | None]:
    """嘗試抓取全文，最多 FULLTEXT_MAX_ATTEMPTS 次。

    回傳 (全文或 None, 實際嘗試次數, 媒體清單, 作者或 None)。
    """
    attempts = 0
    last_err: Exception | None = None
    for attempts in range(1, FULLTEXT_MAX_ATTEMPTS + 1):
        try:
            resp = _get(url)
            ctype = resp.headers.get("Content-Type", "").lower()
            if "pdf" in ctype or url.lower().endswith(".pdf"):
                pdf_media = _save_pdf(resp.content, url, url)
                text = pdf_to_markdown(resp.content)
                return (text or None), attempts, [pdf_media], None
            soup = BeautifulSoup(resp.text, "lxml")
            media = extract_media(soup, url)
            authors = extract_authors_from_soup(soup)
            # 優先嘗試頁面內的 PDF 下載連結
            pdf_url = extract_pdf_link(soup, url)
            if pdf_url:
                log.debug("偵測到 PDF 連結，優先抓取：%s", pdf_url)
                try:
                    pdf_resp = _get(pdf_url)
                    pdf_text = pdf_to_markdown(pdf_resp.content)
                    if pdf_text and len(pdf_text) > 400:
                        media.append(_save_pdf(pdf_resp.content, pdf_url, url))
                        return pdf_text, attempts, media, authors
                except Exception as pdf_exc:  # noqa: BLE001
                    log.debug("PDF 下載失敗，退回 HTML：%s", pdf_exc)
            # 退回 HTML 解析
            text = html_to_markdownish(soup)
            if text and len(text) > 400:
                return text, attempts, media, authors
            last_err = ValueError(f"內容過短（{len(text or '')} 字元）")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(2 * attempts)
    log.warning("全文抓取失敗（%s 次）：%s — %s", attempts, url, last_err)
    return None, attempts, [], None


def extract_media(soup: BeautifulSoup, page_url: str) -> list[dict]:
    """抽取頁面代表性圖片/影音並附帶來源標註（需求 1.7）。"""
    media: list[dict] = []
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        media.append(
            {
                "media_type": "image",
                "url": og_image["content"],
                "attribution": f"圖片來源：{page_url}",
            }
        )
    og_video = soup.find("meta", property="og:video")
    if og_video and og_video.get("content"):
        media.append(
            {
                "media_type": "video",
                "url": og_video["content"],
                "attribution": f"影片來源：{page_url}",
            }
        )
    return media


def _parse_time(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return dt.datetime(*value[:6])
    return None


def _save_entry(
    session,
    source: SourceSite,
    *,
    title: str,
    link: str,
    summary: str | None,
    authors: str | None,
    published_at: dt.datetime | None,
    doi: str | None = None,
    fetch_full: bool = True,
) -> Article | None:
    """寫入單篇文章；以 url_hash 去重（需求 1.4 資料不得重複）。"""
    h = url_hash(link)
    if session.query(Article.id).filter_by(url_hash=h).first():
        return None

    fulltext, attempts, media, page_authors = (None, 0, [], None)
    if fetch_full:
        fulltext, attempts, media, page_authors = fetch_fulltext(link)

    content = fulltext or summary
    if not content:
        return None

    article = Article(
        source_id=source.id,
        title=title[:1000],
        url=link[:1000],
        url_hash=h,
        doi=doi,
        authors=(authors or page_authors or "")[:2000] or None,
        published_at=published_at,
        content_original=content,
        is_fulltext=bool(fulltext),
        fetch_attempts=attempts,
        content_format="md",
        attribution=f"資料來源：{source.name} — {link}",
        status="draft",
    )
    session.add(article)
    session.flush()
    for m in media:
        session.add(ArticleMedia(article_id=article.id, **m))
    return article


def collect_rss(session, source: SourceSite, limit: int = 30) -> int:
    feed = feedparser.parse(source.url, request_headers=HEADERS)
    # limit=1 時：掃前 10 篇，找第一篇有 PDF 的直接儲存；沒有則退回第一篇
    scan_size = 10 if limit == 1 else limit
    count = 0
    fallback_entry: dict | None = None
    for entry in feed.entries[:scan_size]:
        link = entry.get("link")
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        summary_html = entry.get("summary") or ""
        summary = BeautifulSoup(summary_html, "lxml").get_text(" ", strip=True) or None
        authors = ", ".join(a.get("name", "") for a in entry.get("authors", []) if a.get("name"))
        base = dict(title=title, link=link, summary=summary,
                    authors=authors or None, published_at=_parse_time(entry))
        if limit == 1:
            # 嘗試抓全文：有 PDF 則直接採用，無 PDF 存為候補
            h = url_hash(link)
            if session.query(Article.id).filter_by(url_hash=h).first():
                continue
            fulltext, attempts, media, page_authors = fetch_fulltext(link)
            has_pdf = any(m["media_type"] == "pdf" for m in media)
            if has_pdf:
                saved = _save_entry(session, source, fetch_full=False, **base)
                if saved:
                    saved.content_original = fulltext or summary
                    saved.is_fulltext = bool(fulltext)
                    saved.fetch_attempts = attempts
                    if not saved.authors and page_authors:
                        saved.authors = page_authors[:2000]
                    for m in media:
                        session.add(ArticleMedia(article_id=saved.id, **m))
                    session.flush()
                    return 1
            elif fallback_entry is None:
                fallback_entry = {**base, "_fulltext": fulltext, "_attempts": attempts, "_media": media, "_authors": page_authors}
        else:
            saved = _save_entry(session, source, **base)
            if saved:
                count += 1
    # limit=1 且無 PDF，退回第一篇
    if limit == 1 and fallback_entry:
        fb = {k: v for k, v in fallback_entry.items() if not k.startswith("_")}
        saved = _save_entry(session, source, fetch_full=False, **fb)
        if saved:
            saved.content_original = fallback_entry["_fulltext"] or fb.get("summary")
            saved.is_fulltext = bool(fallback_entry["_fulltext"])
            saved.fetch_attempts = fallback_entry["_attempts"]
            if not saved.authors and fallback_entry.get("_authors"):
                saved.authors = fallback_entry["_authors"][:2000]
            for m in fallback_entry["_media"]:
                session.add(ArticleMedia(article_id=saved.id, **m))
            session.flush()
            return 1
    return count


def collect_arxiv(session, source: SourceSite, limit: int = 30) -> int:
    """arXiv 官方 Atom API：摘要即為論文 abstract；全文改抓 PDF 轉 md。"""
    feed = feedparser.parse(source.url, request_headers=HEADERS)
    count = 0
    for entry in feed.entries[:limit]:
        link = entry.get("link")
        title = " ".join((entry.get("title") or "").split())
        if not link or not title:
            continue
        abstract = " ".join((entry.get("summary") or "").split()) or None
        authors = ", ".join(a.get("name", "") for a in entry.get("authors", []))
        doi = entry.get("arxiv_doi")
        pdf_url = next(
            (l.get("href") for l in entry.get("links", []) if l.get("type") == "application/pdf"),
            None,
        )

        h = url_hash(link)
        if session.query(Article.id).filter_by(url_hash=h).first():
            continue

        fulltext, attempts, pdf_media = None, 0, []
        if pdf_url:
            fulltext, attempts, pdf_media, _ = fetch_fulltext(pdf_url)

        article = Article(
            source_id=source.id,
            title=title[:1000],
            url=link[:1000],
            url_hash=h,
            doi=doi,
            authors=authors[:2000] or None,
            published_at=_parse_time(entry),
            content_original=fulltext or abstract,
            is_fulltext=bool(fulltext),
            fetch_attempts=attempts,
            content_format="md",
            attribution=f"資料來源：arXiv — <{link}>（CC 授權依論文頁面標示）",
            status="draft",
        )
        if not article.content_original:
            continue
        session.add(article)
        session.flush()
        for m in pdf_media:
            session.add(ArticleMedia(article_id=article.id, **m))
        count += 1
    return count


def collect_html(session, source: SourceSite, limit: int = 20) -> int:
    """無 RSS 的網站：解析列表頁找文章連結，再逐篇抓取（需求 1.1）。"""
    resp = _get(source.url)
    soup = BeautifulSoup(resp.text, "lxml")
    base = source.url
    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = requests.compat.urljoin(base, a["href"])
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 25 or href in seen:
            continue
        if any(k in href for k in ("/article", "/articles/", "/abs/", "/doi/", "/view/")):
            seen.add(href)
            links.append((title, href))
    count = 0
    for title, href in links[:limit]:
        saved = _save_entry(
            session, source,
            title=title, link=href, summary=None,
            authors=None, published_at=None,
        )
        if saved:
            count += 1
    return count


COLLECTOR_BY_TYPE = {
    "rss": collect_rss,
    "arxiv_api": collect_arxiv,
    "html": collect_html,
}


def collect_all(limit_per_source: int = 30) -> dict[str, int]:
    """蒐集所有啟用來源，回傳 {來源名稱: 新增篇數}。"""
    results: dict[str, int] = {}
    with Session() as session:
        sources = session.query(SourceSite).filter_by(enabled=True).all()
        for source in sources:
            handler = COLLECTOR_BY_TYPE.get(source.type)
            if not handler:
                continue
            try:
                added = handler(session, source, limit_per_source)
                source.last_fetched_at = dt.datetime.now()
                session.commit()
                results[source.name] = added
                log.info("%s：新增 %s 篇", source.name, added)
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                results[source.name] = -1
                log.exception("蒐集失敗 %s：%s", source.name, exc)
    return results
