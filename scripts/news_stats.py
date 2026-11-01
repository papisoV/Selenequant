"""news-ingest 状态/统计打印(空库友好,便于日常巡检)。

用法: call activate.bat && python scripts/news_stats.py [days]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "packages" / "news-ingest")
)

from selene_space_news_ingest.news_store import get_news_store  # noqa: E402


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    store = get_news_store()
    print("=== news status (3 后台任务健康账本) ===")
    print(json.dumps(store.get_status(), ensure_ascii=False, indent=2))
    print(f"\n=== news stats (high+breaking, past {days}d) ===")
    print(json.dumps(store.compute_stats(days=days), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
