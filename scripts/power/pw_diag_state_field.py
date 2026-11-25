# -*- coding: utf-8 -*-
"""诊断「州0不存在」——`edit_update` 把地址的州/城市清掉了。

现象（2026-11-25 实测，订单 3013984）：
    换 Fnsku 后 edit_update 返回 `error=0 操作成功`，
    但再下单代发报 **「州0不存在」** ⇒ 地址字段被写坏。

原因是编辑页的地址字段是**联动下拉**：
    country=美国（中文） / city_name=Georgia(GA) / city=17 / district=SPRINGFIELD
我按页面解析值原样回传，但 `city=17` 这种内部 id 与 `city_name` 的显示文本
可能对不上，后端就判「州不存在」。

本脚本（只读）：打印编辑页里**所有地址相关字段**的原始值 + 完整 select 选项，
找出正确的「州」字段名与取值方式。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

OID = sys.argv[1] if len(sys.argv) > 1 else "3013984"


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", c).strip()


def main():
    pw = SS.PW()
    pw.login()
    r = pw.get("hwc_shop.php?act=edit_order&id=%s" % OID)
    t = r if isinstance(r, str) else str(r)
    print("页面长度:", len(t))

    print("\n=== 地址相关 input ===")
    for tag in re.findall(r"<input[^>]*>", t, re.I):
        nm = re.search(r'name="([^"]*)"', tag)
        if not nm:
            continue
        n = nm.group(1)
        if not re.search(r"country|city|district|state|province|zip|address|contact|mobile",
                         n, re.I):
            continue
        print("  %-14s %s" % (n, tag[:150]))

    print("\n=== 地址相关 select（含选项） ===")
    for m in re.finditer(
            r'<select[^>]*name="([^"]*(?:country|city|district|state|province)[^"]*)"[^>]*>(.*?)</select>',
            t, re.S | re.I):
        name, inner = m.group(1), m.group(2)
        opts = re.findall(r"<option[^>]*value=\"([^\"]*)\"[^>]*>([^<]*)</option>", inner)
        sel = re.search(r"<option[^>]*value=\"([^\"]*)\"[^>]*selected", inner, re.I)
        print("  %-14s 选中=%s | 选项数=%d"
              % (name, sel.group(1) if sel else "?", len(opts)))
        for v, txt in opts[:8]:
            print("        %-8s %s" % (v, clean(txt)[:40]))

    print("\n=== 隐藏的城市/州 id 字段 ===")
    for n in ("city", "district", "country", "city_name", "th_city", "th_district"):
        vals = re.findall(r'name="%s"[^>]*value="([^"]*)"' % re.escape(n), t)
        print("  %-12s %s" % (n, vals[:3]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
