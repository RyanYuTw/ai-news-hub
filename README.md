# AI 前線觀測站（AI News Hub）

蒐集 AI 論文與相關資訊 → 以 NotebookLM 轉譯為繁體中文 → 後台管理與編輯。

- 資料庫：MySQL（`ai_news_hub`，連線經 `~/.my.cnf`，無明文密碼）
- 語言：Python 3.13（Flask + SQLAlchemy + APScheduler）

## 功能總覽

| 功能 | 說明 |
|---|---|
| 多來源蒐集 | RSS、arXiv API、HTML 解析三種模式；後台可新增自訂來源 |
| 全文優先 | 嘗試 3 次抓全文，失敗改用摘要（`is_fulltext` 記錄） |
| PDF 處理 | 偵測 PDF 連結，`local_path` 存直接下載 URL（無需落地） |
| NotebookLM 翻譯 | 有 PDF：`nlm source add --url`；無 PDF：暫存 TXT 上傳；翻譯後自動刪除 source |
| 圖片生成 | 從原始圖片或 PDF 第一頁裁切出三種規格（1080x1080 / 1080x1350 / 1920x1080） |
| Google Drive 同步 | 翻譯文字檔 → `ai_news_hub/txt`；生成圖片 → `ai_news_hub/images`（上架後自動刪除） |
| 後台管理 | 文章列表、編輯、Markdown 即時預覽、狀態切換、手動翻譯／重新生成圖片 |
| 來源管理 | 後台新增／停用蒐集來源 |
| 排程器 | APScheduler：定時蒐集（預設每 2 小時）、定時翻譯（預設每 30 分鐘一篇） |

## 快速開始

```bash
# 1. 建立資料庫（已執行過可略）
mysql < schema.sql

# 2. 安裝套件
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. 設定憑證
mkdir -p ~/.ai_news_hub
cp credentials.example ~/.ai_news_hub/credentials
chmod 600 ~/.ai_news_hub/credentials

# 4. 啟動後台
.venv/bin/python run.py
# → http://127.0.0.1:5001
```

## 憑證設定（`~/.ai_news_hub/credentials`）

```bash
# NotebookLM（必填）
NOTEBOOKLM_NOTEBOOK_ID=<notebook UUID>

# Google Drive（選填，未設定時圖片/文字存本機）
GDRIVE_TXT_FOLDER_ID=<ai_news_hub/txt 資料夾 ID>
GDRIVE_IMAGES_FOLDER_ID=<ai_news_hub/images 資料夾 ID>

# 蒐集/翻譯排程（選填，使用預設可不填）
COLLECT_CRON=0 */2 * * *
TRANSLATE_CRON=*/30 * * * *
```

### Google Drive 設定步驟

1. Google Cloud Console 建立服務帳戶 → 啟用 Drive API → 下載 JSON 金鑰
2. 將金鑰存至 `~/.ai_news_hub/gdrive_service_account.json`（chmod 600）
3. 在 Drive 建立 `ai_news_hub/txt` 和 `ai_news_hub/images` 資料夾
4. 將服務帳戶 email（JSON 內 `client_email`）加為兩個資料夾的「編輯者」
5. 在 credentials 填入兩個資料夾 ID

## 使用流程

1. **蒐集**：排程自動執行，或後台點「立即蒐集＋翻譯」。新文章狀態為「待翻譯」。
2. **翻譯**：排程自動翻譯（每次一篇），或在文章頁手動觸發。
   - 有 PDF 連結：直接以 `--url` 送 NotebookLM，不落地
   - 無 PDF：原文暫存為 TXT 上傳，翻譯後刪除
   - 翻譯完成後：繁中內容寫入資料庫，同步上傳 TXT 至 Drive（`ai_news_hub/txt`）
3. **圖片生成**：翻譯完成後自動生成三種規格圖片，上傳至 Drive（`ai_news_hub/images`）。亦可在文章頁手動重新生成。
4. **編輯／預覽**：文章頁左側編輯 Markdown，右側即時預覽。
5. **上架**：狀態改為「上架」時，Drive 上的圖片自動刪除（已複製使用後不再需要）。

## 資料庫 Schema

| 資料表 | 說明 |
|---|---|
| `sources` | 蒐集來源（RSS / arXiv API / HTML） |
| `articles` | 文章本體（原文、繁中翻譯、狀態） |
| `article_media` | 文章媒體（圖片、PDF）；`local_path` 存 PDF 下載 URL；`gdrive_file_id` 存圖片 Drive ID |

## 專案結構

```
ai_news_hub/
├── schema.sql              # MySQL schema + 預設來源
├── run.py                  # Web 後台 + 排程器入口
├── collect.py              # 手動蒐集 CLI
├── credentials.example     # 憑證範本
└── app/
    ├── config.py           # 設定與憑證載入
    ├── db.py               # SQLAlchemy 模型（經 ~/.my.cnf 連線）
    ├── gdrive.py           # Google Drive 上傳／刪除（Service Account）
    ├── collectors.py       # RSS / arXiv API / HTML 蒐集、PDF 連結偵測、去重
    ├── translator.py       # NotebookLM 翻譯、Drive TXT 上傳
    ├── image_processor.py  # 三規格圖片生成、Drive 上傳
    ├── scheduler.py        # APScheduler 排程（蒐集、翻譯、PDF 清理）
    ├── web.py              # Flask 後台路由
    └── templates/          # 後台頁面
```

## 安全事項

- 資料庫連線一律走 `~/.my.cnf`，程式與設定檔無明文密碼。
- 所有 API 憑證僅存於 `~/.ai_news_hub/credentials`（隱藏檔、chmod 600），以環境變數讀取，不入庫、不進 log。
- Drive 服務帳戶 JSON 金鑰存於 `~/.ai_news_hub/gdrive_service_account.json`，已加入 `.gitignore`。
- Schema 只建立與操作 `ai_news_hub` 資料庫；預設來源不可刪除（僅能停用）。
