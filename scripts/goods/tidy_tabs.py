# -*- coding: utf-8 -*-
"""关掉窗口里**重复**的同类页面（只留一个），用于清理自己测试留下的标签。

用法：python scripts/goods/tidy_tabs.py BHY [--dry-run]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_net  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}


async def main() -> int:
    store = (sys.argv[1] if len(sys.argv) > 1 else "BHY").upper()
    dry = "--dry-run" in sys.argv
    port = probe_port(WINDOWS[store])
    if not port:
        print("!! 探不到端口")
        return 1
    tl = json.loads(urlopen("http://127.0.0.1:%d/json/list" % port, timeout=8)
                    .read().decode("utf-8", "replace"))
    pages = [t for t in tl if t.get("type") == "page"]
    seen: dict[str, str] = {}
    to_close = []
    for t in pages:
        title = (t.get("title") or "").strip()[:20]
        if title and title in seen:
            to_close.append(t)
        else:
            seen[title] = t.get("id")
    print("%s: 共 %d 个标签，其中重复 %d 个" % (store, len(pages), len(to_close)))
    for t in to_close:
        print("   关闭重复:", (t.get("title") or "")[:24], t.get("url", "")[:50])
    if dry or not to_close:
        print("（%s）" % ("dry-run，未关" if dry else "没有重复，无需处理"))
        return 0
    for t in to_close:
        await cdp_net.close_tab(port, t.get("id"))
    print("已关闭 %d 个重复标签" % len(to_close))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
