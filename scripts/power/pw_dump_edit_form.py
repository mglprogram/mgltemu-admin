# -*- coding: utf-8 -*-
"""读订单编辑页的**全部字段**（含产品行），为「只改 Fnsku」做准备。

关键背景（2026-11-25 踩坑）：
  · 整表单回传 `edit_update` ⇒ 地址被写坏成 "0 0"（级联下拉静态解析是空/0）
  · 只传产品行             ⇒ 报「表内产品行字段解析空」
  ⇒ 必须搞清**哪些字段必须传、哪些不能传**。

本脚本：打印编辑页 form[name=myform] 里每个字段的原始 HTML 片段 + 值，
区分「文本输入 / 隐藏 / 下拉」。
"""
import html as _html
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

OID = sys.argv[1] if len(sys.argv) > 1 else ""


def main():
    if not OID:
        sys.exit("用法: pw_dump_edit_form.py <订单id>")
    pw = SS.PW()
    pw.login()
    r = pw.get("hwc_shop.php?act=edit_order&id=%s" % OID)
    t = r if isinstance(r, str) else str(r)
    print("页面长度:", len(t))

    m = re.search(r'<form[^>]*name="myform"[^>]*>(.*?)</form>', t, re.S | re.I)
    body = m.group(1) if m else t
    print("form 段长度:", len(body))

    print("\n=== 所有 input（含类型/名字/值）===")
    for tag in re.findall(r"<input[^>]*>", body, re.I):
        nm = re.search(r'name="([^"]*)"', tag)
        if not nm:
            continue
        name = nm.group(1)
        typ = (re.search(r'type="([^"]*)"', tag) or [None, "text"])[1]
        val = re.search(r'value="([^"]*)"', tag)
        v = _html.unescape(val.group(1)) if val else ""
        print("  %-16s %-8s %s" % (name, typ, v[:70]))

    print("\n=== select（名 / 选中值 / 选项数）===")
    for name, inner in re.findall(
            r'<select[^>]*name="([^"]*)"[^>]*>(.*?)</select>', body, re.S | re.I):
        opts = re.findall(r'<option[^>]*value="([^"]*)"', inner)
        sel = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', inner, re.I)
        print("  %-16s 选中=%-8s 选项=%d" % (name, sel.group(1) if sel else "?", len(opts)))

    print("\n=== 产品行附近原始 HTML（找 ck_sku / sku[] / huan_id） ===")
    for kw in ("ck_sku[]", "sku[]", "huan_id[]"):
        for mm in list(re.finditer(re.escape(kw), body))[:2]:
            i = mm.start()
            seg = re.sub(r"\s+", " ", body[max(0, i - 120):i + 200])
            print("\n--- %s @%d ---\n%s" % (kw, i, seg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
