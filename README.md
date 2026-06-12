# AI 前線觀測站（AI News Hub）

蒐集 AI 論文與相關資訊 → 轉譯為繁體中文 → 後台管理 → 排程自動發布至社群平台。

- 資料庫：MySQL（`ai_news_hub`，連線經 `~/.my.cnf`，無明文密碼）
- 語言：Python 3.13（Flask + SQLAlchemy + APScheduler + Anthropic SDK）

## 功能總覽

| 需求 | 實作 |
|---|---|
| 1.1 預設/自訂來源，RSS 優先、無 RSS 解析網頁 | `app/collectors.py`（rss / html / arxiv_api 三種處理器，後台可新增自訂來源） |
| 1.2 全文優先，3 次失敗改用摘要 | `fetch_fulltext()` 重試 3 次，`is_fulltext` / `fetch_attempts` 記錄 |
| 1.3 預設來源 | arXiv cs.AI（官方 API）、Nature Machine Intelligence、JAIR、Artificial Intelligence Review（Springer），均以 RSS/API 蒐集 |
| 1.4 PDF 轉 md/txt、去重、標註來源 | pypdf 轉文字；`url_hash`（SHA-256）唯一索引去重；`attribution` 欄位 |
| 1.5 / 1.6 繁中轉譯、可發布內容 | `app/translator.py`：預設以本機 Ollama（`qwen3.5:9b`）翻譯整理為可直接發布的貼文，長文自動切塊；設定 `TRANSLATE_BACKEND=claude` 可改用 Claude API；設定 `TRANSLATE_BACKEND=notebooklm` 可直接上傳 PDF 至 NotebookLM（Gemini 直讀，無 PDF 時自動降回 ollama） |
| 1.7 圖片/影音標註來源 | `article_media` 表含 `attribution` |
| 1.8 分頁列表、編輯、預覽、上下架、排程自動發布 | Flask 後台 + APScheduler |
| 2.1 / 2.2 FB / IG / Threads / YouTube + 自訂平台 | `app/publishers.py`（官方 API；自訂平台走 Webhook） |
| 2.3 粉專名稱 | **AI 前線觀測站**（帳號申請步驟見 `docs/social_setup.md`，平台規定禁止自動開帳號） |
| 2.4 憑證隱藏檔 + 變數讀取 | `~/.ai_news_hub/credentials`（chmod 600）→ 環境變數 |
| 3 Schema 自動規劃 | `schema.sql`（僅操作 `ai_news_hub`，不觸碰其他資料庫） |

## 快速開始

```bash
cd ai_news_hub

# 1. 建立資料庫（已執行過可略）
mysql < schema.sql

# 2. 安裝套件
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. 設定憑證（Claude 翻譯需 ANTHROPIC_API_KEY；預設用本機 Ollama 無需 API key；社群發布見 docs/social_setup.md）
mkdir -p ~/.ai_news_hub
cp credentials.example ~/.ai_news_hub/credentials
chmod 600 ~/.ai_news_hub/credentials

# 4. 啟動後台（含排程器：預設每 2 小時蒐集、每 30 分鐘翻譯一篇、每分鐘檢查發布排程）
.venv/bin/python run.py
# → http://127.0.0.1:5000
```

### CLI

```bash
.venv/bin/python collect.py --limit 10            # 手動蒐集
.venv/bin/python collect.py --translate           # 蒐集後翻譯（預設 Ollama）
TRANSLATE_BACKEND=claude .venv/bin/python collect.py --translate       # 改用 Claude API
TRANSLATE_BACKEND=notebooklm .venv/bin/python collect.py --translate   # 改用 NotebookLM（需設定 NOTEBOOKLM_NOTEBOOK_ID）
COLLECT_INTERVAL_MINUTES=60 .venv/bin/python run.py      # 調整蒐集間隔
TRANSLATE_INTERVAL_MINUTES=15 .venv/bin/python run.py   # 調整翻譯間隔（預設 30 分鐘）
```

## 使用流程

1. **蒐集**：排程自動執行，或後台點「立即蒐集＋翻譯」。新文章狀態為「待翻譯」。
2. **翻譯**：自動翻譯為繁中貼文（狀態→「已翻譯」），也可在文章頁手動重翻。三種後端：
   - `ollama`（預設）：本機 `qwen3.5:9b`，無需 API key
   - `claude`：需 `ANTHROPIC_API_KEY`，翻譯品質最高
   - `notebooklm`：需 `NOTEBOOKLM_NOTEBOOK_ID`（NotebookLM notebook UUID）+ `nlm` CLI；有 PDF 時 Gemini 直讀原文，無 PDF 自動降回 ollama
3. **編輯／預覽**：文章頁左側編輯 Markdown、右側即時預覽，確認後將狀態設為「**上架**」。
4. **排程發布**：選擇平台（可複選）與時間建立排程；時間一到由排程器自動發布，結果（貼文連結／失敗原因）記錄在排程列表。

## 專案結構

```
ai_news_hub/
├── schema.sql              # MySQL schema + 預設來源/平台
├── run.py                  # Web 後台 + 排程器入口
├── collect.py              # 手動蒐集 CLI
├── credentials.example     # 憑證範本（真實憑證放 ~/.ai_news_hub/credentials）
├── docs/social_setup.md    # 各社群平台帳號/API 申請指南
└── app/
    ├── config.py           # 設定與隱藏檔憑證載入
    ├── db.py               # SQLAlchemy 模型（經 ~/.my.cnf 連線）
    ├── collectors.py       # RSS / arXiv API / HTML 蒐集、PDF→md、去重
    ├── translator.py       # 繁中翻譯（Ollama 預設 / Claude / NotebookLM 可選；NotebookLM 直讀 PDF，翻譯後自動刪除本機 PDF）
    ├── publishers.py       # FB / IG / Threads / YouTube / 自訂 Webhook
    ├── scheduler.py        # APScheduler：定時蒐集（每 2 小時）、每 30 分鐘翻譯一篇、到時發布
    ├── web.py              # Flask 後台
    └── templates/          # 後台頁面
```

## 安全事項

- 資料庫連線一律走 `~/.my.cnf`，程式與設定檔無明文密碼。
- 社群/API 憑證僅存於 `~/.ai_news_hub/credentials`（隱藏檔、chmod 600），以環境變數讀取，不入庫、不進 log。
- Schema 只建立與操作 `ai_news_hub` 資料庫，刪除操作僅限本系統資料表，且來源預設項目不可刪除（僅能停用）。
- 各平台**帳號申請需手動完成**（平台條款禁止自動開帳號），步驟見 `docs/social_setup.md`；申請一次後即可全自動發布。
