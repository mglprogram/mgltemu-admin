# -*- coding: utf-8 -*-
"""2026-10-29 事故回归：流量加速周更（首次按新排期周四 06:00）报「失败」。

面板显示 rc=6「失败」，但线上数据其实是新的。拆开看是三个独立缺陷叠加。

事故链条（日志 `_logs/temu-flux/weekly-20261029.log`）
------------------------------------------------------
1. 第 1 次 attempt rc=3：
     `ModuleNotFoundError: No module named 'requests'`
   启动器 `run_temu_flux_weekly.cmd` 用的是 **DSH 内部解释器**
   (`~/.dsh/dsh-runtimes/.../dependencies/python/python.exe`)，它没有
   `requests`（也没有 playwright）。`cdp_transport` 在模块顶层 `import requests`
   ⇒ 「刷新重点 SPU 名单」这一步每次都静默失败。
2. 第 2 次 attempt 抓到 4/4 板块全成功，但发布失败：
     `[FAIL] 未设置 DASH_PASSWORD`
   `publish_admin.py` 只认**进程环境变量**，没有用户级回退；而无人值守跑起来
   （DSH headless / 计划任务隐藏窗口）进程里没有这个变量。
3. `run_flux_weekly.py` 把**任何**发布失败一律折成 `return 6`，而 6 在全局口径里
   是「发布锁被占，已并入另一轮，**非故障**」⇒ 面板一边报失败一边写"非故障"，
   真因（缺密码）被彻底埋掉。

幸运的是数据本身是好的，`notify_panel.cmd` 的后台发布轮在 06:35:47 用别的路径
（`panel_refresh.py` 有用户级回退）把它发上线了 —— 但这是**碰巧兜住**，不是设计。
"""
from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "bel_goods"))

PUBLISH = os.path.join(_ROOT, "bel_goods", "publish_admin.py")
FLUX = os.path.join(_ROOT, "bel_goods", "run_flux_weekly.py")
CMD = os.path.join(_ROOT, "tasks", "run_temu_flux_weekly.cmd")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class PublishAdminPasswordFallbackTest(unittest.TestCase):
    """`publish_admin` 是**所有发布路径的唯一出口**，密码回退必须放在这里。"""

    def setUp(self):
        import publish_admin
        self.pa = publish_admin

    def test_has_dash_password_helper(self):
        self.assertTrue(callable(getattr(self.pa, "dash_password", None)))

    def test_reads_user_level_env_when_process_env_is_empty(self):
        """进程环境没有、用户级有 → 必须能取到（本次事故的直接原因）。"""
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                want = str(winreg.QueryValueEx(k, "DASH_PASSWORD")[0] or "")
        except OSError:
            self.skipTest("本机没有用户级 DASH_PASSWORD，跳过")
        if not want:
            self.skipTest("用户级 DASH_PASSWORD 为空，跳过")

        old = os.environ.pop("DASH_PASSWORD", None)
        try:
            self.assertEqual(self.pa.dash_password(), want)
        finally:
            if old is not None:
                os.environ["DASH_PASSWORD"] = old

    def test_process_env_wins(self):
        old = os.environ.get("DASH_PASSWORD")
        os.environ["DASH_PASSWORD"] = "from-process-env"
        try:
            self.assertEqual(self.pa.dash_password(), "from-process-env")
        finally:
            if old is None:
                os.environ.pop("DASH_PASSWORD", None)
            else:
                os.environ["DASH_PASSWORD"] = old

    def test_main_uses_the_helper_not_raw_environ(self):
        """回归点：`_main_body()` 里不能再直接读 `os.environ` 拿密码。

        注意断言要**只看函数体**：`dash_password()` 自己当然要用 os.environ
        （那是首选来源），注释里也会提到旧写法。
        """
        src = read(PUBLISH)
        body = src.split("def _main_body(", 1)[1]
        self.assertIn("pw = dash_password()", body)
        self.assertNotIn('pw = os.environ.get("DASH_PASSWORD", "")', body)

    def test_still_refuses_when_nothing_is_set(self):
        """安全底线不能为了能发布就放松：两边都没有 → 仍必须拒绝（返回 3）。"""
        src = read(PUBLISH)
        self.assertIn("return 3", src)
        self.assertIn("不许裸奔", src)


class FluxDeployExitCodeTest(unittest.TestCase):
    """发布失败不能一律折成 6 —— 6 的全局含义是「发布锁被占，非故障」。"""

    def test_deploy_returns_the_real_rc(self):
        src = read(FLUX)
        # deploy() 必须把 publish_admin 的退出码带出来
        self.assertIn("return r.returncode == 0,", src)
        self.assertIn("int(r.returncode or 0)", src)

    def test_main_propagates_dep_rc(self):
        src = read(FLUX)
        self.assertIn("return dep_rc", src)
        self.assertIn("ok_dep, dep_tail, dep_rc = deploy()", src)

    def test_missing_password_no_longer_masquerades_as_lock(self):
        """缺密码(publish 返回 3) 不能再被说成 6。"""
        src = read(FLUX)
        # 旧的 `if not ok_dep: return 6` 必须消失
        self.assertNotIn("    if not ok_dep:\n        return 6\n", src)


class TaskLauncherUsesUsablePythonTest(unittest.TestCase):
    """业务脚本不能跑在 DSH 的内部解释器上（缺 requests/playwright）。"""

    def test_launcher_does_not_use_dsh_internal_runtime(self):
        """`set "PY=..."` 那一行不能指向 DSH 内部解释器。

        （注释里**会**提到旧路径以说明为什么改 —— 所以只查赋值行，不查全文。）
        """
        py_line = ""
        for ln in read(CMD).splitlines():
            s = ln.strip()
            if s.lower().startswith('set "py='):
                py_line = s
                break
        self.assertTrue(py_line, "找不到 set \"PY=...\" 那一行")
        self.assertNotIn("dsh-runtimes", py_line,
                         "不要用 DSH 内部解释器：它没有 requests/playwright，"
                         "会让依赖 cdp_transport 的步骤静默失败（2026-10-29 事故）")

    def test_launcher_prefers_user_level_python_313(self):
        src = read(CMD)
        self.assertIn(r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe", src)

    def test_launcher_fails_loudly_without_python(self):
        src = read(CMD)
        self.assertIn("[FATAL] python not found", src)


class LauncherHygieneTest(unittest.TestCase):
    """cmd 的两条硬规矩（见 test_cmd_hygiene）。"""

    def test_ascii_only(self):
        n = sum(1 for b in open(CMD, "rb").read() if b > 127)
        self.assertEqual(n, 0, ".cmd 必须纯 ASCII（cmd 按 GBK 读）")

    def test_mentions_thursday(self):
        """排期 2026-10-28 已改为周四；头注别写回 Friday。"""
        src = read(CMD)
        self.assertIn("Thursday", src)
        self.assertNotIn("Friday", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
