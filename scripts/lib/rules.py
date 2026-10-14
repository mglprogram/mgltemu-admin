# -*- coding: utf-8 -*-
"""lib/rules.py — 规则固化：把「工程控制」做成可执行、标准行为接口。

原则（对应 AGENTS.md 铁律6 + 记忆系统）：
- 规则 = 可执行 + 统一 `perform(action, params)` 入口（签名不变，新能力 = 新增 action/方法）。
- 不靠模型背规则（会衰变/遗忘）；agent 跑标准入口或显式 `perform('rule_xxx')` 即触发。
- 与业务 Driver 同构：外部系统（闸门 / 文件）封成一层，只暴露统一接口。

支持的 rule actions：
  rule_gate       强制令闸门（封装 _scripts/gate_check.js <READ|WEB|WRITE> [reason]）
  rule_audit      自动审计（代码副作用写 _logs/audit_<date>.log，不靠记忆）
  rule_flag       红线前置检查（内置示例红线；可扩展）
  rule_validate   目标存在性/校验钩子
  rule_normalize  规范化检查钩子（参数化入口 + perform 是否存在）
"""
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# 工作区根：本文件在 <proj>/ai-python-methodology/scripts/lib/
PROJECT_ROOT = Path(__file__).resolve().parents[4]
GATE_JS = PROJECT_ROOT / "_scripts" / "gate_check.js"


def _log_path(root: Path, date: Optional[str] = None) -> Path:
    d = date or datetime.now().strftime("%Y-%m-%d")
    return root / "_logs" / f"audit_{d}.log"


class RuleDriver:
    """规则执行器：与业务 Driver 同构，统一 perform(action, params)。

    默认用真实 subprocess（node 调用 gate_check.js）；测试注入 runner 即可 mock。
    """

    def __init__(
        self,
        root: Optional[str] = None,
        runner: Optional[Any] = None,
        timeout: int = 60,
    ) -> None:
        self.root: Path = Path(root) if root else PROJECT_ROOT
        self.runner = runner  # 测试注入；None 时用真实 subprocess
        self.timeout = timeout

    def _run_cmd(self, cmd: list) -> Any:
        if self.runner is not None:
            return self.runner(cmd)
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=self.timeout,
        )

    def perform(self, action: str, params: dict) -> dict:
        if action == "rule_gate":
            return self.rule_gate(params)
        if action == "rule_audit":
            return self.rule_audit(params)
        if action == "rule_flag":
            return self.rule_flag(params)
        if action == "rule_validate":
            return self.rule_validate(params)
        if action == "rule_normalize":
            return self.rule_normalize(params)
        raise ValueError(f"unknown rule action: {action}")

    # ---- 强制令闸门（确定性） ----
    def rule_gate(self, params: dict) -> dict:
        kind = str(params.get("kind", "")).upper()
        reason = str(params.get("reason", ""))
        if kind not in ("READ", "WEB", "WRITE"):
            return {"ok": False, "action": "rule_gate", "code": 44,
                    "message": f"BLOCKED:UNKNOWN_ACTION 未知动作类型 \"{kind}\"。合法: READ/WEB/WRITE"}
        gate = self.root / "_scripts" / "gate_check.js"
        cmd = ["node", str(gate), kind] + ([reason] if reason else [])
        r = self._run_cmd(cmd)
        code = int(getattr(r, "returncode", 1))
        out = ((getattr(r, "stdout", None) or "") + (getattr(r, "stderr", None) or "")).strip()
        return {"ok": code == 0, "action": "rule_gate", "code": code,
                "message": out[-400:] or f"code={code}"}

    # ---- 自动审计（代码副作用，不是模型记的） ----
    def rule_audit(self, params: dict) -> dict:
        line = self.format_audit(params)
        path = _log_path(self.root)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return {"ok": True, "action": "rule_audit", "path": str(path), "line": line}
        except OSError as e:
            return {"ok": False, "action": "rule_audit", "error": str(e)}

    @staticmethod
    def format_audit(params: dict) -> str:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        task = params.get("task", params.get("action", ""))
        verdict = params.get("verdict", params.get("ok", "-"))
        summary = (params.get("summary", "") or "").replace("\n", " ").replace("|", "/")
        signal = (params.get("signal", "") or "").replace("\n", " ").replace("|", "/")
        promote = (params.get("promote", "") or "").replace("\n", " ").replace("|", "/")
        return f"[{ts}] | task={task} | verdict={verdict} | 摘要={summary} | signal={signal} | promote={promote}"

    # ---- 红线前置检查 ----
    def rule_flag(self, params: dict) -> dict:
        redline = params.get("redline", "")
        action = params.get("action", "")
        # 内置示例红线：业务 Driver 不得含审批类写动作（只拉数不决策）。
        # 如需强制（如"非同意动作"），在此判定；未知红线默认放行，需人工定义判定。
        forbidden = params.get("if_action_in", [])
        if forbidden and action in forbidden:
            return {"ok": False, "action": "rule_flag", "redline": redline, "action": action,
                    "message": f"BLOCKED:REDLINE 红线「{redline}」禁止动作「{action}」"}
        return {"ok": True, "action": "rule_flag", "redline": redline, "action": action}

    # ---- 校验钩子 ----
    def rule_validate(self, params: dict) -> dict:
        target = Path(str(params.get("target", "")))
        if not target.exists():
            return {"ok": False, "action": "rule_validate", "error": f"target not found: {target}"}
        return {"ok": True, "action": "rule_validate", "target": str(target)}

    # ---- 规范化检查钩子 ----
    def rule_normalize(self, params: dict) -> dict:
        script = Path(str(params.get("script", "")))
        if not script.exists():
            return {"ok": False, "action": "rule_normalize", "error": f"script not found: {script}"}
        text = script.read_text(encoding="utf-8", errors="replace")
        checks = {
            "has_config_flag": "--config" in text or "-config" in text,
            "has_perform": "perform(" in text,
        }
        ok = all(checks.values())
        return {"ok": ok, "action": "rule_normalize", "checks": checks}
