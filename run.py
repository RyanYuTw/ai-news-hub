"""啟動 Web 管理介面 + 背景排程器。

用法：
  python run.py             # http://127.0.0.1:5000
  python run.py --no-sched  # 只跑 Web，不啟動排程
"""
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

from app.web import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    if "--no-sched" not in sys.argv:
        from app.scheduler import start_scheduler

        start_scheduler()
    port = int(os.environ.get("PORT", 5001))
    app.run(host="127.0.0.1", port=port, debug=False)
