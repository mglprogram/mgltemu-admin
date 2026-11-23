# -*- coding: utf-8 -*-
"""按店铺建「库存换算数据库」：货号 → 尺码 → 各仓可用量 + 套装/替代映射结果。

用户 2026-11-23：「每个店铺建立对应店铺的数据库」。

每店一个文件：`data/store-db/<STORE>.json`
    {
      "store": "BHY",
      "builtAt": "...",
      "warehouses": {"hx": "恒信加州3", "ca3": "加州3仓", "kansas": "kansas仓"},
      "ratio": 0.8,
      "items": {
         "BHY-102105-3BGR": {
             "spu": "...",
             "sizes": {
                "S": {"lx": 123.0, "pw_ca3": 45.0, "pw_kansas": 2.0,
                      "lx_eff": 123.0, "pw_ca3_eff": 45.0,   # 经过映射修正后的生效值
                      "src": "direct" | "alias:N202201" | "compose:207220-4SetA×1",
                      "hxTarget": 98, "ca3Target": 36, "kansasTarget": 0}
             }
         }
      },
      "rules_matched": [...]
    }

映射规则来自 `_logs/套装映射.json`（bundle_map.py 生成）。
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "temu-admin" / "public" / "data"
SNAP = ROOT / "data" / "stock-snapshot"
OUTDIR = ROOT / "data" / "store-db"
RULES_P = ROOT / "_logs" / "套装映射.json"
RATIO = 0.8

#: 每店的仓名（TEMU 后台叫法不同）
WH = {
    "BHY": {"hx": "恒信加州3",   "ca3": "加州3仓", "kansas": "kansas仓"},
    "BEL": {"hx": "恒信加州3仓", "ca3": "加州3仓", "kansas": "kansas仓"},
    "KLS": {"hx": "恒信加州3",   "ca3": "加州3仓", "kansas": "kansas仓"},
}
CANON = {"KLS": "KinglamanSport"}


def idx(path: Path, field: str, wh_kw: str = "") -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    out: dict = {}
    for it in d.get("items") or []:
        if wh_kw and wh_kw not in str(it.get("wh") or ""):
            continue
        code = str(it.get("code") or "")
        var = str(it.get("variant") or "")
        size = str(it.get("size") or "").upper()
        if not code or not size:
            continue
        k = (("%s-%s" % (code, var)).upper(), size)
        out[k] = out.get(k, 0.0) + float(it.get(field) or 0)
    return out


def load_rules(store: str) -> dict:
    """按店读映射：`_logs/套装映射_<店>.json`。没有就返回空（不跨店套用）。"""
    p = ROOT / "_logs" / ("套装映射_%s.json" % store.upper())
    if not p.exists():
        return {}
    r = json.loads(p.read_text(encoding="utf-8")).get("rules") or []
    return {str(x["key"]).upper(): x for x in r}


def lookup(idxmap: dict, key: str, size: str) -> tuple:
    """按 (key, size) 查；key 已归一到大写。找不到返回 (0.0, '')"""
    v = idxmap.get((key.upper(), size))
    if v is None:
        return 0.0, ""
    return float(v), "direct"


def resolve(idxmap: dict, rules: dict, base: str, size: str, depth: int = 0) -> tuple:
    """返回 (数量, 来源说明)。支持 alias / compose 递归一层。"""
    if depth > 3:
        return 0.0, ""
    v, src = lookup(idxmap, base, size)
    if v:
        return v, src
    r = rules.get(base.upper())
    if not r:
        return 0.0, ""
    kind = r.get("kind")
    if kind == "alias":
        for a in r.get("alias") or []:
            v, _ = resolve(idxmap, rules, a, size, depth + 1)
            if v:
                return v, "alias:%s" % a
    elif kind == "compose":
        total = 0.0
        parts = []
        for p in r.get("parts") or []:
            v, _ = resolve(idxmap, rules, p["key"], size, depth + 1)
            if v:
                total += v * int(p.get("mult") or 1)
                parts.append("%s×%s" % (p["key"], p.get("mult") or 1))
        if total:
            return total, "compose:" + "+".join(parts)
    return 0.0, ""


def build_store(store: str, day: str) -> dict:
    lx = idx(DATA / "linxing-stock.json", "avail")
    ca3 = idx(DATA / "pw-stock.json", "stock", "加州3仓")
    ks = idx(DATA / "pw-stock.json", "stock", "kansas仓")
    rules = load_rules(store)
    # 只处理「在售中」货号（用户 2026-11-23：只修改在售中的库存）
    onsale_p = OUTDIR / ("%s.onsale.txt" % store)
    onsale = None
    if onsale_p.exists():
        onsale = {x.strip() for x in onsale_p.read_text(encoding="utf-8").splitlines() if x.strip()}
    snap = json.loads((SNAP / ("%s.json" % day)).read_text(encoding="utf-8"))
    rows = ((snap.get("stores") or {}).get(CANON.get(store.upper(), store)) or {}).get("rows") or []
    items: dict = {}
    matched = set()
    skipped = set()
    for r in rows:
        code = str(r.get("code") or "")
        if onsale is not None and code not in onsale:
            skipped.add(code)
            continue
        size = str(r.get("size") or "").upper()
        base = code.split("-", 1)[1] if "-" in code else code
        lx_eff, src_lx = resolve(lx, rules, base, size)
        ca_eff, src_ca = resolve(ca3, rules, base, size)
        ks_v, _ = lookup(ks, base, size)
        if src_lx:
            matched.add(src_lx)
        if src_ca:
            matched.add(src_ca)
        hx = int(math.ceil(lx_eff * RATIO)) if lx_eff > 0 else 0
        ca = int(math.ceil(ca_eff * RATIO)) if ca_eff > 0 else 0
        items.setdefault(code, {"spu": str(r.get("spu") or ""), "sizes": {}})
        items[code]["sizes"][size] = {
            "lx": lx_eff, "pw_ca3": ca_eff, "pw_kansas": ks_v,
            "src_lx": src_lx or "missing", "src_ca3": src_ca or "missing",
            "hxTarget": hx, "ca3Target": ca, "kansasTarget": 0,
        }
    return {"store": store, "builtAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "date": day, "warehouses": WH.get(store.upper(), {}), "ratio": RATIO,
            "onsale_only": True, "skipped_codes": sorted(skipped),
            "items": items, "rules_matched": sorted(matched)}


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    day = str(date.today())
    tot = {}
    for st in ("BHY", "BEL", "KLS"):
        db = build_store(st, day)
        (OUTDIR / ("%s.json" % st)).write_text(
            json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
        n_row = sum(len(v["sizes"]) for v in db["items"].values())
        miss = sum(1 for v in db["items"].values() for s in v["sizes"].values()
                   if s["src_lx"] == "missing" and s["src_ca3"] == "missing")
        tot[st] = (len(db["items"]), n_row, miss, len(db["rules_matched"]))
    for st, (a, b, c, d) in tot.items():
        print("%s：货号 %d / 尺码行 %d / 两路都缺 %d / 命中映射 %d" % (st, a, b, c, d))
    print("→", OUTDIR)


if __name__ == "__main__":
    main()
