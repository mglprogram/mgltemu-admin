# -*- coding: utf-8 -*-
"""check_po_status 的 `--only-tags`（2026-11-02：14450 停用，只用 LM739 校验）。

用户令：「14450 的领星被停用，以后只用 LM739 去校验了」。

⚠ 这条**不能**用"删掉配置文件里的 14450"来实现 ——
   `oms_accounts.local.json` 同时被异常单导出（`export_abnormal_orders.py`）使用，
   那边仍然需要两个账号。停用是按**用途**的，不是全局删账号。
   ⇒ 用 `--only-tags` 在**调用点**收窄；配置保持完整。

跑：python -m pytest scripts/oms/tests -q
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OMS = os.path.dirname(HERE)                       # scripts/oms
ENTRY = os.path.join(OMS, "check_po_status.py")


def _run(*args):
    p = subprocess.run([sys.executable, "-X", "utf8", ENTRY] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=os.path.dirname(OMS))
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def test_unknown_tag_is_rejected_before_any_scan():
    """写错账号名必须当场报错（rc=2），不能静默查 0 个账号然后说"都没找到"。"""
    rc, out = _run("--pos", "PO-211-1", "--only-tags", "NO_SUCH_ACCOUNT")
    assert rc == 2
    assert "unknown" in out.lower() or "未知账号" in out


def test_only_tags_is_advertised_in_help():
    rc, out = _run("--help")
    assert "--only-tags" in out
    assert "LM739" in out, "帮助里要写明用法/缺省账号"


def test_config_file_still_keeps_both_accounts():
    """配置不能为了"停用 14450"被删账号 —— 异常单导出还要用它。"""
    import json
    p = os.path.join(OMS, "oms_accounts.local.json")
    if not os.path.exists(p):
        return                                        # 无人值守机器上没有这份文件，跳过
    tags = {a.get("tag") for a in (json.load(open(p, encoding="utf-8")).get("accounts") or [])}
    assert {"LM739", "14450"} <= tags, "两个账号都要留在配置里；停用按用途收窄"


def test_pipeline_defaults_to_lm739_only():
    """待发货确认 pipeline 的默认校验账号必须是 LM739（不是两个）。"""
    pipe = r"C:\Users\saing\.workbuddy\skills\temu-pending-confirm\pipeline.py"
    if not os.path.exists(pipe):
        return
    src = open(pipe, encoding="utf-8").read()
    assert '"--verify-account", default="LM739"' in src, "默认应只查 LM739"
    assert '"--only-tags"' in src, "必须把 --only-tags 传给 check_po_status"
