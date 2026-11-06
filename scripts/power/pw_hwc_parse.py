# -*- coding: utf-8 -*-
"""pw_hwc_parse.py — hwc_shop.php「店铺 → 全部订单」列表解析 + 只读查询。

用户口径（2026-11-06，重要纠正）：
    帕沃尔的操作页面统一是
    https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1&uselastfilter=1
    （不是 deliver.php 那套代发页）。

本模块只用「标准库 + pw_session」，走 `hwc_shop.php?is_ajax=1` 的
`act=query_shop_order` 接口（= 页面上「查询」按钮的同一个请求），
**只读**，不点按钮、不触发任何下单动作。

返回每单的关键字段：
    id          订单主键（hwc_shop_order.id，下单代发用）
    po          参考单号（deliver_no）
    status      状态列（未下单 / 已下单 / 下单失败 …）
    err         下单错误提醒列
    waybill     运单号（状态列后面的 waybill_<id> 单元格）
    wh          发货仓库
    qd          出货渠道
    sku         平台SKU（可能多行）
    name        产品名称
    addtime     添加时间
    shop_id / shop_name
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "pw_orders", "lib"))
import pw_session as S  # noqa: E402

REF = "hwc_shop.php?act=shop_order&pb=1&uselastfilter=1"
AJAX = "hwc_shop.php?is_ajax=1"

#: 运单号形态（用户给的四种 + 常见承运商前缀）
WB_RE = re.compile(r"\b(1Z[0-9A-Z]{10,}|GFUS[0-9A-Z]{6,}|SPX[0-9A-Z]{6,}|SWX[0-9A-Z]{6,}"
                   r"|YWE[0-9A-Z]{6,}|GFUS[0-9A-Z]+|\d{12,22})\b")


def _txt(s: str) -> str:
    """去标签、折叠空白。"""
    s = re.sub(r"<br\s*/?>", " ", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"[ \t\r\n\u00a0]+", " ", s).strip()


def _cell_after(seg: str, label: str) -> str:
    """取「<span>label</span> :xxx」里的 xxx。"""
    m = re.search(re.escape(label) + r"</span>\s*:([^<]*)<", seg)
    return (m.group(1).strip() if m else "")


def parse_rows(html: str) -> list:
    """把 query_shop_order 返回的 content 拆成订单 dict 列表。"""
    out = []
    # 页面里保留大量被注释掉的旧列（区域ID/站点ID 那个 td），先整体剥掉注释，
    # 否则「状态列」会取到注释里的假 td（实测踩过：状态变成「区域ID:211 站点ID:100」）。
    html = re.sub(r"<!--.*?-->", "", html or "", flags=re.S)
    # 每条 <tr class="tr_html_<id> ..."> 是一个订单；用下一个 tr_html_ 或 </table> 结尾
    starts = [m for m in re.finditer(r'<tr class="tr_html_(\d+)', html)]
    for i, m in enumerate(starts):
        oid = m.group(1)
        end = starts[i + 1].start() if i + 1 < len(starts) else len(html)
        seg = html[m.start():end]

        # 参考单号
        po = _cell_after_id(seg, "deliver_no_", oid)
        # 「下单错误提醒」列的 td 就是 class="waybill_<id>" 那个；它前面那个 td 才是「状态」列。
        # （列顺序：序号/参考单号/店铺详情/收货地址/发货信息/产品名称sku/数量/状态/下单错误提醒/添加时间/操作）
        wb_td = re.search(r'<td[^>]*class="waybill_' + oid + r'"[^>]*>(.*?)</td>', seg, re.S)
        wb_raw = _txt(wb_td.group(1)) if wb_td else ""
        status = ""
        if wb_td:
            before = seg[:wb_td.start()]
            prev_end = before.rfind("</td>")
            prev_start = before.rfind("<td", 0, prev_end if prev_end > 0 else len(before))
            if prev_end > 0 and prev_start >= 0:
                status = _txt(before[prev_start:prev_end + 5])
        if not status:
            mm = re.search(r"(未下单|下单失败|已下单|已发货|已出库|已取消|取消中)", seg)
            status = mm.group(1) if mm else ""

        skus = re.findall(r'平台SKU</span>\s*:([^<]+)<', seg)
        names = re.findall(r'产品名称</span>\s*:([^<]+)<', seg)

        pm = re.search(r'\[编辑\]</a>', seg)  # 仅为定位，无副作用
        out.append({
            "id": oid,
            "po": po,
            "status": status,
            "waybill": wb_raw,
            "wb": (WB_RE.search(wb_raw).group(1) if WB_RE.search(wb_raw) else ""),
            "err": _err(seg),
            "wh": _cell_after(seg, "发货仓库"),
            "qd": _cell_after(seg, "出货渠道"),
            "sku": " / ".join(x.strip() for x in skus),
            "name": (names[0].strip()[:60] if names else ""),
            "shop_id": (_cell_after(seg, "店铺ID")),
            "shop_name": (_cell_after(seg, "店铺名称")),
            "addtime": _addtime(seg),
        })
    return out


def _cell_after_id(seg: str, cls_prefix: str, oid: str) -> str:
    m = re.search(r'class="' + cls_prefix + oid + r'"[^>]*>([^<]*)<', seg)
    return (m.group(1).strip() if m else "")


def _err(seg: str) -> str:
    """「下单错误提醒」列就是 class="waybill_<id>" 的 td。"""
    m = re.search(r'class="waybill_\d+"[^>]*>(.*?)</td>', seg, re.S)
    if not m:
        return ""
    t = _txt(m.group(1))
    return "" if t in ("", "-", "&nbsp;") else t


def _addtime(seg: str) -> str:
    m = re.findall(r">\s*(20\d\d-\d\d-\d\d)\s*(?:<br\s*/?>)?\s*(\d\d:\d\d:\d\d)?", seg)
    if not m:
        return ""
    d, t = m[-1]
    return (d + " " + (t or "")).strip()


def query(pw: S.PW, pos: list, shop_id: str = "", start: str = "2026-10-01",
          end: str = "2026-11-30", page: int = 1, page_size: int = 300,
          status: str = "") -> dict:
    """按参考单号（多行）精确查订单。返回 {"rows": [...], "record_count": n}。"""
    data = {
        "act": "query_shop_order",
        "deliver_no": "\n".join(pos),
        "shop_id": shop_id,
        "ck_id": "", "country": "", "shop_types": "",
        "status": status,
        "start_date": start, "end_date": end,
        "pb_str": "1", "page": str(page), "page_size": str(page_size),
        "sort_by": "hwc_shop_order.addtime", "sort_order": "desc",
    }
    r = pw.post(AJAX, data, referer=REF, json_resp=True)
    if r.get("error"):
        raise S.PWError("query_shop_order 报错：%s" % r.get("message"))
    rows = parse_rows(r.get("content") or "")
    rec = (r.get("filter") or {}).get("record_count")
    return {"rows": rows, "record_count": int(rec or len(rows)),
            "page_count": r.get("page_count"), "raw": r.get("content") or ""}


if __name__ == "__main__":
    pw = S.PW()
    pw.login()
    r = query(pw, sys.argv[1:] or ["PO-211-10349152430710107"])
    print("record_count=%s" % r["record_count"])
    for x in r["rows"]:
        print("%-28s %-8s %-24s %-10s %s" % (x["po"], x["status"], x["wb"] or "-",
                                             x["wh"] or "-", x["sku"]))
