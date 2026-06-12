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
  content_zh       LONGTEXT NULL,                    -- 繁中翻譯（可發布內容）
  content_research LONGTEXT NULL,                    -- 繁中學術研究彙整（詳細版）
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

-- 文章附帶媒體（圖片/影音，含來源標註）
CREATE TABLE IF NOT EXISTS article_media (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id   BIGINT UNSIGNED NOT NULL,
  media_type   ENUM('image','video','audio','pdf') NOT NULL,
  url          VARCHAR(1000) NOT NULL,
  local_path   VARCHAR(1000) NULL,
  attribution  VARCHAR(1000) NOT NULL DEFAULT '',
  variant      VARCHAR(50)  NULL,                       -- 圖片規格：1080x1080 / 1080x1350 / 1920x1080
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_media_article (article_id),
  CONSTRAINT fk_media_article FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 發布平台（內建 + 自訂）
CREATE TABLE IF NOT EXISTS platforms (
  id             INT UNSIGNED NOT NULL AUTO_INCREMENT,
  name           VARCHAR(255) NOT NULL,               -- 顯示名稱（粉專名稱）
  type           VARCHAR(50)  NOT NULL,               -- facebook/instagram/threads/youtube/custom
  credential_key VARCHAR(100) NOT NULL,               -- 隱藏檔內憑證變數前綴
  enabled        TINYINT(1) NOT NULL DEFAULT 1,
  config         JSON NULL,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_platforms_credkey (credential_key)
) ENGINE=InnoDB;

-- 發布排程
CREATE TABLE IF NOT EXISTS publish_jobs (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id    BIGINT UNSIGNED NOT NULL,
  platform_id   INT UNSIGNED NOT NULL,
  scheduled_at  DATETIME NOT NULL,
  status        ENUM('pending','processing','done','failed','canceled') NOT NULL DEFAULT 'pending',
  result_message VARCHAR(2000) NULL,
  posted_url    VARCHAR(1000) NULL,
  executed_at   DATETIME NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_jobs_due (status, scheduled_at),
  KEY idx_jobs_article (article_id),
  CONSTRAINT fk_jobs_article FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
  CONSTRAINT fk_jobs_platform FOREIGN KEY (platform_id) REFERENCES platforms(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 預設蒐集來源
INSERT INTO sources (name, url, type, is_default) VALUES
  ('arXiv cs.AI', 'http://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=50', 'arxiv_api', 1),
  ('Nature Machine Intelligence', 'https://www.nature.com/natmachintell.rss', 'rss', 1),
  ('JAIR', 'https://jair.org/index.php/jair/gateway/plugin/WebFeedGatewayPlugin/rss2', 'rss', 1),
  ('Artificial Intelligence Review (Springer)', 'https://link.springer.com/search.rss?query=&search-within=Journal&facet-journal-id=10462', 'rss', 1)
ON DUPLICATE KEY UPDATE name = VALUES(name);

-- 預設發布平台（粉專名稱：AI 前線觀測站）
INSERT INTO platforms (name, type, credential_key, enabled) VALUES
  ('AI 前線觀測站', 'facebook',  'FACEBOOK',  0),
  ('AI 前線觀測站', 'instagram', 'INSTAGRAM', 0),
  ('AI 前線觀測站', 'threads',   'THREADS',   0),
  ('AI 前線觀測站', 'youtube',   'YOUTUBE',   0)
ON DUPLICATE KEY UPDATE name = VALUES(name);
