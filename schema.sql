-- AI News Hub 資料庫 Schema
-- 僅建立本系統專用資料庫 ai_news_hub，不觸碰其他資料庫

CREATE DATABASE IF NOT EXISTS ai_news_hub
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE ai_news_hub;

-- 蒐集來源（預設 + 自訂）
CREATE TABLE IF NOT EXISTS sources (
  id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
  name          VARCHAR(255) NOT NULL,
  url           VARCHAR(1000) NOT NULL,
  type          ENUM('rss','html','arxiv_api') NOT NULL DEFAULT 'rss',
  is_default    TINYINT(1) NOT NULL DEFAULT 0,
  enabled       TINYINT(1) NOT NULL DEFAULT 1,
  last_fetched_at DATETIME NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_sources_url (url(255)),
  KEY idx_sources_enabled (enabled)
) ENGINE=InnoDB;

-- 文章
CREATE TABLE IF NOT EXISTS articles (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_id        INT UNSIGNED NULL,
  title            VARCHAR(1000) NOT NULL,
  title_zh         VARCHAR(1000) NULL,
  url              VARCHAR(1000) NOT NULL,
  url_hash         CHAR(64) NOT NULL,                -- sha256(url) 去重
  doi              VARCHAR(255) NULL,
  authors          VARCHAR(2000) NULL,
  published_at     DATETIME NULL,
  content_original LONGTEXT NULL,                    -- 原文（全文或摘要）
  content_zh       LONGTEXT NULL,                    -- 繁中翻譯（社群貼文）
  content_research LONGTEXT NULL,                    -- 繁中學術研究筆記（詳細版）
  is_fulltext      TINYINT(1) NOT NULL DEFAULT 0,    -- 1=全文 0=摘要
  fetch_attempts   TINYINT UNSIGNED NOT NULL DEFAULT 0,
  content_format   ENUM('md','txt') NOT NULL DEFAULT 'md',
  attribution      VARCHAR(1000) NOT NULL DEFAULT '',-- 資料來源標註
  status           ENUM('draft','translated','online','offline') NOT NULL DEFAULT 'draft',
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_articles_url_hash (url_hash),
  KEY idx_articles_status_created (status, created_at),
  KEY idx_articles_source (source_id),
  CONSTRAINT fk_articles_source FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- 文章附帶媒體（圖片/PDF，含來源標註）
-- local_path：PDF 存直接下載 URL（供 NotebookLM --url 使用）；翻譯後清空
-- gdrive_file_id：圖片上傳至 Google Drive 後的檔案 ID；文章上架或刪除後清空
CREATE TABLE IF NOT EXISTS article_media (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id     BIGINT UNSIGNED NOT NULL,
  media_type     ENUM('image','video','audio','pdf') NOT NULL,
  url            VARCHAR(1000) NOT NULL,             -- PDF：原始 viewer 網址；圖片：Drive 公開連結
  local_path     VARCHAR(1000) NULL,                 -- PDF：直接下載 URL；翻譯後清空
  gdrive_file_id VARCHAR(500)  NULL,                 -- 圖片 Drive 檔案 ID；上架後清空
  attribution    VARCHAR(1000) NOT NULL DEFAULT '',
  variant        VARCHAR(50)   NULL,                 -- 圖片規格：1080x1080 / 1080x1350 / 1920x1080
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_media_article (article_id),
  CONSTRAINT fk_media_article FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 預設蒐集來源
INSERT INTO sources (name, url, type, is_default) VALUES
  ('arXiv cs.AI', 'http://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=50', 'arxiv_api', 1),
  ('Nature Machine Intelligence', 'https://www.nature.com/natmachintell.rss', 'rss', 1),
  ('JAIR', 'https://jair.org/index.php/jair/gateway/plugin/WebFeedGatewayPlugin/rss2', 'rss', 1),
  ('Artificial Intelligence Review (Springer)', 'https://link.springer.com/search.rss?query=&search-within=Journal&facet-journal-id=10462', 'rss', 1)
ON DUPLICATE KEY UPDATE name = VALUES(name);
