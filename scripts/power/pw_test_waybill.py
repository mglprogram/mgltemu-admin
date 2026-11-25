# -*- coding: utf-8 -*-
"""验证「批量获取单号」接口：`hwc_ship_news.php?act=get_ups_dh&id=<id>&is_ajax=1`。

样本：5668697（刚被 order_queren 确认，现已在 pb=2 待打包）

调用形态（页面 js 实测）：
    $.ajax({ url:"./hwc_ship_news.php?act=get_ups_dh&id="+ids+'&is_ajax=1',
             type:"post", dataType:"json",
             processData:false, contentType:false })
    → {html, error, msg}

两种都试：裸 POST（无 Content-Type）+ 普通 form POST。
"""
import json
import re
import sys
import time
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

BASE = "https://www.powerwarehouse.net/"
OID = sys.argv[1] if len(sys.argv) > 1 else "5668697"


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
            time.sleep(12)
    raise last


def call(pw, oid: str, bare: bool = True) -> dict:
    url = BASE + "hwc_ship_news.php?act=get_ups_dh&id=%s&is_ajax=1" % oid
    req = urllib.request.Request(url, data=b"" if bare else b"", method="POST")
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", BASE + "deliver.php?act=list&pb=2")
    if not bare:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with pw.op.open(req, timeout=pw.timeout) as r:
        txt = r.read().decode("utf-8", "replace")
    try:
        d = json.loads(txt)
    except Exception:  # noqa: BLE001
        d = {"_raw": clean(txt)[:200], "_len": len(txt)}
    return d


def main():
    pw = login_retry()
    print("① 裸 POST（无 Content-Type，与页面一致）")
    d = call(pw, OID, bare=True)
    print("   →", json.dumps(d, ensure_ascii=False)[:400])

    print("\n② 普通 form POST")
    d2 = call(pw, OID, bare=False)
    print("   →", json.dumps(d2, ensure_ascii=False)[:400])

    print("\n③ 回读 pb=2 该行（看运单号列）")
    time.sleep(2)
    t = pw.get("deliver.php?act=list&pb=2&uselastfilter=1")
    t = t if isinstance(t, str) else str(t)
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        if 'value="%s"' % OID not in tr:
            continue
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        for i, c in enumerate(cells):
            if c:
                print("   [%2d] %s" % (i, c[:110]))
        break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
