# -*- coding: utf-8 -*-
"""BEL 403 深挖：看页面**自己**渲染出了什么（列表有没有数据 / 有没有报错弹窗）。

用户问的是「手动打开能不能看到订单」，我这边直接读页面状态就能回答：
  · innerText 里有没有「PO-211-」「共 N 个订单」这类列表痕迹
  · 有没有「No Permission」/「无权限」/「重新登录」之类的提示
  · 表格行数
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402

JS = r"""(() => {
  const t = document.body ? (document.body.innerText || '') : '';
  const rows = document.querySelectorAll('tr').length;
  const po = (t.match(/PO-\d+-\d+/g) || []).slice(0, 5);
  const cnt = (t.match(/共\s*\d+\s*个订单|共\s*\d+\s*条/g) || []).slice(0, 3);
  const bad = ['No Permission', '无权限', '没有权限', '请重新登录', '登录已过期',
               '授权', '验证码'].filter((k) => t.indexOf(k) >= 0);
  return JSON.stringify({
    url: location.href, title: document.title, tr: rows,
    po: po, counts: cnt, warn: bad,
    head: t.replace(/\s+/g, ' ').slice(0, 320),
  });
})()"""


async def main():
    store = sys.argv[1] if len(sys.argv) > 1 else "BEL"
    ws_url, url = A._page_ws(store)
    print("页面:", url[:100])
    raw = await A._ev(ws_url, JS)
    d = json.loads(raw or "{}")
    print("标题:", d.get("title"))
    print("表格行:", d.get("tr"))
    print("订单号样本:", d.get("po"))
    print("计数文本:", d.get("counts"))
    print("警示词:", d.get("warn"))
    print("正文前 320:", d.get("head"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
