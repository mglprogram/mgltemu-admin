# -*- coding: utf-8 -*-
"""substitute_rules.py — TEMU（BEL / BHY）「替发 / 拆解」规则库的读写入口。

数据在 config/temu_stock_rules.json，本模块只做查询与推算，**不碰浏览器、不改台账**。

三件事：
    · groups  ：可互相替发的一组款（双向，取组内最大一份，不相加）
    · 所有变体 ：`2032-所有变体` = 该款号下全部套系（4SetA/4SetB/5SetA…）
    · recipes ：拆解/组合（单向），目标款可得 = min(组件库存 ÷ 用量)

用法：
    from substitute_rules import RULES
    RULES.are_substitutable("2038-4SetA", "2038-4SetB")       # True
    RULES.siblings("202504-5SetB")                            # 同组其它款（按目录展开）
    RULES.best("202504-5SetB", "L", stock_fn)                 # (件数, 来源描述)

命令行：
    python scripts/goods/substitute_rules.py --check           # 校验 + 目录里落不到的规则
    python scripts/goods/substitute_rules.py --of 202504-5SetB # 这款跟谁可互顶 + 各组库存
    python scripts/goods/substitute_rules.py --pair A B        # 两款能不能互顶
    python scripts/goods/substitute_rules.py --report          # 全量分组 + 现货
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "config" / "temu_stock_rules.json"
DATA = ROOT / "temu-admin" / "public" / "data"

# TEMU 页面/台账里带的前缀（BEL-202504-5SetA / BK-2032-4SetA / BLA-E2032-8SetB …）
STORE_PREFIXES = (
    "BK2-", "BK-", "BH2-", "BH-", "BLA-", "BS2-", "BS-", "BEL-", "BHY-", "TM-",
)
FAMILY_MARKS = ("所有变体", "*")


def style_of(code: str) -> str:
    """剥掉店铺/品牌前缀：BEL-202504-5SetA → 202504-5SetA。"""
    s = str(code or "").strip()
    changed = True
    while changed:
        changed = False
        for p in STORE_PREFIXES:
            if s.upper().startswith(p) and len(s) > len(p):
                s = s[len(p):]
                changed = True
                break
    return s


def base_of(style: str) -> str:
    """款号（第一个 - 之前）：202504-5SetA → 202504。"""
    return style_of(style).split("-", 1)[0].upper()


def _is_family(token: str) -> bool:
    t = str(token or "").strip()
    return any(t.endswith(m) for m in FAMILY_MARKS)


def _token_base(token: str) -> str:
    t = str(token or "").strip()
    for m in FAMILY_MARKS:
        if t.endswith(m):
            t = t[: -len(m)]
            break
    return base_of(t)


def _match(token: str, style: str) -> bool:
    """token 能代表这 style 吗？显式款号严格相等；family 只要款号相同。"""
    s = style_of(style).upper()
    if not s:
        return False
    if _is_family(token):
        return _token_base(token) == base_of(s)
    return style_of(token).upper() == s


def _overlap(t1: str, t2: str) -> bool:
    """两个 token 是否指向同一批货（用来把规则里分几行写的组并起来）。"""
    if _is_family(t1) or _is_family(t2):
        return _token_base(t1) == _token_base(t2)
    return style_of(t1).upper() == style_of(t2).upper()


class Rules:
    """规则库。构造时读 JSON，构造完 groups 已按交集合并成连通组。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or RULES_PATH)
        cfg = json.loads(self.path.read_text(encoding="utf-8"))
        self.cfg = cfg
        self.borrow_days = float(cfg.get("borrow_days") or 3.0)
        self.stores = [str(s).upper() for s in (cfg.get("stores") or [])]
        self.raw_groups = [list(g.get("members") or []) for g in (cfg.get("groups") or [])]
        self.group_notes = [str(g.get("note") or "") for g in (cfg.get("groups") or [])]
        self.groups, self._origin = self._merge(self.raw_groups)
        self.recipes = list(cfg.get("recipes") or [])

    # ---------- 组装 ----------
    @staticmethod
    def _merge(raw: list[list[str]]) -> tuple[list[list[str]], list[list[int]]]:
        """把「有几行写了同款」的组并成一个连通组（并查集）。"""
        n = len(raw)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            a, b = find(i), find(j)
            if a != b:
                parent[b] = a

        for i in range(n):
            for j in range(i + 1, n):
                if any(_overlap(a, b) for a in raw[i] for b in raw[j]):
                    union(i, j)
        buckets: dict[int, list[str]] = {}
        origin: dict[int, list[int]] = {}
        for i, toks in enumerate(raw):
            r = find(i)
            cur = buckets.setdefault(r, [])
            for t in toks:
                if not any(_overlap(t, x) or style_of(t).upper() == style_of(x).upper() for x in cur):
                    cur.append(t)
            origin.setdefault(r, []).append(i)
        order = sorted(buckets, key=lambda r: min(origin[r]))
        return [buckets[r] for r in order], [origin[r] for r in order]

    # ---------- 查询 ----------
    def group_of(self, style: str) -> list[str] | None:
        """这 style 落在哪个（合并后的）组，返回组成员 token；不在任何组返回 None。"""
        s = style_of(style)
        if not s:
            return None
        for g in self.groups:
            if any(_match(t, s) for t in g):
                return g
        return None

    def group_id(self, style: str) -> str:
        g = self.group_of(style)
        return "" if g is None else "|".join(g)

    def are_substitutable(self, a: str, b: str) -> bool:
        sa, sb = style_of(a), style_of(b)
        if not sa or not sb:
            return False
        for g in self.groups:
            if any(_match(t, sa) for t in g) and any(_match(t, sb) for t in g):
                return True
        return False

    def siblings(self, style: str, catalog=None) -> list[str]:
        """同组其它款。给了 catalog（款号集合）就只返回真实存在的。"""
        s = style_of(style)
        g = self.group_of(s)
        if not g:
            return []
        if catalog is None:
            return [t for t in g if style_of(t).upper() != s.upper()]
        out = []
        for c in catalog:
            cs = style_of(c)
            if cs.upper() == s.upper():
                continue
            if any(_match(t, cs) for t in g) and cs not in out:
                out.append(cs)
        out.sort()
        return out

    def recipe_of(self, style: str) -> list[list[dict]]:
        s = style_of(style).upper()
        out = []
        for r in self.recipes:
            if style_of(r.get("target") or "").upper() == s:
                ways = r.get("variants") or []
                if not ways and r.get("components"):
                    ways = [r["components"]]
                out.extend([list(w) for w in ways])
        return out

    # ---------- 推算 ----------
    def assemble(self, style: str, size: str, stock_fn) -> tuple[float, str]:
        """拆解：按 recipes 算这尺码能拆出几件。stock_fn(style, size) → 可用件数。"""
        best, why = 0.0, ""
        for way in self.recipe_of(style):
            cap = None
            parts = []
            for comp in way:
                q = float(comp.get("qty") or 1) or 1.0
                have = float(stock_fn(style_of(comp.get("style") or ""), size) or 0)
                n = int(have // q)
                cap = n if cap is None else min(cap, n)
                parts.append("%s×%g" % (style_of(comp.get("style") or ""), q))
            if cap and cap > best:
                best, why = float(cap), "拆解 " + "+".join(parts)
        return best, why

    def best(self, style: str, size: str, stock_fn, catalog=None,
             own: float | None = None, daily: float | None = None) -> tuple[float, str]:
        """替发 + 拆解取最优。返回 (件数, 来源)；来源为空表示用本款自己的。

        只在「本款吃紧」时才算：own <= 0 或 可售天数 <= borrow_days。
        """
        s = style_of(style)
        own_v = float(stock_fn(s, size) if own is None else own)
        tight = own_v <= 0
        if not tight and daily and daily > 0:
            tight = (own_v / daily) <= self.borrow_days
        if not tight:
            return 0.0, ""
        best, why = own_v, ""
        for sib in self.siblings(s, catalog):
            v = float(stock_fn(sib, size) or 0)
            if v > best:
                best, why = v, "替发 %s" % sib
        qty, src = self.assemble(s, size, stock_fn)
        if qty > best:
            best, why = qty, src
        return best, why

    # ---------- 校验 ----------
    def validate(self, catalog=None) -> list[str]:
        """返回问题清单（空列表 = 没问题）。"""
        msgs = []
        if not self.groups:
            msgs.append("规则里没有任何 groups")
        for i, g in enumerate(self.groups):
            # 只有一个 `X-所有变体` 成员是合法写法：等于「该款号内所有套系互通」
            if len(g) < 2 and not (len(g) == 1 and _is_family(g[0])):
                msgs.append("第 %d 组只有 1 个成员：%s" % (i + 1, g))
        if catalog:
            cat = {style_of(c).upper() for c in catalog}
            for g in self.groups:
                hit = [t for t in g if _is_family(t) and any(_token_base(t) == base_of(c) for c in cat)]
                if all(_is_family(t) for t in g) and not hit:
                    msgs.append("规则 %s 在在售款里一款都没找到" % "|".join(g))
                for t in g:
                    if _is_family(t):
                        continue
                    if style_of(t).upper() not in cat:
                        msgs.append("款号不在在售目录里：%s（规则 %s）" % (t, "|".join(g)))
            for r in self.recipes:
                tgt = style_of(r.get("target") or "").upper()
                if tgt and tgt not in cat:
                    msgs.append("拆解目标不在在售目录里：%s" % tgt)
                for way in (r.get("variants") or [r.get("components") or []]):
                    for comp in way:
                        c = style_of(comp.get("style") or "").upper()
                        if c and c not in cat:
                            msgs.append("拆解组件不在在售目录里：%s（→ %s）" % (c, tgt))
        return msgs


# ---------- 目录 / 库存（只读） ----------
def catalog() -> set[str]:
    """在售款号集合（BEL/BHY 的 code 去掉店铺前缀）。"""
    out: set[str] = set()
    try:
        goods = json.loads((DATA / "goods.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    for st in ("BEL", "BHY"):
        for it in ((goods.get("stores") or {}).get(st) or {}).get("items") or []:
            if it.get("status") and it.get("status") != "在售中":
                continue
            c = style_of(it.get("code") or "")
            if c:
                out.add(c)
    return out


def _stock_fn():
    """(款号, 尺码) → 可用件数（恒信可用 + 帕沃尔两仓现货），与 presale_plan 同口径。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import presale_plan as PP  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return lambda code, size: 0.0, {}, {}
    lx, pw, _ = PP._idx()

    def fn(code: str, size: str) -> float:
        k = (style_of(code), str(size).upper())
        return float((lx.get(k) or {}).get("avail") or 0) + float(pw.get(k) or 0)

    return fn, lx, pw


RULES = Rules()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TEMU 替发/拆解规则库")
    ap.add_argument("--check", action="store_true", help="校验规则 + 对照在售目录")
    ap.add_argument("--of", default="", help="看这款跟谁可互顶")
    ap.add_argument("--pair", nargs=2, default=[], metavar=("A", "B"), help="两款能否互顶")
    ap.add_argument("--report", action="store_true", help="全量分组 + 现货")
    ap.add_argument("--rules", default="", help="规则文件路径")
    a = ap.parse_args(argv)
    r = Rules(Path(a.rules)) if a.rules else RULES
    cat = catalog()

    if a.check:
        print("规则 %s：%d 组 / %d 个拆解目标 / 触发线 可售<=%g 天"
              % (r.path.name, len(r.groups), len({x.get('target') for x in r.recipes}), r.borrow_days))
        msgs = r.validate(cat)
        if not msgs:
            print("✅ 全部规则都能在在售目录里落地")
        else:
            print("⚠ %d 条提示：" % len(msgs))
            for m in msgs:
                print("  - " + m)
        return 0

    if a.pair:
        x, y = style_of(a.pair[0]), style_of(a.pair[1])
        ok = r.are_substitutable(x, y)
        print("%s ↔ %s：%s%s" % (x, y, "可互顶" if ok else "不在同一组",
                                 ("（组 %s）" % r.group_id(x)) if ok else ""))
        return 0

    if a.of:
        s = style_of(a.of)
        g = r.group_of(s)
        fn, _, _ = _stock_fn()
        if g is None:
            print("%s：不在任何替发组里，也没有拆解配方" % s)
        else:
            print("%s 所在组：%s" % (s, " | ".join(g)))
            for m in r.siblings(s, cat):
                print("  同组在售：%-22s 现货 %s" % (m, {sz: fn(m, sz) for sz in ("S", "M", "L", "XL", "XXL")}))
        for way in r.recipe_of(s):
            print("  可拆解：" + " + ".join("%s×%g" % (c["style"], c.get("qty") or 1) for c in way))
        return 0

    if a.report:
        fn, _, _ = _stock_fn()
        sizes = ("S", "M", "L", "XL", "XXL")
        for i, g in enumerate(r.groups):
            members = []
            for t in g:
                if _is_family(t):
                    members.extend(sorted(c for c in cat if base_of(c) == _token_base(t)))
                else:
                    members.append(style_of(t))
            members = sorted(set(members))
            print("[%d] %s" % (i + 1, " | ".join(g)))
            for m in members:
                live = "" if m in cat else "（不在售）"
                tot = sum(fn(m, sz) for sz in sizes)
                print("    %-24s%-6s 现货 %5.0f" % (m, live, tot))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
