# -*- coding: utf-8 -*-
"""test_workspace_autoregister.py — 「定时任务会话出现在发货工作区」的钉子。

用户 2026-11-06 的要求：
    「这个定时任务用 dsh 会话激活；激活的会话在**发货这个工作区**出现」

2026-11-07 实测踩了三层坑，每一层都是"看起来已经做了、其实没生效"：

  ① headless profile 的 bundle 里没有 `@deepseek-ai/dsh-workspace`
     ⇒ 会话根本不登记（`--dump-config | grep workspace` 为空即可验证）
  ② 补上插件仍然不登记：`attachSession()` 只被 `session.create({workspaceId})`
     调用，而 CLI 只传 `cwd`；README 写死 "Later cwd-only sessions remain Ungrouped"
  ③ 任务侧事后改 workspace.json 无效：storage-domain 是内存态，
     storage-json 没有 fs.watch

所以本文件钉住「补丁还在、且是正确的那种补丁」：

  · headless patch 必须同时 insert `dsh-workspace` 和 `dsh-workspace-autoregister`
  · 必须用 `insert:` 而不是 `- id:`（后者对不存在的 id 会静默跳过）
  · 插件必须 inject `sessions`（否则 apply() 当场抛，一行日志都不留）
  · 插件不能导出普通对象当 `Config`（cordis 要 schemastery，会抛 validate）
  · 定时任务的 action 必须走 run_via_dsh（直调 .cmd 压根不起会话）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))

DSH_HOME = os.path.join(os.path.expanduser("~"), ".dsh")
PLUGIN = os.path.join(r"D:\AI-workplace\DSH\deploy", "dsh-workspace-autoregister", "index.mjs")
HEADLESS_PATCH = os.path.join(DSH_HOME, "profiles", "headless", "cordis.patch.yml")
DESKTOP_PATCH = os.path.join(DSH_HOME, "profiles", "desktop", "cordis.patch.yml")


def _read(p: str) -> str:
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------- 插件本体

def test_plugin_exists():
    assert os.path.isfile(PLUGIN), "插件本体不见了：%s" % PLUGIN


def test_plugin_injects_sessions():
    """⚠ 没 inject `sessions` 会在 apply() 里直接抛，整个插件当场挂掉。"""
    src = _read(PLUGIN)
    m = [l for l in src.splitlines() if l.strip().startswith("export const inject")]
    assert m, "没找到 inject 声明"
    line = m[0]
    assert "workspaceRegistry" in line
    assert "'sessions'" in line or '"sessions"' in line, (
        "没 inject sessions → ctx.sessions 会抛 "
        "`cannot get property \"sessions\" without inject`")


def test_plugin_listens_for_session_created():
    """登记必须挂在 session/created 上（CLI 走 cwd，不会给 workspaceId）。"""
    src = _read(PLUGIN)
    assert "session/created" in src
    assert "attachSession" in src, "没调官方的 attachSession（会绕过 realpath / 目录校验）"


def test_plugin_does_not_autocreate_workspaces():
    """不自动建工作区 —— 否则每个临时目录都会长出一个工作区。"""
    src = _read(PLUGIN)
    assert "workspaceRegistry.create" not in src, (
        "插件自己在建工作区了；这会给每个临时 cwd 都造一个工作区")


def test_plugin_config_is_not_a_plain_object_export():
    """cordis 的 `Config` 必须是 schemastery；普通对象会抛 validate 错。"""
    src = _read(PLUGIN)
    assert "export const Config = {" not in src, (
        "又导出普通对象当 Config 了 → `Cannot read properties of undefined "
        "(reading 'validate')`")


def test_plugin_reconciles_from_disk():
    """别的进程登记的会话，本进程要能合并进来（storage 没有 fs.watch）。"""
    src = _read(PLUGIN)
    assert "reconcileFromDisk" in src
    assert "readFile" in src, "没读磁盘，跨进程登记永远看不到"


# ---------------------------------------------------------------- 挂载

def test_headless_patch_inserts_both_plugins():
    """两个都要：只有 workspace 不会登记，只有 autoregister 没有注册表可用。"""
    if not os.path.isfile(HEADLESS_PATCH):
        import pytest                                   # noqa: PLC0415
        pytest.skip("本机没有 headless patch")
    src = _read(HEADLESS_PATCH)
    assert "@deepseek-ai/dsh-workspace" in src, "headless 还是没挂 dsh-workspace 插件"
    assert "dsh-workspace-autoregister" in src, "headless 没挂 autoregister"


def test_patches_use_insert_not_bare_id():
    """`- id: 新插件` 会报 `entry not found` 并被静默跳过（2026-11-07 踩过）。"""
    for p in (HEADLESS_PATCH, DESKTOP_PATCH):
        if not os.path.isfile(p):
            continue
        src = _read(p)
        for name in ("dsh-workspace-autoregister",):
            if name not in src:
                continue
            assert "- insert:" in src, "%s 里的 %s 没走 insert 块" % (os.path.basename(p), name)


def test_desktop_patch_has_the_reconciler():
    """桌面端常驻，必须靠对账才能看到 headless 登记的会话。"""
    if not os.path.isfile(DESKTOP_PATCH):
        import pytest                                   # noqa: PLC0415
        pytest.skip("本机没有 desktop patch")
    assert "dsh-workspace-autoregister" in _read(DESKTOP_PATCH), (
        "桌面端没挂对账插件 → 定时任务的会话在界面上永远看不到（而且会被覆盖）")


# ---------------------------------------------------------------- 与任务入口的关系

def test_scheduled_task_action_goes_through_dsh_runner():
    """action 直调业务 .cmd 也能跑通，但**不会起会话** —— 那正是本 bug 的起点。"""
    reg_path = os.path.join(os.path.expanduser("~"), ".agents", "skills",
                            "scheduled-tasks", "tasks.json")
    if not os.path.isfile(reg_path):
        import pytest                                       # noqa: PLC0415
        pytest.skip("本机没有注册表")
    with open(reg_path, encoding="utf-8-sig") as fh:
        reg = json.load(fh)
    for t in reg.get("tasks") or []:
        if t.get("name") != "TEMU-Fulfill-Daily":
            continue
        args = (t.get("action") or {}).get("arguments") or ""
        assert "run_via_dsh.cmd" in args, "action 直调业务入口 ⇒ 起不了会话"
        assert (t.get("dsh") or {}).get("workspace"), "没配 dsh.workspace"
        return
    import pytest                                           # noqa: PLC0415
    pytest.skip("注册表里没有 TEMU-Fulfill-Daily")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
