# -*- coding: utf-8 -*-
"""扩大搜索：从 16 个 script 外再扫所有已加载的 JS chunk（Next/webpack 按需加载）。

上一版只看了 `script[src]`（16 个）→ 0 命中，说明确认发货的代码在**按需 chunk** 里。
这里改成：
  ① 抓 performance.getEntriesByType('resource') 里所有 .js
  ② 逐个 fetch 搜关键字
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402

JS = r"""(async () => {
  const res = performance.getEntriesByType('resource')
    .map(e => e.name).filter(u => u.endsWith('.js'));
  const uniq = [...new Set(res)];
  const hits = [];
  let scanned = 0;
  for (const u of uniq) {
    try {
      const r = await fetch(u, {credentials: 'include'});
      const t = await r.text();
      scanned++;
      if (/confirmShipping|batchConfirm|confirmDeliver|prepareShip/i.test(t)) {
        const re = /["'`\/]((?:kirogi|mms)[^"'`]{0,80}(?:confirmShipping|batchConfirm|confirmDeliver)[^"'`]{0,40})["'`]/gi;
        let m;
        while ((m = re.exec(t)) && hits.length < 80) hits.push(m[1]);
      }
    } catch (e) {}
  }
  return JSON.stringify({js: uniq.length, scanned, hits: [...new Set(hits)]});
})()"""


async def main():
    ws_url, url = A._page_ws("BHY")
    print("页面:", url[:90])
    out = await A._ev(ws_url, JS)
    try:
        d = json.loads(out or "{}")
    except Exception:  # noqa: BLE001
        print("解析失败:", str(out)[:250])
        return 1
    print("JS 资源 %s 个，扫描 %s 个" % (d.get("js"), d.get("scanned")))
    hits = d.get("hits") or []
    print("命中 %d 条：" % len(hits))
    for h in hits:
        print("   ", h)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
