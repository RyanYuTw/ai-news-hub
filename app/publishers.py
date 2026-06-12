"""社群發布模組。

各平台憑證變數命名（{KEY} 為 platforms.credential_key，預設 FACEBOOK/INSTAGRAM/THREADS/YOUTUBE）：
  Facebook : {KEY}_PAGE_ID, {KEY}_PAGE_ACCESS_TOKEN
  Instagram: {KEY}_IG_USER_ID, {KEY}_ACCESS_TOKEN
  Threads  : {KEY}_USER_ID, {KEY}_ACCESS_TOKEN
  YouTube  : {KEY}_CLIENT_ID, {KEY}_CLIENT_SECRET, {KEY}_REFRESH_TOKEN
申請方式見 docs/social_setup.md。
"""
from __future__ import annotations

import logging
import os

import requests

from .db import Article, Platform

log = logging.getLogger("publisher")

GRAPH = "https://graph.facebook.com/v21.0"
THREADS_GRAPH = "https://graph.threads.net/v1.0"
TIMEOUT = 60


class PublishError(Exception):
    pass


def _env(key: str, name: str) -> str:
    value = os.environ.get(f"{key}_{name}", "").strip()
    if not value:
        raise PublishError(
            f"缺少憑證 {key}_{name}，請在 ~/.ai_news_hub/credentials 設定後重試"
        )
    return value


def compose_text(article: Article, max_len: int | None = None) -> str:
    """組合貼文文字：中文標題 + 內文 + 原文連結（內文已含資料來源標註）。"""
    title = article.title_zh or article.title
    body = article.content_zh or ""
    text = f"{title}\n\n{body}\n\n原文連結：{article.url}"
    if max_len and len(text) > max_len:
        keep = max_len - len(f"…\n\n原文連結：{article.url}")
        text = f"{text[:keep]}…\n\n原文連結：{article.url}"
    return text


def _first_image(article: Article) -> str | None:
    for m in article.media:
        if m.media_type == "image" and m.url.startswith("http"):
            return m.url
    return None


def _first_video_path(article: Article) -> str | None:
    for m in article.media:
        if m.media_type == "video" and m.local_path and os.path.exists(m.local_path):
            return m.local_path
    return None


# ---------------------------------------------------------------- Facebook
def publish_facebook(article: Article, platform: Platform) -> str:
    key = platform.credential_key
    page_id = _env(key, "PAGE_ID")
    token = _env(key, "PAGE_ACCESS_TOKEN")
    payload = {
        "message": compose_text(article),
        "link": article.url,
        "access_token": token,
    }
    resp = requests.post(f"{GRAPH}/{page_id}/feed", data=payload, timeout=TIMEOUT)
    data = resp.json()
    if "error" in data:
        raise PublishError(f"Facebook：{data['error'].get('message', resp.text[:300])}")
    post_id = data.get("id", "")
    return f"https://www.facebook.com/{post_id}"


# --------------------------------------------------------------- Instagram
def publish_instagram(article: Article, platform: Platform) -> str:
    """IG 必須附圖：建立 media container 後 publish。"""
    key = platform.credential_key
    ig_user = _env(key, "IG_USER_ID")
    token = _env(key, "ACCESS_TOKEN")
    image_url = _first_image(article)
    if not image_url:
        raise PublishError("Instagram 發布需要至少一張圖片（article_media 無可用圖片）")
    caption = compose_text(article, max_len=2200)
    resp = requests.post(
        f"{GRAPH}/{ig_user}/media",
        data={"image_url": image_url, "caption": caption, "access_token": token},
        timeout=TIMEOUT,
    )
    data = resp.json()
    if "error" in data:
        raise PublishError(f"Instagram 建立素材失敗：{data['error'].get('message')}")
    creation_id = data["id"]
    resp = requests.post(
        f"{GRAPH}/{ig_user}/media_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=TIMEOUT,
    )
    data = resp.json()
    if "error" in data:
        raise PublishError(f"Instagram 發布失敗：{data['error'].get('message')}")
    return f"https://www.instagram.com/p/{data.get('id', '')}"


# ------------------------------------------------------------------ Threads
def publish_threads(article: Article, platform: Platform) -> str:
    key = platform.credential_key
    user_id = _env(key, "USER_ID")
    token = _env(key, "ACCESS_TOKEN")
    text = compose_text(article, max_len=500)
    resp = requests.post(
        f"{THREADS_GRAPH}/{user_id}/threads",
        data={"media_type": "TEXT", "text": text, "access_token": token},
        timeout=TIMEOUT,
    )
    data = resp.json()
    if "error" in data:
        raise PublishError(f"Threads 建立貼文失敗：{data['error'].get('message')}")
    creation_id = data["id"]
    resp = requests.post(
        f"{THREADS_GRAPH}/{user_id}/threads_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=TIMEOUT,
    )
    data = resp.json()
    if "error" in data:
        raise PublishError(f"Threads 發布失敗：{data['error'].get('message')}")
    return f"https://www.threads.net/post/{data.get('id', '')}"


# ------------------------------------------------------------------ YouTube
def publish_youtube(article: Article, platform: Platform) -> str:
    """YouTube 僅支援影片上傳：文章需附本機影片檔（article_media.local_path）。"""
    video_path = _first_video_path(article)
    if not video_path:
        raise PublishError(
            "YouTube 發布需要本機影片檔；此文章無影片素材，"
            "請改選其他平台或先為文章加入影片"
        )
    key = platform.credential_key
    client_id = _env(key, "CLIENT_ID")
    client_secret = _env(key, "CLIENT_SECRET")
    refresh_token = _env(key, "REFRESH_TOKEN")

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": (article.title_zh or article.title)[:100],
            "description": compose_text(article, max_len=4800),
            "categoryId": "28",  # Science & Technology
        },
        "status": {"privacyStatus": "public"},
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return f"https://www.youtube.com/watch?v={response['id']}"


# ------------------------------------------------------------------- 自訂平台
def publish_custom(article: Article, platform: Platform) -> str:
    """自訂平台：以 webhook 方式 POST JSON 到 config.webhook_url。

    憑證 {KEY}_TOKEN 以 Bearer 帶入 Authorization 標頭。
    """
    config = platform.config or {}
    webhook = config.get("webhook_url")
    if not webhook:
        raise PublishError("自訂平台缺少 config.webhook_url")
    headers = {}
    token = os.environ.get(f"{platform.credential_key}_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "title": article.title_zh or article.title,
        "content": article.content_zh,
        "url": article.url,
        "attribution": article.attribution,
        "media": [
            {"type": m.media_type, "url": m.url, "attribution": m.attribution}
            for m in article.media
        ],
    }
    resp = requests.post(webhook, json=payload, headers=headers, timeout=TIMEOUT)
    if resp.status_code >= 300:
        raise PublishError(f"自訂平台回應 {resp.status_code}：{resp.text[:300]}")
    return resp.headers.get("Location") or webhook


PUBLISHER_BY_TYPE = {
    "facebook": publish_facebook,
    "instagram": publish_instagram,
    "threads": publish_threads,
    "youtube": publish_youtube,
    "custom": publish_custom,
}


def publish(article: Article, platform: Platform) -> str:
    """發布文章至指定平台，回傳貼文網址。"""
    handler = PUBLISHER_BY_TYPE.get(platform.type)
    if not handler:
        raise PublishError(f"不支援的平台類型：{platform.type}")
    if article.status != "online":
        raise PublishError("文章未上架（status 必須為 online）才能發布")
    return handler(article, platform)
