"""翻譯模組：以本地 ollama、Claude API 或 NotebookLM 將英文學術內容整理並轉譯為繁體中文。

需求對應：
- 1.5 蒐集資料整理並轉譯為繁體中文
- 1.6 轉譯後須為可直接發布的內容（標題 + 貼文）

後端由環境變數 TRANSLATE_BACKEND 控制（預設 ollama）：
  TRANSLATE_BACKEND=ollama       → 使用 TRANSLATE_MODEL @ OLLAMA_BASE_URL
  TRANSLATE_BACKEND=claude       → 使用 CLAUDE_TRANSLATE_MODEL（需 ANTHROPIC_API_KEY）
  TRANSLATE_BACKEND=notebooklm   → 直接上傳 PDF 至 NotebookLM（需 NOTEBOOKLM_NOTEBOOK_ID + nlm CLI）
                                    有 PDF 時以 Gemini 直讀；無 PDF 則自動降回 ollama
"""
from __future__ import annotations

import json
import logging
import re
import subprocess

import requests

from .config import (
    CLAUDE_TRANSLATE_MODEL,
    NOTEBOOKLM_NOTEBOOK_ID,
    OLLAMA_BASE_URL,
    TRANSLATE_BACKEND,
    TRANSLATE_MODEL,
)
from .db import Article, ArticleMedia, Session

log = logging.getLogger("translator")

SUMMARIZE_PROMPT = """You are a research assistant. Your task is to extract the key points from an AI research paper or article.

Output a structured summary in English with the following sections:
- **Research Problem**: What problem does this work address?
- **Key Contributions**: List 3-5 main findings or contributions.
- **Methods**: Briefly describe the approach or methodology.
- **Results**: Key quantitative or qualitative results.
- **Impact**: Practical implications for industry or society.

Be concise and factual. Do not invent information not present in the source. Output only the structured summary."""

SYSTEM_PROMPT = """你是專業的 AI 科技編輯，為科普粉絲專頁「AI 前線觀測站」服務。
任務：將英文 AI 學術論文或文章的重點摘要翻譯整理為繁體中文（台灣用語）。

要求：
1. 輸出為繁體中文（台灣用語），語氣親切但專業。
2. 保留專有名詞原文（如 Transformer、LLM、reinforcement learning 可附中文對照）。
3. 結構：先以 2-3 句話點出研究亮點，再分段說明重點發現與意義，最後一段說明對產業或日常的影響。
4. 使用 Markdown 格式，可用適量的條列與小標題；不要使用表情符號以外的裝飾字元。
5. 忠於原文，不得捏造原文沒有的數據或結論。
6. 不要輸出任何前言或說明，直接輸出翻譯整理後的內容本體。"""

RESEARCH_SYSTEM_PROMPT = """你是 AI 學術研究助理，協助研究人員深度閱讀與整理英文 AI 學術論文。
任務：將英文論文的完整研究內容整理為繁體中文（台灣學術用語）的詳細研究筆記。

要求：
1. 輸出為供研究人員參考的詳細繁體中文筆記，語氣客觀、學術。
2. 保留所有重要的專有名詞（附原文）、數學符號、模型名稱、資料集名稱。
3. 結構必須包含以下各節（若原文無相關內容可略過）：
   ## 研究背景與動機
   ## 核心貢獻
   ## 方法與架構
   ## 實驗設定
   ## 實驗結果與分析
   ## 相關研究比較
   ## 侷限性與未來工作
   ## 研究意義與應用前景
4. 實驗結果須保留原始數值（如準確率、BLEU 分數、參數量等）。
5. 忠於原文，不得捏造或推論原文未明確提及的內容。
6. 不要輸出任何前言或說明，直接輸出筆記本體。"""

RESEARCH_SUMMARIZE_PROMPT = """You are an AI research assistant. Extract a comprehensive and detailed summary from an AI research paper.

Output a thorough structured summary in English with the following sections:
- **Background & Motivation**: Research context, problem statement, and why it matters.
- **Core Contributions**: All key contributions in detail.
- **Methods & Architecture**: Detailed description of the proposed approach, model architecture, algorithms.
- **Experimental Setup**: Datasets, baselines, evaluation metrics, hardware/compute details.
- **Results & Analysis**: All quantitative results with exact numbers, ablations, qualitative findings.
- **Comparison with Related Work**: How this work relates to or outperforms prior art.
- **Limitations & Future Work**: Acknowledged limitations and suggested future directions.
- **Significance & Applications**: Broader impact and practical applications.

Be thorough and retain all specific numbers, model names, and dataset names. Do not invent information not present in the source. Output only the structured summary."""

