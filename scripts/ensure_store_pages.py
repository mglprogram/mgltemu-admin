# -*- coding: utf-8 -*-
"""ensure_store_pages.py — CLI 入口：巡检并补齐 TEMU 店的默认页面。

真正的实现在 `scripts/power/lib/store_pages.py`（判据单点）。这里只做参数解析。

用法：
    python scripts/ensure_store_pages.py                       # 两店巡检并补齐
    python scripts/ensure_store_pages.py --stores BHY --dry-run
    python scripts/ensure_store_pages.py --json
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "power", "lib"))

import store_pages  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(store_pages.main())
