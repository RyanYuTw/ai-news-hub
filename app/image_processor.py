"""圖片處理模組：從 PDF 第一頁或現有圖片生成三種標準尺寸圖片。

三種規格：
  square   (1:1)  1080 × 1080  — IG 個人主頁整齊排列
  portrait (4:5)  1080 × 1350  — IG 貼文推薦比例
  landscape(16:9) 1920 × 1080  — 橫式，底部加論文標題遮罩

來源優先順序：
  1. 文章既有的 HTTP 圖片（原始圖片，非 variant 生成圖）
  2. PDF 第一頁截圖

生成的圖片以 JPEG 儲存於 media_files/，local_path 記錄絕對路徑，
url 為 /media/<filename>（供管理介面預覽），variant 欄位記錄尺寸規格。
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from .config import MEDIA_DIR
from .db import Article, ArticleMedia, Session

log = logging.getLogger("image_processor")

VARIANTS: dict[str, tuple[int, int]] = {
    "1080x1080": (1080, 1080),
    "1080x1350": (1080, 1350),
    "1920x1080": (1920, 1080),
}

_FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/ArialHB.ttc",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """字元逐一測量，中英文混合皆適用。"""
    lines: list[str] = []
    current = ""
    for char in text:
        test = current + char
        if draw.textbbox((0, 0), test, font=font)[2] > max_width and current:
            lines.append(current)
            current = char
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def _add_title_overlay(img: Image.Image, title: str) -> Image.Image:
    """在 1920×1080 圖片底部疊加漸層遮罩與論文標題。"""
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 底部漸層遮罩（高度約 40%）
    overlay_h = int(h * 0.42)
    for y in range(overlay_h):
        alpha = int(220 * (y / overlay_h) ** 0.55)
        draw.line([(0, h - overlay_h + y), (w, h - overlay_h + y)],
                  fill=(0, 0, 0, alpha))

    # 字型與換行
    font_size = 58
    font = _load_font(font_size)
    max_text_w = int(w * 0.82)
    lines = _wrap_text(draw, title, font, max_text_w)

    line_h = font_size + 16
    total_text_h = len(lines) * line_h
    y = h - 72 - total_text_h  # 距底部 72px

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (w - (bbox[2] - bbox[0])) // 2
        # 細字陰影增加可讀性
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 160))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 245))
        y += line_h

    combined = Image.alpha_composite(img.convert("RGBA"), overlay)
    return combined.convert("RGB")


def _center_crop_resize(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_ratio = img.width / img.height
    dst_ratio = target_w / target_h
    if src_ratio > dst_ratio:
        new_w = int(img.height * dst_ratio)
        left = (img.width - new_w) // 2
        img = img.crop((left, 0, left + new_w, img.height))
    else:
        new_h = int(img.width / dst_ratio)
        top = (img.height - new_h) // 2
        img = img.crop((0, top, img.width, top + new_h))
    return img.resize((target_w, target_h), Image.LANCZOS)


def _pdf_first_page(pdf_path: str) -> Image.Image:
    import fitz  # pymupdf

    doc = fitz.open(pdf_path)
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csRGB)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def _download_image(url: str) -> Image.Image:
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def _source_image(article: Article, pdf_path: str | None) -> tuple[Image.Image, str] | None:
    """取得來源圖片與來源標註。優先用既有 HTTP 圖片，否則用 PDF 第一頁。"""
    existing = next(
        (m for m in article.media
         if m.media_type == "image" and not m.variant and m.url.startswith("http")),
        None,
    )
    if existing:
        try:
            img = _download_image(existing.url)
            log.info("使用現有圖片：%s", existing.url)
            return img, existing.attribution
        except Exception as exc:
            log.warning("下載現有圖片失敗，改用 PDF：%s", exc)

    _pdf = pdf_path
    if _pdf is None:
        pdf_media = next(
            (m for m in article.media if m.media_type == "pdf" and m.local_path),
            None,
        )
        _pdf = pdf_media.local_path if pdf_media else None

    if _pdf and Path(_pdf).exists():
        try:
            img = _pdf_first_page(_pdf)
            log.info("從 PDF 截取第一頁：%s", _pdf)
            return img, "PDF 第一頁截圖"
        except Exception as exc:
            log.warning("PDF 截圖失敗：%s", exc)

    return None


def generate_article_images(article_id: int, pdf_path: str | None = None) -> int:
    """為文章生成三種標準尺寸圖片，回傳已建立的記錄數。"""
    with Session() as session:
        article = session.get(Article, article_id)
        if not article:
            return 0

        result = _source_image(article, pdf_path)
        if result is None:
            log.info("文章 #%s 無可用圖片來源，跳過", article_id)
            return 0
        source_img, source_attr = result

        title = article.title_zh or article.title

        # 移除舊的 variant 圖片記錄（重新生成時清理）
        for m in list(article.media):
            if m.media_type == "image" and m.variant:
                if m.local_path:
                    Path(m.local_path).unlink(missing_ok=True)
                session.delete(m)

        count = 0
        for variant, (w, h) in VARIANTS.items():
            try:
                img = _center_crop_resize(source_img.copy(), w, h)
                if variant == "1920x1080":
                    img = _add_title_overlay(img, title)
                filename = f"{article.url_hash}_{variant}.jpg"
                save_path = MEDIA_DIR / filename
                img.save(save_path, "JPEG", quality=90, optimize=True)

                session.add(ArticleMedia(
                    article_id=article_id,
                    media_type="image",
                    url=f"/media/{filename}",
                    local_path=str(save_path),
                    attribution=source_attr,
                    variant=variant,
                ))
                count += 1
                log.info("已生成圖片 %s（%dx%d）", filename, w, h)
            except Exception as exc:
                log.error("生成 %s 圖片失敗：%s", variant, exc)

        session.commit()
        log.info("文章 #%s 共生成 %d 張圖片", article_id, count)
        return count
