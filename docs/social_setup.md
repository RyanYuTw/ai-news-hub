# 社群平台帳號與 API 申請指南

> **為什麼不能自動申請？**
> Facebook / Instagram / Threads / YouTube 的服務條款均禁止以自動化方式建立帳號，
> 且申請流程需要真人身分驗證（手機簡訊、CAPTCHA、開發者資格審核）。
> 因此帳號與 API 憑證需依下列步驟手動申請一次，之後系統即可全自動發布。

申請完成後，將憑證填入 `~/.ai_news_hub/credentials`（範本：專案根目錄 `credentials.example`）：

```bash
mkdir -p ~/.ai_news_hub
cp credentials.example ~/.ai_news_hub/credentials
chmod 600 ~/.ai_news_hub/credentials
# 編輯填入各平台憑證
```

粉絲專頁／頻道建議名稱（已預設於系統）：**AI 前線觀測站**
（各平台名稱規定：FB 粉專不可全大寫與濫用符號；IG/Threads 帳號限英數與 `._`，
可用 `ai.frontier.watch`；YouTube 頻道名稱自由度最高。）

---

## 1. Facebook 粉絲專頁

1. 以個人帳號登入 Facebook → 建立粉絲專頁（名稱：AI 前線觀測站，類別：科學、科技與工程）。
2. 前往 [Meta for Developers](https://developers.facebook.com/) → 建立應用程式（類型：商業）。
3. 應用程式加入產品「Facebook 登入」與權限：`pages_manage_posts`、`pages_read_engagement`。
4. 用 [Graph API 測試工具](https://developers.facebook.com/tools/explorer/) 取得**長效 Page Access Token**：
   - 先取得 User Token（勾選上述權限）→ 用 `GET /me/accounts` 找到粉專的 `id` 與 `access_token`。
   - 將 User Token 換成長效（60 天）後再取 Page Token，Page Token 即為永久有效。
5. 填入：
   ```
   FACEBOOK_PAGE_ID=<粉專 id>
   FACEBOOK_PAGE_ACCESS_TOKEN=<長效 Page Token>
   ```
6. 正式上線需通過 Meta App Review（開發模式下僅管理員可發文，測試足夠）。

## 2. Instagram

1. 建立 Instagram 帳號（建議 `ai.frontier.watch`），到「設定」切換為**商業帳號**。
2. 將 IG 商業帳號**連結到上面的 FB 粉絲專頁**（IG 設定 → 分享到其他應用程式 → Facebook）。
3. 在同一個 Meta 應用程式加入「Instagram Graph API」，權限：`instagram_basic`、`instagram_content_publish`。
4. 取得 IG User ID：`GET /{page-id}?fields=instagram_business_account`。
5. 填入：
   ```
   INSTAGRAM_IG_USER_ID=<instagram_business_account.id>
   INSTAGRAM_ACCESS_TOKEN=<同 FB 的長效 Token（含 IG 權限）>
   ```
> 注意：IG 發文**必須附一張可公開存取的圖片**，系統會自動使用文章的第一張圖片素材。

## 3. Threads

1. 用上面的 Instagram 帳號開通 Threads（App 內一鍵開通，名稱沿用 IG）。
2. 前往 [Meta for Developers](https://developers.facebook.com/) → 應用程式加入產品「Threads API」。
3. 完成 OAuth 授權取得 Threads 專用 Access Token（權限：`threads_basic`、`threads_content_publish`），
   並換發長效 Token（60 天，可用 refresh 端點延長）。
4. 取得 Threads User ID：`GET https://graph.threads.net/v1.0/me?fields=id`。
5. 填入：
   ```
   THREADS_USER_ID=<id>
   THREADS_ACCESS_TOKEN=<長效 Token>
   ```

## 4. YouTube

1. 以 Google 帳號建立 YouTube 頻道（名稱：AI 前線觀測站）。
2. 前往 [Google Cloud Console](https://console.cloud.google.com/) → 建立專案 → 啟用 **YouTube Data API v3**。
3. 建立 OAuth 2.0 用戶端（類型：桌面應用程式），記下 Client ID / Client Secret。
4. 取得 Refresh Token（一次性，本機執行）：
   ```bash
   .venv/bin/python - <<'PY'
   from google_auth_oauthlib.flow import InstalledAppFlow
   flow = InstalledAppFlow.from_client_config(
       {"installed": {"client_id": "<CLIENT_ID>", "client_secret": "<CLIENT_SECRET>",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}},
       scopes=["https://www.googleapis.com/auth/youtube.upload"])
   creds = flow.run_local_server(port=0)
   print("REFRESH_TOKEN =", creds.refresh_token)
   PY
   ```
5. 填入：
   ```
   YOUTUBE_CLIENT_ID=...
   YOUTUBE_CLIENT_SECRET=...
   YOUTUBE_REFRESH_TOKEN=...
   ```
> 注意：YouTube 只能發布**影片**。文章需在媒體素材中有本機影片檔（`article_media.local_path`）
> 才能發布到 YouTube；純文字文章請選擇其他平台。

## 5. 自訂平台（Webhook）

後台「發布平台」頁可新增自訂平台：填入名稱、憑證前綴與 Webhook URL。
發布時系統會 `POST` JSON（title / content / url / attribution / media）到該 URL，
並以 `Authorization: Bearer {前綴}_TOKEN` 帶入憑證（若有設定）。

---

## 憑證安全原則（全系統強制）

- 憑證只存在 `~/.ai_news_hub/credentials`（chmod 600 的隱藏目錄檔案）。
- 程式以環境變數讀取，**不寫入資料庫、git 追蹤檔案或 log**。
- 權限過寬（非 600）時啟動會出現警告。
- Token 過期時發布工作會標記 `failed` 並記下原因，重新換發後在後台重新排程即可。
