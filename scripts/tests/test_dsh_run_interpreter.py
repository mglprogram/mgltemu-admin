# -*- coding: utf-8 -*-
"""test_dsh_run_interpreter.py — `dsh_run.py` 必须能给裸 `.py` 自动补解释器。

★ 2026-11-07 真机（差点让每日发货任务每次首跑都失败）：
    注册表 `dsh.inner` 与 prompt 给的是一个**裸 .py 路径**
    （`...\run_fulfill_daily_inner.py`）。`subprocess.Popen` 直接执行它时
    Windows 不走 PATHEXT 关联，报
        OSError(8, '%1 不是有效的 Win32 应用程序。')
    ⇒ 包装器 rc=1、`attempts.jsonl` 一条都写不进去
    ⇒ `dsh_task.decide()` 判「等于没执行」（rc=71）。

    正解：`dsh_run.py` 自己识别后缀补 `python` / `powershell` 前缀。
    这几条把行为钉死，别再退回「直接把路径交给 Popen」。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dsh_run as dr  # noqa: E402


class BarePyInterpreterTest(unittest.TestCase):
    def _run(self, target: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("DSH_SENTINEL_ROOT")
            os.environ["DSH_SENTINEL_ROOT"] = td
            try:
                p = subprocess.run(
                    [sys.executable, dr.__file__, "--task", "T-probe", "--", target],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                return p.returncode, (p.stdout or "") + (p.stderr or "")
            finally:
                if old is None:
                    os.environ.pop("DSH_SENTINEL_ROOT", None)
                else:
                    os.environ["DSH_SENTINEL_ROOT"] = old

    def test_bare_py_inner_actually_runs(self):
        """裸 .py 必须能跑起来（修之前这里是 OSError(8) → rc=1）。"""
        with tempfile.TemporaryDirectory() as td:
            script = os.path.join(td, "probe_inner.py")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write("import sys\nprint('INNER-RAN')\nsys.exit(0)\n")
            rc, out = self._run(script)
        self.assertEqual(rc, 0, out)
        self.assertIn("INNER-RAN", out)
        self.assertIn("injected interpreter for .py inner", out)

    def test_inner_failure_rc_is_passed_through(self):
        """补了解释器之后，业务脚本自己的非零退出码仍要原样透传。"""
        with tempfile.TemporaryDirectory() as td:
            script = os.path.join(td, "probe_fail.py")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write("import sys\nsys.exit(7)\n")
            rc, out = self._run(script)
        self.assertEqual(rc, 7, out)

    def test_cmd_inner_is_left_alone(self):
        """`.cmd` 不需要（也不该）加解释器前缀。"""
        with tempfile.TemporaryDirectory() as td:
            bat = os.path.join(td, "probe.cmd")
            with open(bat, "w", encoding="ascii") as fh:
                fh.write("@echo off\r\necho CMD-RAN\r\nexit /b 0\r\n")
            rc, out = self._run(bat)
        self.assertEqual(rc, 0, out)
        self.assertIn("CMD-RAN", out)
        self.assertNotIn("injected interpreter", out)


class SentinelRootEnvTest(unittest.TestCase):
    """哨兵根目录必须可由环境变量指定（沙箱只能写会话工作区）。"""

    def test_env_overrides_default(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("DSH_SENTINEL_ROOT")
            os.environ["DSH_SENTINEL_ROOT"] = td
            try:
                self.assertEqual(os.path.normcase(dr._sentinel_root()),
                                 os.path.normcase(td))
            finally:
                if old is None:
                    os.environ.pop("DSH_SENTINEL_ROOT", None)
                else:
                    os.environ["DSH_SENTINEL_ROOT"] = old

    def test_default_is_under_proj(self):
        old = os.environ.pop("DSH_SENTINEL_ROOT", None)
        try:
            self.assertIn("dsh-sentinel", dr._sentinel_root())
        finally:
            if old is not None:
                os.environ["DSH_SENTINEL_ROOT"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
