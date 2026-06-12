"""資料庫連線與 ORM 模型。

連線憑證由 ~/.my.cnf 選項檔提供（[client] 區段），
程式碼與設定檔中不出現任何明文密碼。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer,
    String, Text, create_engine, func,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import DB_NAME, MY_CNF

engine = create_engine(
    "mysql+pymysql://",
    connect_args={
        "read_default_file": MY_CNF,
        "database": DB_NAME,
        "charset": "utf8mb4",
    },
    pool_pre_ping=True,
    pool_recycle=3600,
)
Session = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class SourceSite(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(1000))
    type: Mapped[str] = mapped_column(Enum("rss", "html", "arxiv_api"), default="rss")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    articles: Mapped[list["Article"]] = relationship(back_populates="source")


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(1000))
    title_zh: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    url: Mapped[str] = mapped_column(String(1000))
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    authors: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    content_original: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)
    content_zh: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)
    content_research: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)
    is_fulltext: Mapped[bool] = mapped_column(Boolean, default=False)
    fetch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    content_format: Mapped[str] = mapped_column(Enum("md", "txt"), default="md")
    attribution: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(
        Enum("draft", "translated", "online", "offline"), default="draft"
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    source: Mapped[SourceSite | None] = relationship(back_populates="articles")
    media: Mapped[list["ArticleMedia"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["PublishJob"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class ArticleMedia(Base):
    __tablename__ = "article_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"))
    media_type: Mapped[str] = mapped_column(Enum("image", "video", "audio", "pdf"))
    url: Mapped[str] = mapped_column(String(1000))
    local_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    attribution: Mapped[str] = mapped_column(String(1000), default="")
    variant: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    article: Mapped[Article] = relationship(back_populates="media")


class Platform(Base):
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(50))
    credential_key: Mapped[str] = mapped_column(String(100), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    jobs: Mapped[list["PublishJob"]] = relationship(back_populates="platform")


class PublishJob(Base):
    __tablename__ = "publish_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"))
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    scheduled_at: Mapped[dt.datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(
        Enum("pending", "processing", "done", "failed", "canceled"), default="pending"
    )
    result_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    posted_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    executed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    article: Mapped[Article] = relationship(back_populates="jobs")
    platform: Mapped[Platform] = relationship(back_populates="jobs")
