# -*- coding: utf-8 -*-
"""帕沃尔（powerwarehouse.net）**统一接口层** —— 全部 9 个动作都走 HTTP，不碰浏览器。

2026-11-25 逐个真机验证过的接口：

| 动作 | 接口 |
|---|---|
| 查单       | POST hwc_shop.php?is_ajax=1   act=query_shop_order |
| 设仓渠道   | POST deliver.php  act=get_shop_paisongx → POST hwc_shop.php act=operate(batch) |
| 下单代发   | POST hwc_shop.php?act=xiadan_df&id=<id>&is_ajax=1 |
| 换 Fnsku   | GET hwc_shop.php?act=edit_order&id=<id> → POST act=edit_update |
| 选品器     | GET deliver.php?act=add_products&type=view&user_id=..&ck=..&keywords=<Fnsku> |
| 库存查询   | GET stock.php?act=list&keywords=<款号/Fnsku> |
| 七店同步   | POST hwc_shop.php?act=update_shop_news&shop_id=<id>&is_ajax=1 |
| 确认订单   | POST deliver.php?act=operate  act=batch&ids=<id>i..&select_types=order_queren |
| 获取单号   | POST hwc_ship_news.php?act=get_ups_dh&id=<id>&is_ajax=1 |

用法：
    from pw_api import PWApi
    pw = PWApi(); pw.login()
    print(pw.query("PO-211-07055826094713286"))
    print(pw.stock("YOGA-4SetA-L"))
    pw.sync_seven()
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_HERE, "lib"),
           os.path.join(os.path.dirname(_HERE), "pw_orders", "lib"),
           _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pw_session as SS  # noqa: E402

BASE = "https://www.powerwarehouse.net/"
DATE = ("2026-11-01", "2026-11-25")

#: 仓库 → 选品器 ck / 下单 ck
CK = {"加州3仓": "22", "加州三仓": "22", "kansas仓": "10", "kansas": "10"}
#: 平台 → (指定快递, 出货渠道)
PLATFORM_ROUTE = {"temu": ("temu", "temu官方面单"),
                  "shein": ("Shein", "Shein官方面单")}
STATUSES = ("下单失败", "已下单", "未下单", "已取消")
SHOP7 = ["BS", "BE", "BAN", "BD", "BHY", "BEL", "KinglamanSport"]


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c or "")
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def _num(s) -> float:
    try:
        return float(str(s).replace(",", "").strip() or 0)
    except Exception:  # noqa: BLE001
        return -1.0


class PWApi:
    """帕沃尔接口层：一个登录会话 + 9 个动作。"""

    def __init__(self, retry: int = 6, wait: float = 10.0):
        self.retry, self.wait = retry, wait
        self.pw = None

    # ---------------------------------------------------------------- 会话
    def login(self) -> "PWApi":
        last = None
        for _ in range(self.retry):
            try:
                p = SS.PW()
                p.login()
                self.pw = p
                return self
            except Exception as ex:  # noqa: BLE001
                last = ex
                time.sleep(self.wait)
        raise RuntimeError("帕沃尔登录失败：%s" % str(last)[:120])

    def _get(self, url: str) -> str:
        return self.pw.get(url) or ""

    def _post(self, url: str, data, referer: str = "", json_ct: bool = True):
        body = urllib.parse.urlencode(data, doseq=True).encode()
        req = urllib.request.Request(
            url if url.startswith("http") else BASE + url, data=body, method="POST")
        req.add_header("User-Agent", "Mozilla/5.0")
        if json_ct:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("X-Requested-With", "XMLHttpRequest")
        if referer:
            req.add_header("Referer", referer if referer.startswith("http")
                           else BASE + referer)
        with self.pw.op.open(req, timeout=self.pw.timeout) as r:
            return r.read().decode("utf-8", "replace")

    def _post_bare(self, url: str, referer: str = ""):
        """裸 POST（页面 `processData:false, contentType:false` 那种）。"""
        req = urllib.request.Request(
            url if url.startswith("http") else BASE + url, data=b"", method="POST")
        req.add_header("User-Agent", "Mozilla/5.0")
        req.add_header("X-Requested-With", "XMLHttpRequest")
        if referer:
            req.add_header("Referer", BASE + referer)
        with self.pw.op.open(req, timeout=self.pw.timeout) as r:
            return r.read().decode("utf-8", "replace")

    @staticmethod
    def _json(txt: str) -> dict:
        try:
            return json.loads(txt)
        except Exception:  # noqa: BLE001
            return {"_raw": clean(txt)[:200], "_len": len(txt or "")}

    # ---------------------------------------------------------------- ① 查单
    def query(self, po: str = "", status: str = "", page: int = 1,
              start: str = "", end: str = "") -> dict:
        """按单号/状态查发货单。返回 `{rows: [...], html: str}`。"""
        body = {"act": "query_shop_order", "deliver_no": po, "shop_id": "",
                "ck_id": "", "status": status,
                "start_date": start or DATE[0], "end_date": end or DATE[1],
                "country": "", "shop_types": "", "pb_str": "", "page": str(page)}
        d = self._json(self._post("hwc_shop.php?is_ajax=1", body,
                                  referer="hwc_shop.php?act=shop_order&pb=1"))
        html = d.get("content") or ""
        return {"html": html, "raw": d, "rows": self._parse_orders(html)}

    @staticmethod
    def _parse_orders(html: str) -> list:
        out = []
        for oid, tr in re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>',
                                  html, re.S):
            txt = clean(tr)
            po = re.search(r"(PO-\d+-\d+|[A-Z]{2,3}\d[A-Z0-9]{6,})", txt)
            sku = re.search(r"平台SKU\s*[:：]\s*(\S+)", txt)
            wh = re.search(r"发货仓库\s*[:：]\s*(\S*)", txt)
            st = next((s for s in STATUSES if s in tr), "")
            err = ""
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S):
                cc = clean(cell)
                if "条" in cc and "下单" in cc:
                    err = cc
            out.append({"id": oid, "po": po.group(1) if po else "",
                        "sku": sku.group(1) if sku else "",
                        "wh": (wh.group(1) if wh else "").strip(),
                        "status": st, "err": err})
        return out

    def state(self, po: str) -> dict:
        rows = self.query(po)["rows"]
        return rows[0] if rows else {"id": "", "po": po, "status": "查不到"}

    # ---------------------------------------------------------------- ② 库存
    def stock(self, keyword: str) -> list:
        """库存页 keywords 查同款 → `[{fnsku, seller, wh, stock, stock_n}]`（按库存降序）。"""
        t = self._get("stock.php?act=list&keywords=%s"
                      % urllib.parse.quote(keyword))
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
        head = None
        for tr in rows:
            cs = [clean(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
            if any("Fnsku" in c for c in cs):
                head = cs
                break
        out = []
        for tr in rows:
            if keyword.lower() not in tr.lower():
                continue
            cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            d = dict(zip(head or [], cells))
            fn = d.get("Fnsku") or ""
            if not fn:
                continue
            wh_raw = d.get("仓库名称", "")
            out.append({"fnsku": fn, "seller": d.get("Seller sku", ""),
                        "wh_raw": wh_raw, "wh": CK.get(wh_raw) and wh_raw or wh_raw,
                        "stock": d.get("代发库存", "0"),
                        "stock_n": _num(d.get("代发库存", "0"))})
        out.sort(key=lambda x: -x["stock_n"])
        return out

    # ---------------------------------------------------------------- ③ 选品器
    def picker(self, fnsku: str, ck: str = "22") -> dict:
        """选品器按 Fnsku 搜 → `{pid, pname, ck_sku, fh_weight}`（空 dict = 没找到）。"""
        u = ("deliver.php?act=add_products&type=view&user_id=14450&user_name=&ck=%s"
             "&keywords=%s" % (ck, urllib.parse.quote(fnsku)))
        t = self._get(u)
        for b in re.split(r'(?=<input type="checkbox" name="checkboxes")', t):
            m = re.match(r'<input type="checkbox" name="checkboxes"\s+class="checkboxes"\s+'
                         r'value="(\d+)"', b)
            if not m:
                continue
            head = b[:1600]

            def g(name):
                mm = re.search(r'name="%s" class="[^"]*" value="([^"]*)"'
                               % re.escape(name), head)
                return mm.group(1) if mm else ""

            if g("ck_sku[]").upper() == fnsku.upper():
                return {"pid": m.group(1), "pname": g("pname[]"),
                        "ck_sku": g("ck_sku[]"), "fh_weight": g("fh_weight[]")}
        return {}

    # ---------------------------------------------------------------- ④ 换 Fnsku
    def _read_form(self, oid: str) -> list:
        t = self._get("hwc_shop.php?act=edit_order&id=%s" % oid)
        m = re.search(r'<form[^>]*name="myform"[^>]*>(.*?)</form>', t, re.S | re.I)
        body = m.group(1) if m else t
        inputs, selects = [], []
        for tag in re.findall(r"<input[^>]*>", body, re.I):
            nm = re.search(r'name="([^"]*)"', tag)
            if not nm:
                continue
            n = nm.group(1)
            typ = (re.search(r'type="([^"]*)"', tag) or [None, "text"])[1].lower()
            if typ in ("button", "submit", "reset", "file", "image"):
                continue
            if typ in ("checkbox", "radio") and "checked" not in tag.lower():
                continue
            val = re.search(r'value="([^"]*)"', tag)
            v = _html.unescape(val.group(1)) if val else ""
            if v.startswith("'+") or v.startswith('"+'):
                v = ""
            inputs.append((n, v))
        for n, inner in re.findall(
                r'<select[^>]*name="([^"]*)"[^>]*>(.*?)</select>', body, re.S | re.I):
            s = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', inner, re.I)
            selects.append((n, _html.unescape(s.group(1)) if s else ""))
        have = {n for n, _v in inputs}
        return list(inputs) + [(n, v) for n, v in selects if n not in have]

    def switch_sku(self, po: str, new_fnsku: str, ck: str = "",
                   apply: bool = True) -> dict:
        """把订单 Fnsku 换成 `new_fnsku`（地址字段原样回传，只动产品行）。"""
        st = self.state(po)
        oid = st.get("id")
        if not oid:
            return {"ok": False, "why": "查不到订单"}
        if not ck:
            ck = CK.get(st.get("wh") or "", "22")
        prod = self.picker(new_fnsku, ck)
        fields = self._read_form(oid)
        addr = {n: v for n, v in fields
                if n in ("country", "city_name", "city", "district")}
        if any(not v or v == "0" for v in addr.values()):
            return {"ok": False, "why": "地址字段不完整: %s" % addr}
        if not prod.get("pid"):
            return {"ok": False, "why": "选品器查不到 %s（ck=%s）" % (new_fnsku, ck)}
        if not apply:
            return {"ok": True, "dry": True, "oid": oid, "prod": prod}
        pairs = []
        for n, v in fields:
            if n == "huan_id[]":
                v = prod["pid"]
            elif n == "ck_sku[]":
                v = prod["ck_sku"]
            elif n == "pname[]":
                v = prod["pname"]
            pairs.append((n, v))
        r = self._json(self._post("hwc_shop.php", pairs,
                                  referer="hwc_shop.php?act=edit_order&id=%s" % oid))
        return {"ok": int(r.get("error") or 0) == 0, "resp": r, "prod": prod}

    # ---------------------------------------------------------------- ⑤ 设仓渠道
    def set_wh(self, po: str, wh: str = "加州3仓",
               platform: str = "temu") -> dict:
        """设仓库 + 渠道（deliver.php get_shop_paisongx → hwc_shop.php operate batch）。"""
        st = self.state(po)
        oid = st.get("id")
        if not oid:
            return {"ok": 0, "why": "查不到订单"}
        zdkd = {"temu": "803", "shein": "804"}.get(platform, "803")
        ck = CK.get(wh, "22")
        r1 = self._json(self._post(
            "deliver.php",
            {"act": "get_shop_paisongx", "ids": "%si" % oid, "zdkd": zdkd,
             "select_ck": ck, "select_types": "pl_edit_ck_chqd"},
            referer="hwc_shop.php?act=shop_order&pb=1"))
        want = PLATFORM_ROUTE.get(platform, PLATFORM_ROUTE["temu"])[1]
        cands = [o for o in (r1.get("html") or [])
                 if str(o.get("pname") or "").replace(" ", "").lower()
                 == want.replace(" ", "").lower()]
        if not cands:
            return {"ok": 0, "why": "取不到渠道", "resp": str(r1)[:160]}
        chqd = str(cands[0]["id"])
        pairs = [("act", "batch"), ("pb", ""), ("pb_str", ""),
                 ("ids", "%si" % oid), ("select_types", "pl_edit_ck_chqd"),
                 ("select_ck", ck), ("select_zdkd", zdkd), ("select_chqd", chqd)]
        self._post("hwc_shop.php?act=operate", pairs,
                   referer="hwc_shop.php?act=shop_order&pb=1")
        return {"ok": 1, "wh": wh, "chqd": chqd, "chqd_name": cands[0].get("pname")}

    # ---------------------------------------------------------------- ⑥ 下单代发
    def ship(self, po: str = "", oid: str = "") -> dict:
        if not oid:
            oid = self.state(po).get("id")
        if not oid:
            return {"ok": False, "why": "查不到订单"}
        d = self._json(self._post("hwc_shop.php?act=xiadan_df&id=%s&is_ajax=1" % oid,
                                  []))
        d["ok"] = str(d.get("msg") or "").startswith("下单成功")
        return d

    # ---------------------------------------------------------------- ⑦ 确认订单
    def confirm(self, ids: list) -> dict:
        pairs = [("act", "batch"), ("pb", "1"), ("pb_str", ""),
                 ("ids", "".join("%si" % i for i in ids)),
                 ("select_types", "order_queren")]
        txt = self._post("deliver.php?act=operate", pairs,
                         referer="deliver.php?act=list&pb=1")
        return {"ok": True, "raw_len": len(txt), "json": self._json(txt)}

    def pending(self, pb: str = "1") -> list:
        """列 `deliver.php` 某 pb 页的订单（pb=1 待确认 / pb=2 待打包）。"""
        t = self._get("deliver.php?act=list&pb=%s&uselastfilter=1" % pb)
        out = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
            m = re.search(r'name="checkboxes\[\]"[^>]*value="(\d+)"', tr)
            if not m:
                continue
            txt = clean(tr)
            po = re.search(r"(PO-\d+-\d+|[A-Z]{2,3}\d[A-Z0-9]{6,})", txt)
            out.append({"id": m.group(1), "po": po.group(1) if po else "",
                        "text": txt[:110]})
        return out

    # ---------------------------------------------------------------- ⑧ 获取单号
    def get_waybill(self, oid: str) -> dict:
        d = self._json(self._post_bare(
            "hwc_ship_news.php?act=get_ups_dh&id=%s&is_ajax=1" % oid,
            referer="deliver.php?act=list&pb=2"))
        d["track"] = clean(d.get("html") or "")
        d["ok"] = int(d.get("error") or 0) == 0
        return d

    # ---------------------------------------------------------------- ⑨ 店铺同步
    def shop_map(self) -> dict:
        t = self._get("hwc_shop.php?act=update_order_news")
        return {m.group(2).strip(): m.group(1)
                for m in re.finditer(r'<td class="xp_shop_name_(\d+)">([^<]+)</td>', t)}

    def sync(self, shops: list = None, pici: str = "1") -> dict:
        shops = shops or SHOP7
        smap = self.shop_map()
        ok, bad, miss = [], [], []
        for name in shops:
            sid = smap.get(name)
            if not sid:
                sid = next((v for k, v in smap.items()
                            if k.lower() == name.lower()), "")
            if not sid:
                miss.append(name)
                continue
            d = self._json(self._post(
                "hwc_shop.php?act=update_shop_news&shop_id=%s&pici=%s&force=0&is_ajax=1"
                % (sid, pici), []))
            if d.get("error") not in (0, None):
                d = self._json(self._post(
                    "hwc_shop.php?act=update_shop_news&shop_id=%s&pici=%s&force=1&is_ajax=1"
                    % (sid, pici), []))
            (ok if d.get("error") in (0, None) else bad).append(name)
            time.sleep(2)
        return {"ok": ok, "fail": bad, "missing": miss}


if __name__ == "__main__":
    pw = PWApi().login()
    po = sys.argv[1] if len(sys.argv) > 1 else ""
    if po:
        print(json.dumps(pw.state(po), ensure_ascii=False))
    else:
        print("shops:", len(pw.shop_map()))
