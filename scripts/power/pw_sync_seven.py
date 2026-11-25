# -*- coding: utf-8 -*-
"""七店依次同步订单（用户 2026-11-25 令）。

店铺：BS / BE / BAN / BD / BHY / BEL / KinglamanSport

接口（2026-11-25 挖到并验证）：
    POST hwc_shop.php?act=update_shop_news&shop_id=<店铺id>&pici=1&force=0&is_ajax=1
    → {"error":0,"msg":"更新成功"}；error=2 时带 force=1 重试

店铺 id 从「更新订单(新)」页的 `xp_shop_name_<shopId>` 反查，
本脚本直接从该页解析 `店铺名 → shopId`，不写死。
"""
import json
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

WANT = ["BS", "BE", "BAN", "BD", "BHY", "BEL", "KinglamanSport"]


def login_retry(n=6):
    last = None
    for i in range(n):
        try:
            p = SS.PW()
            p.login()
            return p
        except Exception as ex:  # noqa: BLE001
            last = ex
            print("  登录重试 %d/%d（%s）" % (i + 1, n, str(ex)[:50]))
            time.sleep(10)
    raise last


def shop_map(pw) -> dict:
    """从「更新订单(新)」页解析 `{店铺名: shopId}`。"""
    t = pw.get("hwc_shop.php?act=update_order_news")
    t = t if isinstance(t, str) else str(t)
    out = {}
    for m in re.finditer(r'<td class="xp_shop_name_(\d+)">([^<]+)</td>', t):
        sid, name = m.group(1), m.group(2).strip()
        out[name] = sid
    return out


def sync(pw, sid: str, force: int = 0, pici: str = "1") -> dict:
    url = ("hwc_shop.php?act=update_shop_news&shop_id=%s&pici=%s&force=%d&is_ajax=1"
           % (sid, pici, force))
    raw = pw.post(url, {})
    try:
        return json.loads(raw if isinstance(raw, str) else json.dumps(raw))
    except Exception:  # noqa: BLE001
        return {"error": -1, "msg": str(raw)[:150]}


def main():
    pw = login_retry()
    smap = shop_map(pw)
    print("找到 %d 个店铺：%s\n" % (len(smap), list(smap)))

    todo, miss = [], []
    for name in WANT:
        sid = smap.get(name)
        if not sid:
            # 大小写不敏感兜底
            for k, v in smap.items():
                if k.lower() == name.lower():
                    sid = v
                    break
        (todo if sid else miss).append((name, sid))
    if miss:
        print("⚠ 没找到店铺 id：%s\n" % [m[0] for m in miss])

    ok, bad = [], []
    for name, sid in todo:
        print("→ 同步 %-16s (id=%s)" % (name, sid))
        try:
            d = sync(pw, sid, force=0)
            if d.get("error") not in (0, None):
                print("   error=%s → force=1 重试" % d.get("error"))
                d = sync(pw, sid, force=1)
        except Exception as ex:  # noqa: BLE001
            print("   异常:", str(ex)[:70])
            bad.append([name, str(ex)[:60]])
            time.sleep(3)
            continue
        print("   → error=%s msg=%s" % (d.get("error"), str(d.get("msg"))[:60]))
        if d.get("error") in (0, None):
            ok.append(name)
        else:
            bad.append([name, str(d.get("msg"))[:60]])
        time.sleep(2)

    print("\n=== 同步结果 ===")
    print("  成功 %d：%s" % (len(ok), ok))
    if bad:
        print("  失败 %d：%s" % (len(bad), bad))
    if miss:
        print("  未找到 id：%s" % [m[0] for m in miss])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