# 單塊翻譯的原文字元上限（過長時切塊）
CHUNK_CHARS = 24000
# 翻譯來源內容上限（避免超長 PDF 全文爆量，截至段落邊界）
MAX_SOURCE_CHARS = 120000

_claude_client = None

# NotebookLM 翻譯用 prompt（直接嵌入 query，不依賴 system prompt）
_NLM_CONTENT_PROMPT = (
    "你是 AI 科技編輯，為粉絲專頁「AI 前線觀站」服務。"
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


def _nlm_translate_pdf(pdf_path: str, article_title: str) -> tuple[str, str, str]:
    """上傳 PDF 至 NotebookLM，取得繁中標題、社群貼文、學術研究筆記後刪除 source。"""
    if not NOTEBOOKLM_NOTEBOOK_ID:
        raise RuntimeError(
            "TRANSLATE_BACKEND=notebooklm 需設定 NOTEBOOKLM_NOTEBOOK_ID，"
            "請在 ~/.ai_news_hub/credentials 加入此變數。"
        )
    # 上傳 PDF
    result = _nlm_run("source", "add", NOTEBOOKLM_NOTEBOOK_ID, "--file", pdf_path, "--wait")
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


def _call_ollama(user_text: str) -> str:
    import json as _json

    parts: list[str] = []
    with requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": TRANSLATE_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "stream": True,
            "think": False,
        },
        stream=True,
        timeout=600,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            chunk = _json.loads(line)
            parts.append(chunk.get("message", {}).get("content", ""))
            if chunk.get("done"):
                break
    return "".join(parts).strip()


def _call_claude(user_text: str) -> str:
    global _claude_client
    import anthropic

    if _claude_client is None:
        _claude_client = anthropic.Anthropic()
    with _claude_client.messages.stream(
        model=CLAUDE_TRANSLATE_MODEL,
        max_tokens=64000,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_text}],
    ) as stream:
        message = stream.get_final_message()
    return "".join(b.text for b in message.content if b.type == "text").strip()


def _call(user_text: str) -> str:
    if TRANSLATE_BACKEND == "claude":
        return _call_claude(user_text)
    return _call_ollama(user_text)


def _split_chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """依段落邊界切塊，避免句子被截斷。"""
    if len(text) <= size:
        return [text]
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        if current and len(current) + len(p) + 2 > size:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks


def summarize_content(text: str, title: str) -> str:
    """第一步：將原文彙整為結構化重點（英文），供後續翻譯使用。"""
    text = text[:MAX_SOURCE_CHARS]
    chunks = _split_chunks(text)
    if len(chunks) == 1:
        prompt = f"Paper title: {title}\n\nSource text:\n\n{chunks[0]}"
        backend_backup = TRANSLATE_BACKEND
        # 使用 SUMMARIZE_PROMPT 而非 SYSTEM_PROMPT
        return _call_with_system(SUMMARIZE_PROMPT, prompt)
    # 長文本：逐塊彙整後合併為單一摘要
    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(
            _call_with_system(
                SUMMARIZE_PROMPT,
                f"Paper title: {title}\n\nPart {i}/{len(chunks)}:\n\n{chunk}",
            )
        )
    combined = "\n\n".join(parts)
    return _call_with_system(
        SUMMARIZE_PROMPT,
        f"Paper title: {title}\n\nMerge and deduplicate the following partial summaries into one coherent structured summary:\n\n{combined}",
    )


def _call_with_system(system: str, user_text: str) -> str:
    """使用指定 system prompt 呼叫後端。"""
    if TRANSLATE_BACKEND == "claude":
        global _claude_client
        import anthropic
        if _claude_client is None:
            _claude_client = anthropic.Anthropic()
        with _claude_client.messages.stream(
            model=CLAUDE_TRANSLATE_MODEL,
            max_tokens=8192,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_text}],
        ) as stream:
            message = stream.get_final_message()
        return "".join(b.text for b in message.content if b.type == "text").strip()
    # ollama
    import json as _json
    parts: list[str] = []
    with requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": TRANSLATE_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "stream": True,
            "think": False,
        },
        stream=True,
        timeout=600,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            chunk = _json.loads(line)
            parts.append(chunk.get("message", {}).get("content", ""))
            if chunk.get("done"):
                break
    return "".join(parts).strip()


