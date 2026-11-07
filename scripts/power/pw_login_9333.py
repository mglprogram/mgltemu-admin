# -*- coding: utf-8 -*-
"""登录帕沃尔（**无头** Chrome，2026-11-07 用户令：帕沃尔仓的发货改成无头）。

本脚本现在只是 `lib/pw_browser.ensure_logged_in()` 的薄封装：
自己起无头实例（独立 profile）→ 没登录就用账密登录 → 落到 hwc_shop。
账号读 `scripts/pw_orders/pw_account.local.json`（gitignored）。

用户 2026-11-07 令：「以后自己登录」。所以这是可复用的常驻能力，不是一次性探针。

登录真机坑（都已在 `pw_browser.ensure_logged_in` 里处理）：
  · 鼠标点「立即登录」**不触发提交**（无 onclick），Enter 也无效；只有 JS `button.click()` 有效
  · 页头有个导航链接「用户登录」(href=./user.php)，按「登录」模糊匹配会点到它
  · 账号/密码要用 `Input.insertText`（先 JS focus+select），单纯点击焦点落不进去
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import pw_browser as B  # noqa: E402

PORT = 9333


def main() -> int:
    pg, _ = B.ensure(PORT)
    if not pg:
        print("[FAIL] 无头帕沃尔浏览器起不来（端口 %d）" % PORT)
        return 2
    if B.is_logged_in(pg):
        print("[OK] 已登录：%s" % (pg.url or "")[:90])
    elif not B.ensure_logged_in(pg):
        return 42
    pg.goto(B.HWC_URL)
    time.sleep(6)
    print("[OK] 已打开 hwc_shop 全部订单页：%s" % (pg.url or "")[:100])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
