# -*- coding: utf-8 -*-
"""lib/audit.py — 工程控制自检：扫描代码目录，检出残留的高频反模式（闸门）。

对治：新脚本又引入「硬编码端口」「裸 open() 中文」。
原则：`python -m lib.audit <dir>` 扫目录，检出即返回非零（HARD STOP），让违规
代码无法通过检查。作为 CI/提交前/入口前自检闸门。

检出的反模式：
  1. 硬编码端口字面量（ports.py 里列出的常见漂移端口/5位数字常量），
     应改用 lib.zport 现场探测。
  2. 裸 open( 读/写文本却未显式 encoding='utf-8'；裸 print( 中文在 GBK 控制台乱码。
     应改用 lib.uio（read_utf8/write_utf8）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# 数据驱动红线：从 data/red_lines.json 读信号（@经验飞轮 的『错误清单』）。
# open/print/端口 由本文件内置精确逻辑处理（红线上标 handled_by=audit），
# 其余动态红线（旧路径/未来新增）由此处从清单读取并检出。
from lib import redline as _redline
from lib import gate_scripts as _gs

__all__ = ["KNOWN_PORTS", "audit_file", "audit_dir", "main", "AUDIT_EXIT"]

AUDIT_EXIT = 42

# 常见漂移端口（硬编码即违规；引用 zport 探测是正解）
KNOWN_PORTS = {
    "59446": "SA 店窗口 CDP（示例，会漂移）",
    "59181": "HOV 店窗口 CDP（示例，会漂移）",
    "58163": "AE 店窗口 CDP（示例，会漂移）",
    "52642": "曾有过的 HubStudio CDP（会漂移）",
    "57422": "曾有过的 HubStudio CDP（会漂移）",
    "60306": "曾有过的 HubStudio CDP（会漂移）",
    "55249": "曾有过的店窗口 CDP（会漂移）",
    "50704": "自用hub（会漂移）",
    "62409": "曾有过的 HubStudio CDP（会漂移）",
}

# 脚本归档闸门（RL-SCRIPT-ARCHIVE，2026-08-31）：委托 lib/gate_scripts.py 的 scan_loose（单一来源）。

# 裸 open / print 中文字面量（应走 uio）
_RE_OPEN = re.compile(r"\bopen\s*\(", re.IGNORECASE)
_RE_OPEN_ENCODING = re.compile(r"encoding\s*=", re.IGNORECASE)
_RE_PRINT_CN = re.compile(r"print\s*\([^)]*[\u4e00-\u9fff]")
_RE_HAS_CN = re.compile(r"[\u4e00-\u9fff]")
# print 规则豁免（2026-09-01）：文件自己已把 stdout 重绑成 UTF-8 时，中文 print 不会在
# GBK 控制台乱码，再报就是纯误报（实例：每日导出销量/run_monitor.py 第 21 行已包装，
# 却被逐行正则报了 6 条）。逐行正则看不到文件级的包装，故在文件级先判定一次。
_RE_STDOUT_WRAPPED = re.compile(
    r"sys\.stdout\s*=\s*io\.TextIOWrapper|sys\.stdout\.reconfigure\s*\(|"
    r"from\s+lib(?:\.uio)?\s+import[^\n]*\buio\b|from\s+lib\.uio\s+import|import\s+lib\.uio",
    re.IGNORECASE,
)
# 归档/临时目录（2026-09-01）：一次性废脚本不是"待修的债"，但会把闸门淹掉
# （实测本项目 124 命中文件里 104 个在这些目录，311 条噪声让 audit 恒红=闸门失效）。
# 归档规范见「脚本归档约定.md」：_archive/_tmp/_upload_tmp/_setup 均为非活代码区。
SKIP_DIRS = ("_archive", "_tmp", "_upload_tmp", "_setup", ".opencode", "node_modules", ".git")

# ---------------------------------------------------------------------------
# 红线适用域（applies_to · 2026-09-01）
# ---------------------------------------------------------------------------
# 问题：红线原本只有 `scope`（py/md/all＝**文件类型**），没有**领域**维度，于是
# 「TikTok 导出须点最新第 0 条」「CDP 须走 target-level websockets」这类**域专属**
# 规则会对全树每个 .py 生效——没被误报只是因为正则恰好没命中，不是设计。
#
# 解法：条目可选 `applies_to`，缺省=全域（向后兼容，现有条目行为不变）：
#   "applies_to": {"paths": ["scripts/power/**", "每日导出销量/**"],
#                  "exclude_paths": ["**/tests/**"]}
# 路径按「相对审计根」匹配（匹配不上再试绝对路径），`**` 跨目录、`*` 不跨 `/`。
# 域↔agent↔preset 是可映射的；**运行期**要求（免审批/低推理/必须跑在 preset 下）
# 不属于 audit，应走 preflight。
def _glob_to_re(pattern: str) -> re.Pattern[str]:
    """把 glob（支持 `**`）编成正则。`*` 不跨 `/`，`**` 跨目录。"""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _path_matches(path: Path, root: Optional[Path], patterns) -> bool:
    """path 是否命中任一 glob（先按相对审计根，再按绝对路径）。"""
    if not patterns:
        return False
    cands = [path.as_posix()]
    if root is not None:
        try:
            cands.insert(0, path.relative_to(root).as_posix())
        except ValueError:
            pass
    for pat in patterns:
        rx = _glob_to_re(str(pat).replace("\\", "/"))
        if any(rx.match(c) for c in cands):
            return True
    return False


def rule_applies_to(rl: dict, path: Path, root: Optional[Path] = None) -> bool:
    """红线条目是否适用于该文件（无 `applies_to` = 全域，向后兼容）。"""
    at = rl.get("applies_to")
    if not isinstance(at, dict):
        return True
    if _path_matches(path, root, at.get("exclude_paths") or []):
        return False
    inc = at.get("paths") or []
    return _path_matches(path, root, inc) if inc else True


# ---------------------------------------------------------------------------
# 任务分类（categories · 2026-09-01，用户定的四类）
# ---------------------------------------------------------------------------
# 「SKILL 是为完成一个任务而做的」——红线按**这段代码在干什么**分域：
# 爬虫类 / 自动脚本 / 数据处理类 / 内部DSH调整。
# 相比按目录或按 preset：目录会漂移，preset 是运行期概念（审计者的 preset ≠ 该文件
# 将来被哪个 preset 跑），而 `import playwright` 这类**内容特征是文件自带的**，
# 故分类靠内容识别，配置外置在 data/rule_categories.json。
# 一个文件可同属多类（并集）；红线不写 categories = 全域。
_CATEGORIES_CACHE: Optional[dict] = None


def _load_categories() -> dict:
    global _CATEGORIES_CACHE
    if _CATEGORIES_CACHE is None:
        try:
            import json
            p = Path(__file__).resolve().parent.parent / "data" / "rule_categories.json"
            _CATEGORIES_CACHE = json.loads(p.read_text(encoding="utf-8-sig")).get("categories", {})
        except Exception:
            _CATEGORIES_CACHE = {}
    return _CATEGORIES_CACHE


def file_categories(path: Path, text: str, root: Optional[Path] = None) -> set:
    """识别文件属于哪些任务分类（可多类）。"""
    hit = set()
    for name, spec in _load_categories().items():
        for sig in spec.get("signals") or []:
            try:
                if re.search(sig, text, re.IGNORECASE):
                    hit.add(name)
                    break
            except re.error:
                continue
        if name not in hit and _path_matches(path, root, spec.get("path_hints") or []):
            hit.add(name)
    return hit


def rule_matches_categories(rl: dict, cats: set) -> bool:
    """红线是否命中该文件的分类（无 categories = 全域）。"""
    want = (rl.get("applies_to") or {}).get("categories") if isinstance(rl.get("applies_to"), dict) else None
    if not want:
        return True
    return bool(set(want) & cats)


def rule_no_signal_ok(rl: dict, text: str) -> bool:
    """红线条目是否满足「文件不含某信号」：`applies_to.without_signal`（文件级负条件）。

    默认 True（无 without_signal）。`without_signal` 任一正则命中该文件的整体文本 →
    返回 False（规则不适用该文件）。
    用途：像「crawler 里 `connect_over_cdp` 但**无** `expect_download`（非下载截留）才拦」这类
    域专属 + 文件级负条件——下载截留（`expect_download`）是 playwright `connect_over_cdp`
    合法保留用例，须豁免，但一行行正则看不到「这个文件是否在做下载截留」。
    """
    at = (rl.get("applies_to") or {}).get("without_signal") or []
    for sig in at:
        try:
            if re.search(str(sig), text):
                return False
        except re.error:
            continue
    return True


def _builtin_applies(rule_id: str, path: Path, root: Optional[Path], cats: Optional[set] = None) -> bool:
    """内置检查（端口 RL-002 / open RL-003 / print RL-004）也遵守其红线条目的适用域。"""
    try:
        for rl in _redline.load():
            if rl.get("id") == rule_id:
                if not rule_applies_to(rl, path, root):
                    return False
                return True if cats is None else rule_matches_categories(rl, cats)
    except Exception:
        pass
    return True
# RL-PORTABLE-001 (2026-08-31): 入口脚本经 METHOD_SCRIPTS_ROOT 全局库加载 lib，却缺自包含 _INC_ROOT 钩子。
_RE_LIB_IMPORT = re.compile(r"^\s*from lib import|^\s*import lib\b", re.MULTILINE | re.IGNORECASE)
_RE_METHOD_FALLBACK = re.compile(r"sys\.path\.insert\([^)]*METHOD_SCRIPTS_ROOT")

# 会漂移的事实（现取·P3，2026-08-31）：agent 清单/项目/路径/目录/端口/入口表行「禁止固化旧值」。
# markdown（agent/SKILL/AGENTS.md 正文）是漂移重灾，但散文里合法讨论路径会误报——
# 故漂移信号一律**数据驱动**：red_lines.json 中 scope ∈ {"all","md"} 且 active 的条目才应用 .md
# （面向代码的信号默认 scope=py，不误伤文档）。--include-md 才扫 .md（默认 CI 不扫，避免刷屏）。
def _scope_ok(rl, ftype: str) -> bool:
    """判断绿线条目是否适用于文件类型 ftype（'py'|'md'）。缺省（无 scope）视为 py。"""
    s = rl.get("scope")
    if s in ("all", ftype):
        return True
    if s is None:
        return ftype == "py"
    return False


# .md 内代码围栏（```）内的行属代码，不当作散文漂移；跳过以降低误报。
_RE_FENCE = re.compile(r"^\s*(```|~~~)")


def audit_file(path: Path, root: Optional[Path] = None) -> list[str]:
    """审计单文件，返回违规列表（空=干净）。

    `root` = 审计根，仅用于把红线的 `applies_to.paths` 按相对路径匹配；
    省略时退化为按绝对路径匹配（不影响无 `applies_to` 的条目）。
    """
    # 审计工具自身豁免（2026-08-27 修复 self-flag bug）：audit.py 内容是规则定义
    # （KNOWN_PORTS / 正则 / 注释与 docstring 里提到的 open/print），不是业务违规；
    # 若审计它自己，会把"规则文字"误报为违规，导致任何项目审计都 HARD STOP 42。
    if path.name == "audit.py":
        return []
    issues: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return [f"{path.name}: 读取失败 {e}"]

    lines = text.splitlines()
    # 审计工具自身豁免：audit.py 含检测规则定义（KNOWN_PORTS / 正则表达式），
    # 其中的端口/关键字属于规则，不是业务违规。
    is_tool_self = path.name == "audit.py"
    # stdout 已被本文件重绑为 UTF-8（或已引入 uio）→ 中文 print 不会乱码，豁免 print 规则。
    stdout_wrapped = bool(_RE_STDOUT_WRAPPED.search(text))
    # 任务分类（爬虫/自动脚本/数据处理/内部DSH调整）——按内容识别，红线据此分域。
    _cats = file_categories(path, text, root)
    # 动态红线（数据驱动）：red_lines.json 里 active 且非 handled_by=audit 的信号
    # （open/print/端口由上方内置精确逻辑处理，这里只扫旧路径等可扩展模式）。
    try:
        _dyn_rules = [
            rl for rl in _redline.load()
            if rl.get("status") == "active"
            and str(rl.get("signal") or "").strip()
            and rl.get("handled_by") != "audit"
            and _scope_ok(rl, "py")
            and rule_applies_to(rl, path, root)
            and rule_matches_categories(rl, _cats)
            and rule_no_signal_ok(rl, text)
        ]
    except Exception:
        _dyn_rules = []
    # 内置检查的适用域（缺省全域）
    _ok_port = _builtin_applies("RL-002", path, root, _cats)
    _ok_open = _builtin_applies("RL-003", path, root, _cats)
    _ok_print = _builtin_applies("RL-004", path, root, _cats)
    in_main = False
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("if __name__"):
            in_main = True
        # 跳过 __main__ 自检块里的 print（属正常自检输出，非业务裸输出）
        if in_main and _RE_PRINT_CN.search(line):
            continue
        if is_tool_self:
            # 跳过 KNOWN_PORTS 字典段（端口黑名单定义是规则不是违规）
            if re.match(r"^\s*\"\d+\":", line):
                continue
            if _RE_OPEN.search(line) and line.lstrip().startswith("#"):
                continue
        # 只有含中文且未显式 encoding 的 open() 才算（避免误报不含中文的 open）
        if _ok_open and _RE_OPEN.search(line) and _RE_HAS_CN.search(line) and not _RE_OPEN_ENCODING.search(line):
            issues.append(f"{path.name}:{i} 裸 open() 读含中文且未显式 encoding（应走 uio.read_utf8/write_utf8）")
        if _ok_print and _RE_PRINT_CN.search(line) and not stdout_wrapped:
            issues.append(f"{path.name}:{i} 裸 print() 含中文（GBK 控制台乱码，应走 uio 或 sys.stdout 包装）")
        for port, why in (KNOWN_PORTS.items() if _ok_port else ()):
            # 端口作为独立常量（字符串/数字）出现且非注释 -> 疑似硬编码
            if re.search(rf"(?<![0-9]){re.escape(port)}(?![0-9])", line) and not line.lstrip().startswith("#") and "#" not in line.split(port)[0][-3:]:
                issues.append(f"{path.name}:{i} 疑似硬编码端口 {port}（{why}；应改 lib.zport 探测）")
        # 数据驱动红线：从 red_lines.json 动态读取的信号（open/print/端口已内置处理，这里跳过）
        if not is_tool_self:
            # 检测器/字面量定义行豁免（同 KNOWN_PORTS 理由）：BUILTIN_DETECTORS 等自身含信号
            # 的 regex 是"待检 pattern"，不是业务违规值——逐行扫描会误报，闸门自锁（audit.py 已全文件免，
            # 此处按"字面量/元组行"共性豁免，仍扫描 `x = "..."` 这类真实赋值）。
            _literal_cont = re.match(r"^\s*(\(|[\"'])", line)
            for rl in _dyn_rules:
                if _literal_cont:
                    break
                try:
                    rx = re.compile(rl.get("signal", ""))
                except re.error:
                    continue
                if rx.search(line):
                    issues.append(f"{path.name}:{i} 命中红线[{rl.get('id')}] {rl.get('title')}（{rl.get('hint', '')}）")
    # RL-PORTABLE-001: 入口脚本（from lib import + METHOD_SCRIPTS_ROOT 回退）缺自包含 _INC_ROOT 钩子。
    # 有回退却无钩子 -> 目标机无 ~/Desktop/AI/... 全局方法库时跑不起来；应加 _INC_ROOT 自包含 lib 钩子。
    if (
        not is_tool_self
        and _RE_LIB_IMPORT.search(text)
        and _RE_METHOD_FALLBACK.search(text)
        and "_INC_ROOT" not in text
    ):
        issues.append(
            f"{path.name}: 入口经全局方法库(METHOD_SCRIPTS_ROOT)加载 lib 但缺自包含 _INC_ROOT 钩子"
            "（RL-PORTABLE-001；应加 _INC_ROOT 自包含 lib 钩子，打包附带 scripts/lib 时优先用包内）"
        )
    return issues


def audit_md_file(path: Path) -> list[str]:
    """审计单个 markdown（agent/SKILL/AGENTS.md 正文）的「会漂移的事实」固化。

    只应用 red_lines.json 中 scope ∈ {"all","md"} 且 active 的漂移信号（数据驱动），
    并跳过代码围栏内的行（那是代码，不是散文）。不会对 open()/print()/端口 等代码型规则
    应用到这里（散文里合法讨论会误报）。仅 --include-md 时由 audit_dir 调用。
    """
    if path.name == "audit.py":
        return []
    issues: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return [f"{path.name}: 读取失败 {e}"]

    try:
        # 只取「有内容正则」的红线：signal 为空的是结构型规则（如 RL-SCRIPT-ARCHIVE，
        # 由 gate_scripts 结构性检出），re.compile("") 会匹配每一行，必须排除；
        # handled_by=audit 的由本文件内置精确逻辑处理，同样不走通用正则。
        _md_rules = [
            rl for rl in _redline.load()
            if rl.get("status") == "active"
            and str(rl.get("signal") or "").strip()
            and rl.get("handled_by") != "audit"
            and _scope_ok(rl, "md")
        ]
    except Exception:
        _md_rules = []

    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if _RE_FENCE.search(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for rl in _md_rules:
            try:
                rx = re.compile(rl.get("signal", ""))
            except re.error:
                continue
            if rx.search(line):
                issues.append(
                    f"{path.name}:{i} 命中漂移红线[{rl.get('id')}] {rl.get('title')}（{rl.get('hint', '')}；应走现取命令，勿固化旧值）"
                )
    return issues


def audit_loose_scripts(root: Path) -> dict[str, list[str]]:
    """结构性闸门：检出「项目根」下散落的脚本文件（RL-SCRIPT-ARCHIVE → HARD STOP）。

    委托 lib/gate_scripts.scan_loose（单一来源，防定义漂移）；仅项目根（含 AGENTS.md）触发。
    检出的反模式：脚本文件被直接丢在项目根（或临时目录），未按约定归档。
       - 一次性/探索脚本 → `_scripts/_archive/`
       - 反复使用脚本   → `scripts/<业务域>/`（四件套：run_*.py + data + lib + tests）
    """
    return _gs.scan_loose(root)


def audit_dir(root: Path, include_md: bool = False) -> dict[str, list[str]]:
    """递归审计 .py/.js（含可选 .md）文件，返回 {路径: 违规列表}。

    2026-09: 由「仅 .py」扩展为「.py + .js」——爬虫/抓取反模式（如"打开标签页抢置顶"
    RL-018）大量落在 .js（CDP 客户端），只扫 .py 会漏。node_modules/_archive 等由
    SKIP_DIRS 排除，不受影响。
    第三方/厂商目录（data/skill_lint_exempt.json 登记）整体跳过——代码与文档同理，
    它们随上游包升级覆盖，不是本团队的债。
    """
    result: dict[str, list[str]] = {}
    try:
        from lib import skill_lint as _sl
    except Exception:
        _sl = None
    # 单文件入口：`python -m lib.audit <某文件>` 也应真正审计该文件，
    # 而非因 rglob 不递归到单文件而返回「通过」(2026-09 修：此前单文件审计形同虚设)。
    if root.is_file():
        if root.suffix.lower() in (".py", ".js"):
            iss = audit_file(root, root.parent)
            if iss:
                result[str(root)] = iss
        elif include_md and root.suffix.lower() == ".md":
            iss = audit_md_file(root)
            if iss:
                result[str(root)] = iss
        return result
    for p in sorted(root.rglob("*.py")) + sorted(root.rglob("*.js")):
        if "__pycache__" in p.parts or "site-packages" in p.parts:
            continue
        if any(d in p.parts for d in SKIP_DIRS):
            continue
        if _sl is not None and _sl.is_exempt(p):
            continue
        iss = audit_file(p, root)
        if iss:
            result[str(p)] = iss
    if include_md:
        # 文档侧还要过「写回契约」闸门（lib/skill_lint SL-001~007）：
        # 经验必须落到规则修订而不是散文提醒。第三方 SKILL 由 data/skill_lint_exempt.json 豁免。
        for p in sorted(root.rglob("*.md")):
            if "__pycache__" in p.parts or "site-packages" in p.parts:
                continue
            if any(d in p.parts for d in SKIP_DIRS):
                continue
            if _sl is not None and _sl.is_exempt(p):
                continue
            iss = audit_md_file(p)
            if iss:
                result.setdefault(str(p), []).extend(iss)
            if _sl is None:
                continue
            if not _sl.is_contract_doc(p):
                continue
            try:
                sl_iss = [m for lvl, m in _sl.lint_file(p) if lvl == "ERR"]
            except Exception:
                sl_iss = []
            if sl_iss:
                result.setdefault(str(p), []).extend(sl_iss)
    return result


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else []
    include_md = "--include-md" in argv
    args = [a for a in argv if a != "--include-md"]
    target = args[0] if args else "."
    root = Path(target)
    if not root.exists():
        print(f"目标不存在: {root}", file=__import__("sys").stderr)
        return AUDIT_EXIT
    found = audit_dir(root, include_md=include_md)
    # 结构闸门：项目根散落脚本（RL-SCRIPT-ARCHIVE）并入结果
    for f, iss in audit_loose_scripts(root).items():
        found.setdefault(str(f), []).extend(iss)
    if not found:
        print(
            "Audit 通过：未检出硬编码端口 / 裸 open 中文 / 裸 print 中文"
            + (" / 漂移事实。", "。")[include_md is False]
        )
        return 0
    print(f"Audit 发现 {len(found)} 个文件有问题：")
    for f, iss in found.items():
        print(f"  {f}")
        for x in iss:
            for line in x.splitlines():
                print(f"    - {line}")
    return AUDIT_EXIT


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
