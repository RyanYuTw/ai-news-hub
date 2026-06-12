"""設定與憑證載入。

憑證一律存放在隱藏檔 ~/.ai_news_hub/credentials（chmod 600），
格式為 KEY=VALUE，啟動時載入環境變數，程式內以變數讀取，
絕不寫入 git 追蹤檔案或 log。
"""
import os
from pathlib import Path

CRED_DIR = Path.home() / ".ai_news_hub"
CRED_FILE = CRED_DIR / "credentials"

DB_NAME = "ai_news_hub"
MY_CNF = str(Path.home() / ".my.cnf")

# 媒體下載存放目錄
MEDIA_DIR = Path(__file__).resolve().parent.parent / "media_files"

# 全文抓取重試次數（需求 1.2：三次後改用摘要）
FULLTEXT_MAX_ATTEMPTS = 3


def load_credentials() -> None:
    """將隱藏憑證檔載入環境變數（已存在的環境變數優先）。"""
    if not CRED_FILE.exists():
        return
    mode = CRED_FILE.stat().st_mode & 0o777
    if mode & 0o077:
        # 權限過寬時僅警告，不顯示內容
        print(f"[警告] {CRED_FILE} 權限為 {oct(mode)}，建議執行 chmod 600")
    for line in CRED_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_dirs() -> None:
    CRED_DIR.mkdir(mode=0o700, exist_ok=True)
    MEDIA_DIR.mkdir(exist_ok=True)


load_credentials()
ensure_dirs()

# 蒐集排程、翻譯排程與發布檢查排程的 Cron 設定
COLLECT_CRON = os.environ.get("COLLECT_CRON", "0 */2 * * *")
TRANSLATE_CRON = os.environ.get("TRANSLATE_CRON", "*/30 * * * *")
PUBLISH_CRON = os.environ.get("PUBLISH_CRON", "* * * * *")

# notebooklm 設定
# 需預先建立一個 NotebookLM notebook 並將 UUID 填入此變數
NOTEBOOKLM_NOTEBOOK_ID = os.environ.get("NOTEBOOKLM_NOTEBOOK_ID", "")

