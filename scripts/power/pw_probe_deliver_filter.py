# -*- coding: utf-8 -*-
"""试 `deliver.php?act=list` 的各种**筛选参数**，看服务端凭什么把这单筛掉。

你截图里筛选框填了「参考单号 = PO-211-07055826094713286」，
而我直接 GET 页面拿不到 —— 说明服务端靠 **session 的上次筛选** 或 **查询参数**。

本脚本逐个试：
    deliver_no=PO / waybill= / order_queren= / search=1 / uselastfilter 组合
以及 **POST searchForm**（页面 `searchOrder()` 可能走 POST）。

只读。
"""
import json
import re
import urllib.parse
import urllib.request

import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

BASE = "https://www.powerwarehouse.net/"
PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"


def login_retry(n=8):
    last = None
    import time  # noqa: PLC0415
    for i in range(n):
        try:
            p = SS.PW()
            p.login()
            return p
        except Exception as ex:  # noqa: BLE001
            last = ex
            time.sleep(12)
    raise last


def probe(pw, url) -> tuple:
    try:
        t = pw.get(url)
        t = t if isinstance(t, str) else str(t)
        return len(t), PO in t
    except Exception as ex:  # noqa: BLE001
        return -1, "ERR " + str(ex)[:40]


def main():
    pw = login_retry()
    enc = urllib.parse.quote(PO)
    variants = [
        "deliver.php?act=list&pb=1",
        "deliver.php?act=list&pb=1&uselastfilter=1",
        "deliver.php?act=list&pb=1&deliver_no=%s" % enc,
        "deliver.php?act=list&pb=1&deliver_no=%s&uselastfilter=1" % enc,
        "deliver.php?act=list&pb=1&keyword=%s" % enc,
        "deliver.php?act=list&pb=1&keywords=%s" % enc,
        "deliver.php?act=list&pb=1&po=%s" % enc,
        "deliver.php?act=list&pb=1&search=1&deliver_no=%s" % enc,
        "deliver.php?act=list&pb=all&deliver_no=%s" % enc,
        "deliver.php?act=list&pb=2&deliver_no=%s" % enc,
    ]
    print("=== GET 变体 ===")
    for u in variants:
        n, hit = probe(pw, u)
        print("  %-70s 长度=%-7s 命中=%s" % (u.replace(enc, "<PO>"), n, hit))

    # POST searchForm
    print("\n=== POST searchForm ===")
    for pb in ("1", "2"):
        data = urllib.parse.urlencode({
            "deliver_no": PO, "waybill": "", "order_queren": "",
            "keywords": "", "pb": pb,
        }).encode()
        req = urllib.request.Request(BASE + "deliver.php?act=list&pb=%s" % pb,
                                     data=data, method="POST")
        req.add_header("User-Agent", "Mozilla/5.0")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Referer", BASE + "deliver.php?act=list&pb=1")
        try:
            with pw.op.open(req, timeout=pw.timeout) as r:
                t = r.read().decode("utf-8", "replace")
            print("  POST pb=%s → 长度=%d 命中=%s" % (pb, len(t), PO in t))
            if PO in t:
                i = t.find(PO)
                print("     片段:", re.sub(r"\s+", " ",
                                          re.sub(r"<[^>]+>", " ",
                                                 t[i - 200:i + 300]))[:200])
        except Exception as ex:  # noqa: BLE001
            print("  POST pb=%s 失败 %s" % (pb, str(ex)[:50]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
