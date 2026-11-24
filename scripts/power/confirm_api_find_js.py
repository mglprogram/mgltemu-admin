# -*- coding: utf-8 -*-
"""在页面 JS 里找「确认发货」的**写接口路径**（不点按钮、无副作用）。

思路：
  ① 已在用的只读接口叫 `/kirogi/bg/mms/confirmShippingList`
  ② 同族写接口一定在打包后的 JS 里，用字符串搜 `confirmShipping` / `batchConfirm`
  ③ 把命中的路径片段打印出来
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402

JS = r"""(async () => {
  // 1) 找页面加载的所有 script src
  const srcs = [...document.querySelectorAll('script[src]')].map(s => s.src);
  const hits = [];
  for (const u of srcs.slice(0, 60)) {
    try {
      const r = await fetch(u, {credentials: 'include'});
      const t = await r.text();
      if (!/confirmShipping|batchConfirm|confirmDeliver/i.test(t)) continue;
      const re = /["'`]([^"'`]{0,80}(?:confirmShipping|batchConfirm|confirmDeliver)[^"'`]{0,60})["'`]/gi;
      let m;
      while ((m = re.exec(t)) && hits.length < 60) hits.push(m[1]);
    } catch (e) {}
  }
  return JSON.stringify({scripts: srcs.length, hits: [...new Set(hits)]});
})()"""


async def main():
    ws_url, url = A._page_ws("BHY")
    print("页面:", url[:100])
    out = await A._ev(ws_url, JS)
    try:
        d = json.loads(out or "{}")
    except Exception:  # noqa: BLE001
        print("解析失败:", str(out)[:300])
        return 1
    print("扫描 script:", d.get("scripts"))
    hits = d.get("hits") or []
    print("命中 %d 条：" % len(hits))
    for h in hits:
        print("   ", h)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
