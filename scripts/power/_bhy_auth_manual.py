# -*- coding: utf-8 -*-
"""临时：手工走一遍 BHY 美国站授权（home → 商家中心 → seller-login 勾条款 → 授权登录）。

一次性排障脚本，不进 pipeline。
"""
import sys, time, json, urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
from lib import auth_temu_driver as A
from lib import cdp_transport as C

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 53773
SHOP = "BHY"


def tabs():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % PORT, timeout=5) as r:
            return [(t.get("id") or t.get("targetId"), t.get("url") or "") for t in json.load(r)
                    if t.get("type") == "page"]
    except Exception as e:                                     # noqa: BLE001
        print("tabs err", e)
        return []


def watch(tid, secs=20, tag=""):
    """盯住某个 tab 的 URL 演变。"""
    out = []
    t0 = time.time()
    while time.time() - t0 < secs:
        u = dict(tabs()).get(tid, "")
        out.append(u)
        print("  [%s] +%.1fs %s" % (tag, time.time() - t0, u[:120]))
        time.sleep(1.5)
    return out


print("=== tabs before ===")
for tid, u in tabs():
    print("  ", tid, u[:110])

# 1) 自己开一页到主页（不带 redirectUrl，走正规链路）
tid = C.create_target(PORT, "https://agentseller-us.temu.com/", background=True)
print("=== 新页 tid=%s ===" % tid)
time.sleep(5)
print("  now:", dict(tabs()).get(tid, "")[:140])

b = C.connect(PORT)
target = [t for t in (lambda: json.load(urllib.request.urlopen(
    "http://127.0.0.1:%d/json/list" % PORT, timeout=5)))() if (t.get("id") or t.get("targetId")) == tid]
page = C.CdpPage(PORT, target[0])

print("=== S2 点「中国地区·商家中心」 ===")
ok = A._click_region_goto(page, b)
print("  click_region_goto ->", ok)
time.sleep(4)

print("=== 找授权框 ===")
pg, kind = A.find_auth_page(b)
print("  find_auth_page ->", kind, (pg.url[:120] if pg else None))
if pg is None:
    print("FAIL: 没有授权框")
    sys.exit(2)

print("=== S3 勾条款 ===")
print("  checkbox ->", A._check_auth_checkbox(pg))
time.sleep(1)

print("=== S4 点授权 ===")
print("  confirm ->", A._click_auth_confirm(pg))

print("=== S5 复核（真开页看落点）===")
st, landed = A.probe_login_state(PORT, "us", log=print, shop=SHOP)
print("  probe ->", st, landed[:120])
if st != "ok":
    tid2 = C.create_target(PORT, "https://agentseller-us.temu.com/mmsos/orders.html", background=True)
    us = watch(tid2, 20, "post")
    print("  最终:", us[-1][:120])
print("DONE", st)
