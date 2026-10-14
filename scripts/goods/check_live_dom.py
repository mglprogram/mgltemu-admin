# -*- coding: utf-8 -*-
"""用 Chrome --dump-dom 复核线上页面（渲染层验证，不依赖 CDP 连接）。

用法：
    python scripts/goods/check_live_dom.py            # 两个路由都查
    python scripts/goods/check_live_dom.py 断货尺码 低库存款·尺码
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
    "onsale-low": ("https://mgltemu.us.ci/#/temu/on-sale?view=low",
                   ["断货尺码", "低库存款·尺码", "SPU ID", "货号 / 款号", "BEL-"]),
    "dashboard": ("https://mgltemu.us.ci/#/dashboard",
                  ["在售款 · 款-库存提醒", "个款·尺码待补货", "个已断货"]),
}


def dump(url: str, profile: str, budget: int = 25000, tries: int = 3) -> str:
    """Chrome --dump-dom 取渲染后的 DOM。

    ⚠ 偶发只回**未挂载的空壳**（约 10KB，只有加载动画）：多半是复用同一个
    `--user-data-dir` 时上一次的 Chrome 还没退干净（profile 被占），这次导航就没跑起来。
    ⇒ 每次换一个带序号的 profile 目录，并对「空壳」重试。
    """
    best = ""
    for i in range(max(1, tries)):
        p = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
                            f"--user-data-dir={profile}-{i}", f"--virtual-time-budget={budget}",
                            "--dump-dom", url],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        out = p.stdout or ""
        if len(out) > len(best):
            best = out
        if len(out) > 30000:          # 挂载成功（SPA 渲染后 DOM 明显更大）
            return out
    return best


def text_of(html: str) -> str:
    no_script = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", no_script))


def main() -> int:
    wanted = sys.argv[1:]
    bad = 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (url, keys) in PAGES.items():
        html = dump(url, str(OUT / f"_chrome_{name}"))
        (OUT / f"dom-{name}.html").write_text(html, encoding="utf-8")
        txt = text_of(html)
        print(f"=== {name}  ({url})  bytes={len(html)}")
        for k in (wanted or keys):
            hit = k in txt or k in html
            print(f"    {'OK ' if hit else 'MISS'}  {k}")
            if not hit:
                bad += 1
        # 抽一小段低库存/卡片文本，便于人工核对
        for anchor in ("低库存款·尺码（库存", "款·尺码待补货"):
            i = txt.find(anchor)
            if i >= 0:
                print("    片段:", txt[max(0, i - 40): i + 200])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
