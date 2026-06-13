"""Google Drive 媒體儲存模組（OAuth2 使用者憑證）。

初始設定（一次性）：
  1. GCP Console → APIs & Services → Credentials → Create OAuth 2.0 Client ID（Desktop app）
  2. 下載 JSON 至 ~/.ai_news_hub/gdrive_oauth_client.json（chmod 600）
  3. 執行 python setup_gdrive.py → 瀏覽器授權 → token 自動儲存
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

log = logging.getLogger("gdrive")

_TOKEN_PATH = Path.home() / ".ai_news_hub" / "gdrive_token.json"
_CLIENT_PATH = Path.home() / ".ai_news_hub" / "gdrive_oauth_client.json"
_SCOPES = ["https://www.googleapis.com/auth/drive"]


def is_available() -> bool:
    return _TOKEN_PATH.exists()


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not _TOKEN_PATH.exists():
        raise RuntimeError(
            f"找不到 Drive token，請執行 python setup_gdrive.py 完成授權。\n"
            f"期望路徑：{_TOKEN_PATH}"
        )

    creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _TOKEN_PATH.write_text(creds.to_json())
        _TOKEN_PATH.chmod(0o600)

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_bytes(
    data: bytes,
    filename: str,
    mime_type: str,
    folder_id: str | None = None,
) -> tuple[str, str]:
    """上傳 bytes 至 Drive，回傳 (file_id, public_url)。"""
    from googleapiclient.http import MediaIoBaseUpload

    svc = _service()
    meta: dict = {"name": filename}
    if folder_id:
        meta["parents"] = [folder_id]
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type)
    f = svc.files().create(body=meta, media_body=media, fields="id").execute()
    file_id: str = f["id"]
    svc.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()
    url = f"https://drive.google.com/uc?export=view&id={file_id}"
    log.info("Drive 上傳完成：%s → %s", filename, file_id)
    return file_id, url


def delete_file(file_id: str) -> None:
    try:
        _service().files().delete(fileId=file_id).execute()
        log.info("Drive 檔案已刪除：%s", file_id)
    except Exception as exc:
        log.warning("Drive 檔案刪除失敗 %s：%s", file_id, exc)
