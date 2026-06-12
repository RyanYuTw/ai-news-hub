"""翻譯模組：以 NotebookLM 將英文學術內容整理並轉譯為繁體中文。

翻譯流程僅使用 NotebookLM，其餘本機 Ollama 與 Claude 後端均已停用。
使用時需預先設定好 NOTEBOOKLM_NOTEBOOK_ID。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess

from .config import (
    NOTEBOOKLM_NOTEBOOK_ID,
)
from .db import Article, ArticleMedia, Session

log = logging.getLogger("translator")

# NotebookLM 翻譯用 prompt（直接嵌入 query，不依賴 system prompt）
_NLM_CONTENT_PROMPT = (
    "你是 AI 科技編輯，為粉絲專頁「AI 前線觀測站」服務。"
    "請根據這篇論文的完整內容，輸出一篇繁體中文（台灣用語）貼文，要求："
    "①語氣親切但專業；"
    "②保留專有名詞原文並附中文對照；"
    "③結構：先 2-3 句研究亮點，再分段說明重點發現，最後說明對產業或日常的影響；"
    "④使用 Markdown，可用條列和小標題；"
    "⑤忠於原文，不得捏造數據或結論；"
    "⑥不要輸出引用標記（如 [1]）；"
    "⑦直接輸出內容本體，不要有任何前言或說明。"
)
_NLM_TITLE_PROMPT = (
    "請根據這篇論文的主題，輸出一行繁體中文標題，"
    "要求吸引人但忠於原意，不要加引號或任何說明，直接輸出標題文字。"
)
_NLM_RESEARCH_PROMPT = (
    "你是 AI 學術研究助理，協助研究人員深度閱讀論文。"
    "請根據這篇論文的完整內容，輸出詳細的繁體中文（台灣學術用語）研究筆記，要求："
    "①語氣客觀、學術；"
    "②保留所有重要的專有名詞（附原文）、數學符號、模型名稱、資料集名稱；"
    "③結構依序包含（無相關內容可略過）：## 研究背景與動機、## 核心貢獻、"
    "## 方法與架構、## 實驗設定、## 實驗結果與分析、## 相關研究比較、"
    "## 侷限性與未來工作、## 研究意義與應用前景；"
    "④保留原始數值（準確率、BLEU 分數、參數量等）；"
    "⑤不得捏造或推論原文未提及的內容；"
    "⑥不要輸出引用標記（如 [1]）；"
    "⑦直接輸出筆記本體，不要有任何前言或說明。"
)


def _nlm_run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["nlm", *args], capture_output=True, text=True, check=True)


def _nlm_translate_file(file_path: str) -> tuple[str, str, str]:
    """上傳檔案至 NotebookLM，取得繁中標題、社群貼文、學術研究筆記後刪除 source。"""
    if not NOTEBOOKLM_NOTEBOOK_ID:
        raise RuntimeError(
            "NotebookLM 翻譯需設定 NOTEBOOKLM_NOTEBOOK_ID，"
            "請在 ~/.ai_news_hub/credentials 加入此變數。"
        )
    # 上傳檔案 (PDF 或 TXT)
    result = _nlm_run("source", "add", NOTEBOOKLM_NOTEBOOK_ID, "--file", file_path, "--wait")
    m = re.search(r"Source ID:\s*([0-9a-f-]{36})", result.stdout)
    if not m:
        raise RuntimeError(f"無法從 nlm 輸出解析 source ID：{result.stdout}")
    source_id = m.group(1)
    log.info("NotebookLM source 上傳完成：%s", source_id)

    def _query(prompt: str) -> str:
        raw = json.loads(
            _nlm_run(
                "notebook", "query", NOTEBOOKLM_NOTEBOOK_ID,
                prompt, "--source-ids", source_id, "--json",
            ).stdout
        )["answer"].strip()
        return re.sub(r"\s*\[\d+(?:,\s*\d+)*\]", "", raw).strip()

    try:
        title_raw = _query(_NLM_TITLE_PROMPT)
        content_raw = _query(_NLM_CONTENT_PROMPT)
        research_raw = _query(_NLM_RESEARCH_PROMPT)
        return title_raw, content_raw, research_raw
    finally:
        try:
            _nlm_run("source", "delete", source_id, "--confirm")
            log.info("NotebookLM source 已清除：%s", source_id)
        except Exception as exc:
            log.warning("NotebookLM source 清除失敗（%s）：%s", source_id, exc)


def _generate_images(article_id: int, pdf_path: str | None = None) -> None:
    """呼叫圖片處理模組；失敗時僅記錄 warning，不中斷翻譯流程。"""
    try:
        from .image_processor import generate_article_images
        generate_article_images(article_id, pdf_path)
    except Exception as exc:
        log.warning("圖片生成失敗（不影響翻譯）#%s：%s", article_id, exc)


def _delete_local_pdf(pdf_media: ArticleMedia) -> None:
    """刪除 media_files 目錄下的 PDF 檔，並清空 local_path 欄位。"""
    from pathlib import Path

    path = Path(pdf_media.local_path)
    try:
        path.unlink(missing_ok=True)
        log.info("已刪除本機 PDF：%s", path)
    except OSError as exc:
        log.warning("刪除本機 PDF 失敗（%s）：%s", path, exc)
    pdf_media.local_path = None


def translate_article(article_id: int) -> bool:
    """翻譯單篇文章：一律使用 NotebookLM 轉譯為繁中標題、貼文、學術筆記。

    優先使用 PDF 上傳；無 PDF 時則將英文原文寫入暫存文字檔上傳至 NotebookLM。
    """
    with Session() as session:
        article = session.get(Article, article_id)
        if not article or not article.content_original:
            return False
        try:
            pdf_media = next(
                (m for m in article.media if m.media_type == "pdf" and m.local_path),
                None,
            )
            if pdf_media:
                log.info("NotebookLM 翻譯 #%s（PDF：%s）", article.id, pdf_media.local_path)
                title_zh, body, research = _nlm_translate_file(pdf_media.local_path)
                article.title_zh = title_zh[:1000]
                article.content_zh = f"{body}\n\n---\n{article.attribution}"
                article.content_research = f"{research}\n\n---\n{article.attribution}"
                article.status = "translated"
                session.commit()
                # 圖片生成後再刪 PDF，確保可截取第一頁
                _generate_images(article.id, pdf_media.local_path)
                _delete_local_pdf(pdf_media)
                session.commit()
                log.info("已翻譯文章 #%s（NotebookLM PDF）：%s", article.id, article.title_zh)
                return True
            else:
                log.info("NotebookLM 翻譯 #%s（無 PDF，使用英文原文）", article.id)
                # 將原文寫入暫存 TXT 檔上傳
                import tempfile
                from pathlib import Path
                temp_dir = Path(__file__).resolve().parent.parent / "media_files"
                temp_dir.mkdir(exist_ok=True)
                temp_file_path = temp_dir / f"temp_article_{article.id}.txt"
                try:
                    temp_file_path.write_text(article.content_original, encoding="utf-8")
                    title_zh, body, research = _nlm_translate_file(str(temp_file_path))
                    article.title_zh = title_zh[:1000]
                    article.content_zh = f"{body}\n\n---\n{article.attribution}"
                    article.content_research = f"{research}\n\n---\n{article.attribution}"
                    article.status = "translated"
                    session.commit()
                    _generate_images(article.id)
                    log.info("已翻譯文章 #%s（NotebookLM 文字）：%s", article.id, article.title_zh)
                    return True
                finally:
                    try:
                        if temp_file_path.exists():
                            temp_file_path.unlink()
                            log.info("已刪除暫存文字檔：%s", temp_file_path)
                    except Exception as exc:
                        log.warning("刪除暫存文字檔失敗：%s", exc)
        except Exception as exc:
            session.rollback()
            log.error("翻譯失敗 #%s：%s", article_id, exc)
            return False


def translate_pending(limit: int = 10) -> int:
    """批次翻譯尚未翻譯的文章。"""
    with Session() as session:
        ids = [
            row[0]
            for row in session.query(Article.id)
            .filter(Article.status == "draft", Article.content_original.isnot(None))
            .order_by(Article.created_at.desc())
            .limit(limit)
            .all()
        ]
    done = 0
    for article_id in ids:
        if translate_article(article_id):
            done += 1
    return done
