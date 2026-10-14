# -*- coding: utf-8 -*-
"""lib/skill_lint.py — SKILL / agent 文档的「写回契约」闸门（工程控制层）。

对治（2026-09-01 用户指出的方法层缺陷）：
    经验飞轮 S4 写回长期只有「追加一条散文实测坑」这一个抽屉，导致
    SOP 正文永远不动、坑区无限膨胀、被推翻的旧结论与新结论并存互相矛盾，
    读者必须自己从散文里重建判定。**修的是提醒，不是方法。**

原则（与 lib/audit.py 同构）：规则变程序闸门，不靠 LLM 自觉。
    `python -m lib.skill_lint <文件或目录>` 检出即返回 42（HARD STOP）。

检出的反模式：
    SL-001 骨架缺段        缺少标准契约章节（定位/触发/步骤/维护约定）
    SL-005 判定源重复      同一判定表表头在文件内出现 >=2 次（改一处必漏另一处）
    SL-006 只增不改宪法    正文写有「只增不改」等禁止修订旧结论的措辞
    SL-007 旧坑区标题      仍用 `## 实测坑` 这类「提醒抽屉」标题（应写进判定表/步骤）
    SL-008 变更记录段      出现 `## 变更记录` 段（2026-09-28 用户令：一律不得写）

（SL-002/003/004 已于 2026-09-19 随「## 变更记录」段落退役：正文不再承载条目簿，
 写回证据＝git 提交，追溯看 `git log`/`git diff`；编号不回收，避免旧引用错位。
 SL-008 是 2026-09-28 用户令的**硬禁止**：新写变更记录段即拦，不靠自觉。）
"""
from __future__ import annotations

import re
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

from lib import uio

__all__ = [
    "LINT_EXIT",
    "load_exempt", "is_exempt", "lint_file", "lint_dir", "main",
]

LINT_EXIT = 42


# 标准骨架：HARD = 缺即拦；SOFT = 缺只告警（方法型 SKILL 可无前置/判定表）
_HARD_SECTIONS = [
    ("定位", (r"定位",)),
    ("触发", (r"何时用", r"触发")),
    ("步骤", (r"怎么用", r"步骤", r"流程", r"执行")),
    ("维护约定", (r"维护约定",)),
]
_SOFT_SECTIONS = [
    ("前置条件", (r"前置",)),
    ("判定表", (r"判定表", r"路由", r"分支")),
]

# 声称推翻旧结论的措辞
# 禁止修订旧结论的宪法级措辞
_RE_FROZEN = re.compile(r"只增不改|只追加不修改|不得修改旧(?:条目|结论|坑)|保留原文不改")
# 规则定义行豁免（EF-002：闸门自身必须先自豁免，否则闸门锁死自己）：
# 「禁止/不得/勿写/违规/拦」或引用 SL-xxx / skill_lint 的行，是在**定义或禁止**该措辞，不是在使用它。
_RE_FROZEN_EXEMPT = re.compile(r"禁止|不得|不许|勿写|违规|拦|SL-\d{3}|skill_lint")
# 旧「提醒抽屉」标题
_RE_OLD_PIT_HEAD = re.compile(r"^#{2,3}\s*(实测坑|踩坑记录|踩过的坑|经验记录)")

# 变更记录段（2026-09-28 用户令：SKILL / pipeline 一律不得再写变更记录段）
# 写回证据 = git 提交；追溯看 git log / git diff，正文不承载条目簿。
_RE_CHANGELOG_HEAD = re.compile(r"^#{1,3}\s*(变更记录|变更日志|版本记录|修订记录|更新记录|changelog)\s*$", re.I)
# 规则定义行豁免（定义/禁止该段的行不算违规，否则闸门锁死自己）
_RE_CHANGELOG_EXEMPT = re.compile(r"禁止|不得|不许|勿写|违规|拦|SL-\d{3}|skill_lint|不再|已退役")

_RE_H2 = re.compile(r"^#{2,3}\s+(.*?)\s*$")
_RE_FENCE = re.compile(r"^\s*(```|~~~)")
_RE_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")

_MSG_CLEAN = "skill_lint 通过：骨架完整 / 判定源唯一 / 无冻结措辞 / 无旧坑区标题"
_LEVEL_ERR = "ERR"
_LEVEL_WARN = "WARN"


