#!/usr/bin/env python3
"""初始化 Google Drive OAuth2 授權（一次性）。

使用步驟：
  1. GCP Console → APIs & Services → Credentials
     → Create Credentials → OAuth 2.0 Client ID → Desktop app
  2. 下載 JSON，儲存為 ~/.ai_news_hub/gdrive_oauth_client.json
     chmod 600 ~/.ai_news_hub/gdrive_oauth_client.json
  3. 執行本腳本：python setup_gdrive.py
     → 瀏覽器開啟授權頁面 → 選擇 Google 帳號並允許
     → token 自動儲存至 ~/.ai_news_hub/gdrive_token.json
"""
from pathlib import Path

_CLIENT_PATH = Path.home() / ".ai_news_hub" / "gdrive_oauth_client.json"
_TOKEN_PATH = Path.home() / ".ai_news_hub" / "gdrive_token.json"
_SCOPES = ["https://www.googleapis.com/auth/drive"]

if not _CLIENT_PATH.exists():
    print(f"錯誤：找不到 OAuth client 設定檔。")
    print(f"請將 GCP 下載的 JSON 儲存至：{_CLIENT_PATH}")
    raise SystemExit(1)

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("缺少套件，請執行：pip install google-auth-oauthlib")
    raise SystemExit(1)

print("正在開啟瀏覽器進行 Google 授權...")
flow = InstalledAppFlow.from_client_secrets_file(str(_CLIENT_PATH), _SCOPES)
creds = flow.run_local_server(port=0)

_TOKEN_PATH.write_text(creds.to_json())
_TOKEN_PATH.chmod(0o600)
print(f"授權成功！Token 已儲存至：{_TOKEN_PATH}")
print("日後 token 過期會自動更新，無需重新授權。")
