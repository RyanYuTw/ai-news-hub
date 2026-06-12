"""CLI：手動執行一次蒐集（+ 翻譯）。

用法：
  python collect.py               # 蒐集所有啟用來源
  python collect.py --translate   # 蒐集後翻譯待處理文章
  python collect.py --limit 5     # 每個來源最多抓 5 篇
"""
import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

from app.collectors import collect_all  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--translate", action="store_true", help="蒐集後翻譯")
    parser.add_argument("--limit", type=int, default=30, help="每來源最多篇數")
    args = parser.parse_args()

    results = collect_all(limit_per_source=args.limit)
    for name, count in results.items():
        print(f"{name}: {'失敗' if count < 0 else f'新增 {count} 篇'}")

    if args.translate:
        from app.translator import translate_pending

        done = translate_pending()
        print(f"已翻譯 {done} 篇")


if __name__ == "__main__":
    main()
