# -*- coding: utf-8 -*-
"""查 Fnsku 的产品主档（`huan_id`）+ 状态 —— 判断「SKU:状态异常无法下单」的真因。

背景（2026-11-25）：
    把 ck_sku[] 从 BHY-YOGA-4SetA-L 换成 BS2-YOGA-4SetA-L 后，
    下单报「SKU:状态异常无法下单」。
⇒ 怀疑换上去的产品主档本身状态异常（未审核 / 未绑定店铺 / 被禁用），
  而不是库存问题。

做法：库存页 keywords 搜到该 Fnsku，读它那一行的**复选框 value（产品 id）**
与「库龄/预警/入仓时间」等状态列。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

KW = sys.argv[1] if len(sys.argv) > 1 else "YOGA-4SetA-L"


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def main():
    pw = SS.PW()
    pw.login()
    from urllib.parse import quote  # noqa: PLC0415
    t = pw.get("stock.php?act=list&keywords=%s" % quote(KW))
    t = t if isinstance(t, str) else str(t)

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
    head = None
    for tr in rows:
        cs = [clean(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
        if any("Fnsku" in c for c in cs):
            head = cs
            break
    print("表头:", head)

    print("\n=== 命中行（带 checkbox value = 产品id）===")
    for tr in rows:
        if KW.lower() not in tr.lower():
            continue
        m = re.search(r'name="checkboxes(?:\[\])?"[^>]*value="(\d+)"', tr) or \
            re.search(r'value="(\d+)"[^>]*name="checkboxes', tr)
        pid = m.group(1) if m else "(无)"
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        d = dict(zip(head or [], cells))
        print("\n  产品id=%s" % pid)
        for k in ("Fnsku", "Seller sku", "仓库名称", "代发库存", "库存预警",
                  "入仓时间", "库龄(天)", "10天销量"):
            if k in d:
                print("     %-10s %s" % (k, d[k]))

    # 原始片段：看 checkbox 到底长什么样
    print("\n=== 原始 checkbox 片段 ===")
    for m in list(re.finditer(r"<input[^>]*checkboxes[^>]*>", t))[:4]:
        print("  ", m.group(0)[:150])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
