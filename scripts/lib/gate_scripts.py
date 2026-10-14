# -*- coding: utf-8 -*-
"""lib/gate_scripts.py — 脚本归位程序闸门（工程控制 · 2026-08-31）。

对治：任何会话在新建/落盘脚本文件时未先判归位，把脚本丢到项目根/临时/业务子域，
      未按「统一归档」落位。
原则：新建脚本动作起点强制调用本闸门「先判归位」；目标位置不在允许归档区即 HARD_STOP(42)，
      强制先归位再落盘（用程序拦，不用 LLM 自觉）。与 @经验飞轮 红线 RL-SCRIPT-ARCHIVE 一致。

两种判定（别混，各司其职）：
  A. require_archive(target) —— **预制闸门（严格，面向「将要新建的单个脚本」）**：
     新脚本只允许落「允许区」（相对项目根）：
         _scripts/_archive/   (一次性/探索)
         scripts/<业务域>/    (反复使用·四件套)
     其它（项目根 / 业务子域 / 临时目录）→ HARD_STOP(42)。
     说明：现有业务子域脚本（tkDM/gmass/样品数据多店铺 等）不重新判定，免误伤存量；
           允许区清单可被数据文件 _allowed_script_dirs.json 覆盖（按需加白/收窄）。
  B. scan_loose(root) —— **提交前兜底网（宽容，面向「存量+新增」整树审计）**：
     仅标出「项目根」散落脚本（供 lib/audit 结构闸门），
     因为若用严格允许区扫整树，会把尚未四件套化的存量业务脚本全标红 = 不可用。

用法：
    from lib import gate_scripts as gs
    gs.require_archive("tkDM/foo.py")            # GateError(42)：tkDM 不在允许区
    gs.require_archive("scripts/tkDM/foo.py")    # OK
CLI:
    python -m lib.gate_scripts check <path>          # 判定单个新脚本目标位置
    python -m lib.gate_scripts scan <project_root>   # 扫项目根散落脚本（兜底网）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

__all__ = [
    "HARD_STOP_CODE", "SCRIPT_EXTS", "DEFAULT_ALLOWED", "GateError",
    "find_project_root", "allowed_dirs", "is_allowed", "require_archive", "scan_loose",
    "check_text_encoding",
]

HARD_STOP_CODE = 42  # 对齐 gate_check.js / preflight 的 HARD_STOP(42)

# 脚本扩展名（新建/落盘判定用）
SCRIPT_EXTS = {".py", ".js", ".mjs", ".cjs", ".ps1", ".bat", ".sh", ".ts", ".tsx"}
# 项目根标志：存在才判定「项目根 / 允许区」，脚本库 lib/ 等目录不触发
PROJECT_MARKERS = ("AGENTS.md",)
# 新脚本允许落位（相对项目根）；可被 data/_allowed_script_dirs.json 覆盖
DEFAULT_ALLOWED = ("_scripts/_archive", "scripts")


class GateError(RuntimeError):
    """闸门未通过：目标脚本位置不在允许区（应先归位再落盘）。"""

    def __init__(self, problems: list[str] | str, code: int = HARD_STOP_CODE) -> None:
        self.problems = problems if isinstance(problems, list) else [problems]
        self.code = code
        super().__init__("; ".join(self.problems))


# ---- 编码闸门（Windows 脚本文本，实测复犯类）----------------------------------
# 来历：2026-09-12 用编辑器直写 .ps1（UTF-8 无 BOM）后交给 PowerShell 5.1 执行 →
#       5.1 按 ANSI 读文件，中文被拆成乱码 token，报 UnexpectedToken 直接不跑。
#       同日另一次：bat 的 BOM/换行问题同样静默损坏中文。→ 闸门放在"落盘前"。
BOM_UTF8 = b"\xef\xbb\xbf"
ENCODING_EXTS = (".ps1", ".bat")


def check_text_encoding(path: Path | str) -> list[str]:
    """落盘前的编码闸门：`.ps1` 必须带 UTF-8 BOM；`.bat` 必须**不带** BOM 且用 CRLF。

    只读文件头若干字节判断，**不修改文件**；文件不存在（预检）时返回空（不拦）。
    返回问题列表，空 = 通过。
    """
    p = Path(path)
    if p.suffix.lower() not in ENCODING_EXTS:
        return []
    try:
        raw = p.read_bytes()
    except OSError:
        return []
    if not raw:
        return []
    has_bom = raw.startswith(BOM_UTF8)
    suffix = p.suffix.lower()
    if suffix == ".ps1" and not has_bom:
        return [f"{p.name} 缺 UTF-8 BOM：PowerShell 5.1 会按 ANSI 读 → 中文乱码/解析报错、脚本不执行"
                "（写 .ps1 必须带 BOM；红线 RL-019）"]
    if suffix == ".bat":
        if has_bom:
            return [f"{p.name} 带 UTF-8 BOM：cmd 首行会报错；应为 UTF-8 **无** BOM + CRLF + chcp 65001（红线 RL-019）"]
        if b"\r\n" not in raw:
            return [f"{p.name} 缺 CRLF 换行：cmd 解析异常；应为 UTF-8 无 BOM + CRLF（红线 RL-019）"]
    return []


def find_project_root(path: Path | str) -> Path | None:
    """从 path 向上找含 AGENTS.md 的项目根；找不到返回 None。"""
    p = Path(path).resolve()
    for parent in [p, *p.parents]:
        if (parent / "AGENTS.md").exists():
            return parent
    return None


def allowed_dirs() -> tuple[str, ...]:
    """返回允许区（相对项目根）元组；优先读数据文件 data/_allowed_script_dirs.json。"""
    p = Path(__file__).resolve().parent.parent / "data" / "_allowed_script_dirs.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        got = d.get("allowed")
        return tuple(got) if isinstance(got, list) and got else tuple(DEFAULT_ALLOWED)
    except Exception:
        return tuple(DEFAULT_ALLOWED)


def is_allowed(rel: str) -> bool:
    """判定相对工作区根的脚本归属是否允许（每个 agent/域自包含，便于迁移）。

    允许：
      1. 一次性/探索脚本 → 以 `_scripts/_archive/` 开头（统一归档区）。
      2. 反复使用/业务脚本 → 落在**任一 `<owner>/scripts/` 容器**下（owner=agent/业务域），
         即相对路径中任一分量 == "scripts"。这样每个 agent/域自带 `scripts/`（run_*.py+data+lib+tests，
         四件套）自包含，可整体迁移；共享基础设施（lib/闸门）留公共位。
    额外允许区可经 data/_allowed_script_dirs.json 精确前缀追加。
    """
    parts = [p for p in rel.split("/") if p]
    if rel == "_scripts/_archive" or rel.startswith("_scripts/_archive/"):
        return True
    if "scripts" in parts:
        return True
    allow = allowed_dirs()
    return any(rel == a or rel.startswith(a.rstrip("/") + "/") for a in allow)


def require_archive(target: Path | str, workspace_root: Path | str | None = None) -> None:
    """预制闸门（每个文件夹=工作区）：新脚本目标不归属任何 agent/域容器 → HARD_STOP(42)。非脚本文件不拦。

    workspace_root: 显式指定「工作区根文件夹」（每个文件夹一个工作区）；缺省则向上找含 AGENTS.md 的最近根。
    归属判定见 is_allowed()：`_scripts/_archive/`（一次性）或任意 `<owner>/scripts/`（自包含业务脚本）。
    """
    root = Path(workspace_root).resolve() if workspace_root is not None else find_project_root(target)
    if root is None:
        raise GateError(
            f"无法定位工作区根：{Path(target).resolve()}；请用 --root <文件夹> 显式指定工作区根，或在其下建 AGENTS.md"
        )
    tp = Path(target).resolve()
    try:
        rel = tp.relative_to(root)
    except ValueError:
        raise GateError(f"目标不在工作区根 {root} 之下，禁止把脚本建到工作区外：{tp}")
    if rel.suffix.lower() not in SCRIPT_EXTS:
        return  # 非脚本文件（数据/配置/md 等），不拦
    enc = check_text_encoding(tp)
    if enc:
        raise GateError(enc)
    rels = rel.as_posix().lstrip("./")
    if is_allowed(rels):
        return
    raise GateError(
        f"新脚本须归属到自己的 agent/域容器后落盘：'{rels}'（相对工作区根 {root}）不在允许区。"
        f"一次性/探索 → _scripts/_archive/；反复使用 → <所属agent>/scripts/（或 scripts/<业务域>，四件套：run_*.py+data+lib+tests）。"
        f"每个 agent 脚本自包含，便于整体迁移。红线 RL-SCRIPT-ARCHIVE"
    )


def scan_loose(root: Path | str) -> dict[str, list[str]]:
    """提交前兜底网：仅标出「项目根」散落脚本（=lib/audit 结构闸门，单一来源）。"""
    root = Path(root)
    if not root.is_dir() or not (root / "AGENTS.md").exists():
        return {}
    issues: dict[str, list[str]] = {}
    for f in sorted(root.iterdir()):
        if f.is_file() and f.suffix.lower() in SCRIPT_EXTS:
            issues[str(f)] = [
                f"{f.name} 位于项目根（散落脚本）；应归档到 _scripts/_archive/ 或按四件套落 scripts/<业务域>/（红线 RL-SCRIPT-ARCHIVE）"
            ]
    return issues


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        sys.stdout.write("usage: python -m lib.gate_scripts check <path> [--root <folder>] | scan <root>\n")
        return 2
    cmd = argv[0]
    if cmd == "check":
        target = None
        root = None
        i = 1
        while i < len(argv):
            a = argv[i]
            if a == "--root":
                i += 1
                if i >= len(argv):
                    sys.stderr.write("--root 需要一个文件夹参数\n")
                    return 2
                root = argv[i]
            elif a.startswith("--root="):
                root = a.split("=", 1)[1]
            else:
                target = a
            i += 1
        if not target:
            sys.stdout.write("check 需要目标路径\n")
            return 2
        try:
            require_archive(target, workspace_root=root)
            enc = check_text_encoding(target)
            if enc:
                sys.stderr.write(f"HARD_STOP({HARD_STOP_CODE}): " + "; ".join(enc) + "\n")
                return HARD_STOP_CODE
            sys.stdout.write(f"OK: {target} 在允许区（编码检查通过）\n")
            return 0
        except GateError as e:
            sys.stderr.write(f"HARD_STOP({e.code}): " + "; ".join(e.problems) + "\n")
            return e.code
    if cmd == "scan":
        root = Path(argv[1] if len(argv) > 1 else ".")
        issues = scan_loose(root)
        if not issues:
            sys.stdout.write(f"scan OK: {root} 无工作区根散落脚本\n")
            return 0
        sys.stdout.write(f"发现 {len(issues)} 个工作区根散落脚本：\n")
        for k, v in issues.items():
            sys.stdout.write(f"  {k}  ->  {v[0]}\n")
        return HARD_STOP_CODE
    sys.stdout.write(f"未知命令: {cmd}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
