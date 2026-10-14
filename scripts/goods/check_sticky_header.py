# -*- coding: utf-8 -*-
"""复核线上表格「表头是否固定在表体上方」（滚动时列名一直在）。

判据（DOM 层）：
  1. 表体容器 `.el-scrollbar__wrap` 有固定高度（不是跟随整页）—— 说明表头/表体分离；
  2. 表头 wrapper 的 position 是 sticky/absolute 或它不在同一个滚动容器里；
  3. 该表 el-table 上带了 height（`style` 里能看到 height）。

用法：python scripts/goods/check_sticky_header.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\AI-workplace\TEMU")
OUT = ROOT / "out"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PAGES = {
    "onsale-low": "https://mgltemu.us.ci/#/temu/on-sale?view=low",
    "onsale-all": "https://mgltemu.us.ci/#/temu/on-sale",
}


def dump(url: str, profile: str, budget: int = 25000, tries: int = 3) -> str:
    best = ""
    for i in range(tries):
        p = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
                            f"--user-data-dir={profile}-{i}", f"--virtual-time-budget={budget}",
                            "--dump-dom", url],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        out = p.stdout or ""
        if len(out) > len(best):
            best = out
        if len(out) > 30000:
            return out
    return best


def main() -> int:
    for name, url in PAGES.items():
        html = dump(url, str(OUT / f"_chrome_sticky_{name}"))
        (OUT / f"dom-{name}.html").write_text(html, encoding="utf-8")
        print(f"=== {name}  bytes={len(html)}")
        if len(html) <= 30000:
            print("   !! 只拿到空壳（未挂载），跳过")
            continue
        # el-table 的内联 height
        heights = re.findall(r'class="el-table[^"]*"[^>]*style="([^"]*height[^"]*)"', html)
        heights += re.findall(r'style="([^"]*height:\s*calc[^"]*)"', html)
        print("   el-table 上的 height 样式:", heights[:4] or "（没找到）")
        print("   表头容器数量:", html.count("el-table__header-wrapper"),
              "| 固定列层:", html.count("el-table__fixed"),
              "| 表体滚动容器:", html.count("el-scrollbar__wrap"))
        # 表头 wrapper 是否带 sticky/absolute
        hw = re.findall(r'class="el-table__header-wrapper"[^>]*style="([^"]*)"', html)
        print("   表头 wrapper 内联样式:", hw[:2] or "（无内联）")
        print("   是否出现 sticky:", "sticky" in html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
