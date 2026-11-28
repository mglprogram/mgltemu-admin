# -*- coding: utf-8 -*-
"""钉住 refresh_today_sales.py 透传 publish_admin.py 的退出码（特别是 rc=6）。

2026-11-28 实测踩坑：publish_admin.py --data-only 在发布锁被另一轮占用时
按设计返回 rc=6（「已并入那一轮，非故障」），注册表 ok_results=[0, 6, 5] 也认 6。
但 refresh_today_sales.py 旧代码只看 rc==0，把 6 吞成 1 导致任务巡检误报失败。
"""

import os
import sys
from unittest import mock

_TESTS = os.path.dirname(os.path.abspath(__file__))
_GOODS = os.path.join(os.path.dirname(_TESTS), "goods")
if _GOODS not in sys.path:
    sys.path.insert(0, _GOODS)

import refresh_today_sales as RTS  # noqa: E402


def test_publish_rc6_is_preserved():
    """publish_admin 返回 6（发布锁被占）时，main() 必须原样返回 6，不能吞成 1。"""
    def fake_run_rc(args, timeout, what):
        script = str(args[0])
        if script.endswith("publish_admin.py"):
            return 6, "已有发布在跑"
        return 0, "ok"

    with mock.patch.object(sys, "argv", ["refresh_today_sales.py", "--skip-fetch"]), \
         mock.patch.object(RTS, "ensure_gate_password", return_value="dummy"), \
         mock.patch.object(RTS, "run_rc", side_effect=fake_run_rc):
        assert RTS.main() == 6


def test_publish_rc3_is_preserved():
    """其它非零码（如缺密码 rc=3）同样原样透传，不掩盖真故障。"""
    def fake_run_rc(args, timeout, what):
        script = str(args[0])
        if script.endswith("publish_admin.py"):
            return 3, "no password"
        return 0, "ok"

    with mock.patch.object(sys, "argv", ["refresh_today_sales.py", "--skip-fetch"]), \
         mock.patch.object(RTS, "ensure_gate_password", return_value="dummy"), \
         mock.patch.object(RTS, "run_rc", side_effect=fake_run_rc):
        assert RTS.main() == 3