def _sections(lines: list[str]) -> list[tuple[str, int, int]]:
    """切出 (标题, 起始行号1基, 结束行号) 列表；正文围栏内的 # 不算标题。"""
    heads: list[tuple[str, int]] = []
    in_fence = False
    for i, line in enumerate(lines, 1):
        if _RE_FENCE.search(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _RE_H2.match(line)
        if m:
            heads.append((m.group(1), i))
    out: list[tuple[str, int, int]] = []
    for idx, (title, start) in enumerate(heads):
        end = heads[idx + 1][1] - 1 if idx + 1 < len(heads) else len(lines)
        out.append((title, start, end))
    return out


def _find_section(secs: list[tuple[str, int, int]], patterns: tuple[str, ...]):
    for title, start, end in secs:
        for pat in patterns:
            if re.search(pat, title):
                return (title, start, end)
    return None


_RE_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _header_cells(line: str) -> list[str]:
    cells = [re.sub(r"[*`\s]", "", c) for c in line.strip().strip("|").split("|")]
    return [c for c in cells if c]


def _table_headers(lines: list[str]) -> list[tuple[int, list[str], set[str]]]:
    """取出真正的表头行（下一行是 |---|---| 分隔行），连同该表的首列取值集合（行键）。

    只比表头会把「参数表 vs 返回字段表」这类同构但不同内容的表误判为重复，
    因此同时收集行键：真正被抄写的判定表会重复同样的行键。
    """
    out: list[tuple[int, list[str], set[str]]] = []
    in_fence = False
    for i, line in enumerate(lines):
        if _RE_FENCE.search(line):
            in_fence = not in_fence
            continue
        if in_fence or not _RE_TABLE_ROW.match(line):
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if not _RE_TABLE_SEP.match(nxt):
            continue
        cells = _header_cells(line)
        if len(cells) < 3:
            continue
        keys: set[str] = set()
        for j in range(i + 2, len(lines)):
            row = lines[j]
            if not _RE_TABLE_ROW.match(row):
                break
            rc = _header_cells(row)
            if rc:
                keys.add(rc[0])
        out.append((i + 1, cells, keys))
    return out


def lint_file(path: Path) -> list[tuple[str, str]]:
    """检查单个 SKILL / agent markdown，返回 [(level, msg)]；空=干净。"""
    # 闸门自身豁免：本文件的正则与 docstring 含全部待检信号，逐行扫会自锁。
    if path.name == "skill_lint.py":
        return []
    issues: list[tuple[str, str]] = []
    text = uio.read_utf8(path)
    if not text:
        return [(_LEVEL_ERR, f"{path.name}: 读取为空或不存在")]
    lines = text.splitlines()
    secs = _sections(lines)
    name = path.name

    # SL-001 骨架缺段（只对 SKILL.md 生效；agent/AGENTS.md 有自己的骨架，
    # 但 SL-005~007 对所有契约文档一律适用）
    is_skill = path.name.lower() == "skill.md"
    if is_skill:
        for label, pats in _HARD_SECTIONS:
            if _find_section(secs, pats) is None:
                issues.append((
                    _LEVEL_ERR,
                    f"{name}: SL-001 骨架缺段，缺少 [{label}] 章节"
                    f"（标准骨架：定位/触发/前置/判定表/步骤/程序入口/维护约定）",
                ))
        for label, pats in _SOFT_SECTIONS:
            if _find_section(secs, pats) is None:
                issues.append((_LEVEL_WARN, f"{name}: SL-001 建议补 [{label}] 章节（方法型 SKILL 可豁免）"))

    # SL-007 旧「提醒抽屉」标题
    in_fence = False
    for i, line in enumerate(lines, 1):
        if _RE_FENCE.search(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _RE_OLD_PIT_HEAD.match(line):
            issues.append((
                _LEVEL_ERR,
                f"{name}:{i} SL-007 仍用「提醒抽屉」标题（{line.strip()}）"
                f"；应把结论写进判定表/步骤，不留提醒抽屉",
            ))

    # SL-008 变更记录段（2026-09-28 用户令）：SKILL / pipeline 一律不得再写变更记录段。
    # 写回证据 = git 提交；追溯看 git log / git diff。
    in_fence = False
    for i, line in enumerate(lines, 1):
        if _RE_FENCE.search(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _RE_CHANGELOG_HEAD.match(line) and not _RE_CHANGELOG_EXEMPT.search(line):
            issues.append((
                _LEVEL_ERR,
                f"{name}:{i} SL-008 出现「变更记录」段（{line.strip()}）"
                f"；用户令 2026-09-28：SKILL / pipeline 一律不得写变更记录段，"
                f"写回证据看 git 提交（git log / git diff）",
            ))

    # SL-005 判定源重复：表头首列相同 + ≥2 个同名列 + ≥2 个相同行键 => 同一判定被抄了多份
    heads = _table_headers(lines)
    for a in range(len(heads)):
        for b in range(a + 1, len(heads)):
            ln_a, cells_a, keys_a = heads[a]
            ln_b, cells_b, keys_b = heads[b]
            shared_cols = set(cells_a) & set(cells_b)
            shared_keys = keys_a & keys_b
            if cells_a[0] == cells_b[0] and len(shared_cols) >= 2 and len(shared_keys) >= 2:
                cols = "/".join(sorted(shared_cols))
                rows = "/".join(sorted(shared_keys)[:4])
                issues.append((
                    _LEVEL_ERR,
                    f"{name}: SL-005 判定源重复，第 {ln_a} 行与第 {ln_b} 行表头首列同为"
                    f" [{cells_a[0]}]，共有列 [{cols}]，且重复行键 [{rows}]"
                    f"；同一判定只允许一个权威位置，其余处改为锚点引用",
                ))

    # SL-006 只增不改宪法
    in_fence = False
    for i, line in enumerate(lines, 1):
        if _RE_FENCE.search(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _RE_FROZEN.search(line) and not _RE_FROZEN_EXEMPT.search(line):
            issues.append((
                _LEVEL_ERR,
                f"{name}:{i} SL-006 出现禁止修订旧结论的措辞（{line.strip()[:40]}）"
                f"；写回契约要求经验必须落到规则修订，不是冻结追加",
            ))
    return issues


def load_exempt() -> list[dict]:
    """读豁免登记表（data/skill_lint_exempt.json）。只登记第三方/厂商 SKILL。"""
    path = Path(__file__).resolve().parent.parent / "data" / "skill_lint_exempt.json"
    data = uio.read_json(path, default={}) or {}
    items = data.get("exempt") or []
    return [x for x in items if isinstance(x, dict) and x.get("match")]


def is_exempt(path: Path, rules: Optional[list[dict]] = None) -> Optional[dict]:
    """路径是否被豁免：对路径上的**任一目录名**做 fnmatch（覆盖 references/ 等子目录）。"""
    rules = load_exempt() if rules is None else rules
    parts = [p for p in path.resolve().parts]
    for rl in rules:
        pat = str(rl.get("match"))
        for seg in parts:
            if fnmatch(seg, pat):
                return rl
    return None


def is_contract_doc(path: Path) -> bool:
    """该文档是否受「写回契约」约束。

    契约管的是**承载经验与判定的文档**：SKILL.md 与 agent 定义（`agent/` `agents/` 下的 .md）。
    `references/**` 这类纯参考页、素材页不在其列——要求它们写成契约文档属过度适用
    （与 SL-005 曾把 API 字段表判为重复判定同类的精度问题）。
    显式把单个文件传给本工具时不受此限制，由调用者负责。
    """
    if path.name.lower() == "skill.md":
        return True
    parts = {p.lower() for p in path.parts}
    if ("agent" in parts or "agents" in parts) and "references" not in parts:
        return True
    return False


def lint_dir(root: Path, all_md: bool = False, use_exempt: bool = True) -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
    """递归检查目录下的 SKILL.md（all_md=True 时检查全部 .md）。

    返回 (违规映射, 被豁免的文件列表)。豁免来自 data/skill_lint_exempt.json，
    只用于第三方/厂商 SKILL；自家 SKILL 不得登记豁免。
    """
    result: dict[str, list[tuple[str, str]]] = {}
    skipped: list[str] = []
    rules = load_exempt() if use_exempt else []
    pattern = "*.md" if all_md else "SKILL.md"
    for p in sorted(root.rglob(pattern)):
        if "__pycache__" in p.parts or "node_modules" in p.parts:
            continue
        if rules and is_exempt(p, rules):
            skipped.append(str(p))
            continue
        if not is_contract_doc(p):
            continue
        iss = lint_file(p)
        if iss:
            result[str(p)] = iss
    return result, skipped


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else []
    all_md = "--all-md" in argv
    warn_only = "--warn" in argv
    use_exempt = "--no-exempt" not in argv
    args = [a for a in argv if not a.startswith("--")]
    target = Path(args[0]) if args else Path(".")
    if not target.exists():
        sys.stderr.write(f"target not found: {target}\n")
        return LINT_EXIT

    skipped: list[str] = []
    if target.is_file():
        found = {str(target): lint_file(target)}
    else:
        found, skipped = lint_dir(target, all_md=all_md, use_exempt=use_exempt)
    found = {k: v for k, v in found.items() if v}

    n_err = sum(1 for v in found.values() for lvl, _ in v if lvl == _LEVEL_ERR)
    if skipped:
        note = f"skill_lint: {len(skipped)} file(s) exempt by data/skill_lint_exempt.json (third-party)"
        print(note)
    if not found:
        print(_MSG_CLEAN)
        return 0

    header = f"skill_lint: {len(found)} file(s) flagged, {n_err} error(s)"
    print(header)
    for f, iss in found.items():
        print(f"  {f}")
        for lvl, msg in iss:
            print(f"    [{lvl}] {msg}")
    if n_err and not warn_only:
        return LINT_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
