import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\bel_goods")
import flux_auth

sys.stdout.reconfigure(encoding="utf-8")

URL = "https://agentseller-us.temu.com/main/flux-analysis"
win = sys.argv[1]
port = flux_auth.find_store_port(win)
print("port", port)

# 优先复用已有的 flux tab；没有就拿一个已登录的卖家页导航
t = flux_auth.find_tab(port, "flux-analysis")
if not t:
    pick = next((x for x in flux_auth.list_tabs(port)
                 if flux_auth.is_seller_page(x.get("url") or "")), None)
    if not pick:
        print("没有可用 tab")
        raise SystemExit(2)
    print("navigate:", pick.get("id"), (pick.get("url") or "")[:60])
    flux_auth.navigate(pick["webSocketDebuggerUrl"], URL)
    time.sleep(10)
    t = flux_auth.find_tab(port, "flux-analysis")

if not t:
    print("导航后仍找不到 flux tab")
    raise SystemExit(3)

# 校验真的渲染出快速筛选卡片
CARDS = "() => document.querySelectorAll('.quick-overdue-filter_card__7x5wV').length"
n = flux_auth.js(t["webSocketDebuggerUrl"], CARDS)
print("flux tab:", t["id"], "cards:", n)
for x in flux_auth.list_tabs(port):
    print("  ", x.get("id"), (x.get("url") or "")[:88])