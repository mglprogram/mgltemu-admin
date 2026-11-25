# -*- coding: utf-8 -*-
"""帕沃尔「确认订单 → 批量获取单号」接口版（2026-11-25 挖到并验证）。

接口：
  ① 确认订单    POST deliver.php?act=operate
                data: act=batch, pb=1, ids=<id>i<id>i…, select_types=order_queren
  ② 获取单号    POST hwc_ship_news.php?act=get_ups_dh&id=<订单id>&is_ajax=1
                → {html: 运单号片段, error, msg}（无 Content-Type）

★ 用户 2026-11-25 提示：「批量获取跟踪号也是要先在平台买面单才能拉到的」
  ⇒ 前提：TEMU 侧已买面单（有运单号），帕沃尔才能拉回。

流程：
    deliver.php?act=list&pb=1（待确认）→ 勾选 → 确认订单
    → deliver.php?act=list&pb=2（待打包）→ 批量获取单号
"""
import json
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

BASE = "https://www.powerwarehouse.net/"


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def login_retry(n=8):
    last = None
    for i in range(n):
        try:
            p = SS.PW()
            p.login()
            return p
        except Exception as ex:  # noqa: BLE001
            last = ex
            print("  登录重试 %d/%d %s" % (i + 1, n, str(ex)[:50]))
            import time  # noqa: PLC0415
            time.sleep(12)
    raise last


# ---------------------------------------------------------------- 查页
def list_page(pw, po: str, pb: str = "1") -> str:
    u = "deliver.php?act=list&pb=%s" % pb
    if po:
        u += "&deliver_no=%s" % urllib.parse.quote(po)
    return pw.get(u) or ""


def row_ids(t: str, po: str = "") -> list:
    """从列表页解析 `checkboxes` 的 value（订单 id）。"""
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        if po and po not in tr:
            continue
        m = re.search(r'name="(?:checkboxes\[\]?|ids\[\]?)"[^>]*value="(\d+)"', tr)
        if not m:
            continue
        pom = re.search(r"(PO-\d+-\d+|[A-Z]{2,3}\d[A-Z0-9]{6,})", clean(tr))
        out.append({"id": m.group(1), "po": pom.group(1) if pom else "",
                    "text": clean(tr)[:110]})
    return out


# ---------------------------------------------------------------- ① 确认订单
def confirm_orders(pw, ids: list, pb: str = "1") -> dict:
    data = [("act", "batch"), ("pb", pb), ("pb_str", ""),
            ("ids", "".join("%si" % i for i in ids)),
            ("select_types", "order_queren")]
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + "deliver.php?act=operate", data=body,
                                 method="POST")
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Referer", BASE + "deliver.php?act=list&pb=1")
    with pw.op.open(req, timeout=pw.timeout) as r:
        txt = r.read().decode("utf-8", "replace")
    try:
        return json.loads(txt)
    except Exception:  # noqa: BLE001
        return {"error": -1, "msg": clean(txt)[:200], "raw_len": len(txt)}


# ---------------------------------------------------------------- ② 获取单号
def get_waybill(pw, oid: str) -> dict:
    """`hwc_ship_news.php?act=get_ups_dh` —— 裸 POST（页面不设 Content-Type）。"""
    url = BASE + "hwc_ship_news.php?act=get_ups_dh&id=%s&is_ajax=1" % oid
    req = urllib.request.Request(url, data=b"", method="POST")
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", BASE + "deliver.php?act=list&pb=2")
    with pw.op.open(req, timeout=pw.timeout) as r:
        txt = r.read().decode("utf-8", "replace")
    try:
        d = json.loads(txt)
    except Exception:  # noqa: BLE001
        d = {"error": -1, "msg": clean(txt)[:200]}
    d["html"] = clean(d.get("html") or "")[:200]
    return d


def main():
    po = sys.argv[1] if len(sys.argv) > 1 else ""
    apply_ = "--apply" in sys.argv
    pw = login_retry()

    # ① 待确认
    t1 = list_page(pw, po, "1")
    rows = row_ids(t1, po)
    print("【待确认 pb=1】命中 %d 行：" % len(rows))
    for r in rows:
        print("   id=%-9s %-24s %s" % (r["id"], r["po"], r["text"][:60]))

    if rows:
        if not apply_:
            print("\n[dry-run] 将确认订单 ids=%s" % [r["id"] for r in rows])
        else:
            d = confirm_orders(pw, [r["id"] for r in rows])
            print("\n确认订单 →", json.dumps(d, ensure_ascii=False)[:250])

    # ② 待打包 → 获取单号
    t2 = list_page(pw, po, "2")
    rows2 = row_ids(t2, po)
    print("\n【待打包 pb=2】命中 %d 行：" % len(rows2))
    for r in rows2:
        print("   id=%-9s %-24s %s" % (r["id"], r["po"], r["text"][:60]))

    if not rows2:
        print("\n（该单不在待打包页 —— 可能还没确认，或已获取过单号）")
        return 0
    if not apply_:
        print("\n[dry-run] 将获取单号 ids=%s" % [r["id"] for r in rows2])
        return 0

    for r in rows2:
        w = get_waybill(pw, r["id"])
        print("  %-24s error=%s msg=%s html=%s"
              % (r["po"], w.get("error"), str(w.get("msg"))[:40], w.get("html")[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
