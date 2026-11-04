# -*- coding: utf-8 -*-
"""`export_abnormal_orders --only-tags`（2026-11-04：14450 停用，异常单导出只用 LM739）。

用户令：「要我把异常单导出也改成只用 LM739 吗？…改」。

⚠ 这条**不能**用「删掉配置里的 14450」实现：
   待发货确认 11-02 就停了 14450，但异常单导出 11-04 才停；
   两条链停用时间不同，而 `oms_accounts.local.json` 是**共用**的。
   ⇒ 用 `--only-tags` 在调用点收窄；配置保持两个账号，
     将来要补历史，手工加 `--only-tags 14450` 就能查。

跑：python -m pytest scripts/oms/tests -q
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OMS = os.path.dirname(HERE)
ENTRY = os.path.join(OMS, "export_abnormal_orders.py")
REPO = os.path.dirname(os.path.dirname(OMS))
CMD = os.path.join(REPO, "scripts", "tasks", "run_oms_abnormal_export.cmd")


def _run(*args):
    p = subprocess.run([sys.executable, "-X", "utf8", ENTRY] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=REPO)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def test_unknown_tag_is_rejected_before_any_scan():
    """写错账号名当场 rc=2，不能静默查 0 个账号然后说"没有异常"。"""
    rc, out = _run("--pos", "x", "--only-tags", "NO_SUCH") if False else _run(
        "--only-tags", "NO_SUCH_ACCOUNT", "--no-notify")
    assert rc != 0
    assert "unknown" in out.lower() or "未知账号" in out


def test_only_tags_is_in_help():
    rc, out = _run("--help")
    assert "--only-tags" in out
    assert "LM739" in out, "帮助里要写明用法（缺省停的是哪个账号的哪条链）"


def test_scheduled_launcher_pins_lm739():
    """定时任务入口必须带 `--only-tags LM739` —— 这才是真正生效的那一处。

    只改脚本参数而入口没传，等于没改（注册表/入口才是运行时的事实来源）。
    """
    src = open(CMD, encoding="utf-8").read()
    assert "--only-tags LM739" in src, "定时任务入口必须固定只查 LM739"
    assert "--only-tags 14450" not in src, "不许把停用账号写进定时任务"


def test_config_keeps_both_accounts_for_future_backfill():
    """配置仍然保留两个账号：停用按用途收窄，不是删账号（补历史时还要用）。"""
    import json
    p = os.path.join(OMS, "oms_accounts.local.json")
    if not os.path.exists(p):
        return
    tags = {a.get("tag") for a in (json.load(open(p, encoding="utf-8")).get("accounts") or [])}
    assert {"LM739", "14450"} <= tags


def test_launcher_is_ascii_and_crlf():
    """`.cmd` 必须 ASCII + CRLF（cmd 按 GBK 读，中文/混行尾会炸）。"""
    raw = open(CMD, "rb").read()
    assert not any(b > 127 for b in raw), "非 ASCII 字节会让 cmd 解析崩"
    assert raw.count(b"\r\n") == raw.count(b"\n"), "混用 LF/CRLF"
