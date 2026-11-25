# -*- coding: utf-8 -*-
"""换 Fnsku 的正确做法：**用编辑页的「选产品」功能**，而不是手改文本。

诊断结论（2026-11-25）：
    只改 `ck_sku[]` 文本 → 下单报「SKU:状态异常无法下单」
    因为 `huan_id[]`（产品主键）没跟着换 ⇒ 两者对不上。

正确链路应该和页面「编辑」一样：从产品库选一个新 Fnsku，前端自动带出
  `huan_id[]` + `ck_sku[]` + `pname[]` 三件套。

本脚本：挖编辑页里「选产品 / 搜产品」的 JS（`get_product` / `select_product` 等），
找到那个接口就能拿到正确的 huan_id。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

OID = sys.argv[1] if len(sys.argv) > 1 else "3014222"


def main():
    pw = SS.PW()
    pw.login()
    r = pw.get("hwc_shop.php?act=edit_order&id=%s" % OID)
    t = r if isinstance(r, str) else str(r)
    print("页面长度:", len(t))

    # 所有函数名
    fns = sorted(set(re.findall(r"function\s+([a-z_0-9]+)\s*\(", t, re.I)))
    print("\n页面里的函数（%d 个）：" % len(fns))
    print("  ", fns)

    # 找产品/选品相关的
    print("\n=== 产品/选品相关函数 ===")
    for fn in fns:
        if not re.search(r"prod|sku|huan|select|search|chose|choo", fn, re.I):
            continue
        m = re.search(r"function\s+%s\s*\([^)]*\)\s*\{" % re.escape(fn), t, re.S)
        if not m:
            continue
        i = m.start()
        seg = t[i:i + 900]
        print("\n--- %s ---\n%s" % (fn, re.sub(r"\s+", " ", seg)[:600]))

    # 找 ajax 里带 prod/sku/huan 的
    print("\n=== ajax 里与产品有关的 ===")
    for m in list(re.finditer(r"\$\.(post|ajax|get)\s*\(", t))[:20]:
        i = m.start()
        seg = re.sub(r"\s+", " ", t[i:i + 300])
        if re.search(r"prod|sku|huan|goods", seg, re.I):
            print("  •", seg[:260])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