def translate_title(title: str) -> str:
    return _call(
        "將以下英文論文標題翻譯為一行繁體中文標題（吸引人但忠於原意，"
        "不要加引號或任何說明）：\n\n" + title
    )


def translate_content(summary: str, title: str) -> str:
    """第二步：將彙整後的英文重點翻譯為繁體中文貼文。"""
    return _call(f"論文標題：{title}\n\n以下為論文重點摘要，請翻譯整理為繁體中文貼文：\n\n{summary}")


def summarize_research(text: str, title: str) -> str:
    """為學術研究筆記生成詳細英文結構化摘要。"""
    text = text[:MAX_SOURCE_CHARS]
    chunks = _split_chunks(text)
    if len(chunks) == 1:
        return _call_with_system(
            RESEARCH_SUMMARIZE_PROMPT,
            f"Paper title: {title}\n\nSource text:\n\n{chunks[0]}",
        )
    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(
            _call_with_system(
                RESEARCH_SUMMARIZE_PROMPT,
                f"Paper title: {title}\n\nPart {i}/{len(chunks)}:\n\n{chunk}",
            )
        )
    combined = "\n\n".join(parts)
    return _call_with_system(
        RESEARCH_SUMMARIZE_PROMPT,
        f"Paper title: {title}\n\nMerge the following partial summaries into one coherent detailed structured summary:\n\n{combined}",
    )


def translate_research(research_summary: str, title: str) -> str:
    """將詳細英文摘要翻譯為繁體中文學術研究筆記。"""
    return _call_with_system(
        RESEARCH_SYSTEM_PROMPT,
        f"論文標題：{title}\n\n以下為論文詳細摘要，請整理為繁體中文學術研究筆記：\n\n{research_summary}",
    )


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
    """翻譯單篇文章：彙整重點 → 翻譯 → 更新狀態 draft -> translated。

    notebooklm 後端：優先找 PDF 直接上傳；無 PDF 時自動降回 ollama 文字翻譯。
    """
    with Session() as session:
        article = session.get(Article, article_id)
        if not article or not article.content_original:
            return False
        try:
            # notebooklm 後端：優先以 PDF 翻譯
            if TRANSLATE_BACKEND == "notebooklm":
                pdf_media = next(
                    (m for m in article.media if m.media_type == "pdf" and m.local_path),
                    None,
                )
                if pdf_media:
                    log.info("NotebookLM 翻譯 #%s（PDF：%s）", article.id, pdf_media.local_path)
                    title_zh, body, research = _nlm_translate_pdf(pdf_media.local_path, article.title)
                    article.title_zh = title_zh[:1000]
                    article.content_zh = f"{body}\n\n---\n{article.attribution}"
                    article.content_research = f"{research}\n\n---\n{article.attribution}"
                    article.status = "translated"
                    session.commit()
                    # 圖片生成後再刪 PDF，確保可截取第一頁
                    _generate_images(article.id, pdf_media.local_path)
                    _delete_local_pdf(pdf_media)
                    session.commit()
                    log.info("已翻譯文章 #%s（NotebookLM）：%s", article.id, article.title_zh)
                    return True
                log.warning("文章 #%s 無 PDF，降回 ollama 翻譯", article.id)

            # ollama / claude 後端（或 notebooklm 降回）
            article.title_zh = translate_title(article.title)[:1000]
            log.info("彙整社群重點 #%s", article.id)
            key_points = summarize_content(article.content_original, article.title)
            log.info("翻譯社群內容 #%s（%d 字元）", article.id, len(key_points))
            body = translate_content(key_points, article.title)
            article.content_zh = f"{body}\n\n---\n{article.attribution}"
            log.info("彙整學術研究重點 #%s", article.id)
            research_points = summarize_research(article.content_original, article.title)
            log.info("翻譯學術研究筆記 #%s（%d 字元）", article.id, len(research_points))
            research_body = translate_research(research_points, article.title)
            article.content_research = f"{research_body}\n\n---\n{article.attribution}"
            article.status = "translated"
            session.commit()
            _generate_images(article.id)
            log.info("已翻譯文章 #%s：%s", article.id, article.title_zh)
            return True
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
