# -*- coding: utf-8 -*-
"""TEMU 上链接（草稿发布）pipeline：env → schema → media → plan → apply → verify

用法（默认 dry-run，只校验不写后台；绝不自动提交审核，只存草稿等人工检查）：
    python scripts/goods/run_temu_publish.py --store BHY --stage env
    python scripts/goods/run_temu_publish.py --store BHY --stage schema --cat 30467
    python scripts/goods/run_temu_publish.py --store BHY --find 帽衫
    python scripts/goods/run_temu_publish.py --store BHY --list-drafts
    python scripts/goods/run_temu_publish.py --store BHY --read-draft 57682030962
    python scripts/goods/run_temu_publish.py --store BHY --spec <spec.json|目录> --check-sku
    python scripts/goods/run_temu_publish.py --store BHY --spec <spec.json|目录> --apply
    python scripts/goods/run_temu_publish.py --store BHY --spec <spec.json> --draft-id 57682030962 --apply

退出码：0 正常 | 2 校验/输入错误 | 3 环境/接口失败
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GOODS_DIR = Path(__file__).resolve().parent
if str(GOODS_DIR) not in sys.path:
    sys.path.insert(0, str(GOODS_DIR))

try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not sys.stderr.isatty():
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import temu_publish_api as api  # noqa: E402
import temu_publish_build as pb  # noqa: E402
import temu_title_engine as te  # noqa: E402

DATA_DIR = ROOT / "data" / "temu-publish"
OUT_DIR = ROOT / "out" / "temu-publish"
CAT_DIR = ROOT / "data" / "temu-category"
STAGES = ("env", "schema", "title", "size", "media", "plan", "apply", "verify")


def safe(s: Any) -> str:
    txt = str(s)
    enc = sys.stdout.encoding or "utf-8"
    try:
        txt.encode(enc)
        return txt
    except UnicodeEncodeError:
        return txt.encode(enc, "replace").decode(enc, "replace")


def p_out(s: Any = "") -> None:
    print(safe(s), flush=True)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def schema_path(store: str) -> Path:
    return DATA_DIR / ("schema_%s.json" % store.upper())


def img_cache_path(store: str) -> Path:
    return DATA_DIR / ("img_cache_%s.json" % store.upper())


def load_specs(spec_arg: str) -> list[tuple[str, dict]]:
    p = Path(spec_arg)
    if not p.is_absolute():
        p = ROOT / p
    if p.is_dir():
        files = sorted(p.glob("*.json"))
        if not files:
            raise FileNotFoundError("目录下没有 *.json: %s" % p)
        out = []
        for f in files:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, list):
                for idx, it in enumerate(d):
                    out.append(("%s#%d" % (f.name, idx + 1), it))
            else:
                out.append((f.name, d))
        return out
    if not p.is_file():
        raise FileNotFoundError("找不到 spec 文件: %s" % p)
    d = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(d, list):
        return [("%s#%d" % (p.name, idx + 1), it) for idx, it in enumerate(d)]
    return [(p.name, d)]


# ---------------- 辅助命令 ----------------

def cmd_find_cat(sess: api.TemuSession | None, keyword: str) -> int:
    kw = keyword.strip().lower()
    hits: list[str] = []

    # 1) 优先查本地类目树缓存（如有）
    files = sorted(glob.glob(str(CAT_DIR / "category_tree_*.json")))
    if files:
        tree = load_json(Path(files[-1]), {}) or {}
        nodes = tree.get("nodes") or {}
        for nid, node in nodes.items():
            name = "%s|%s" % (node.get("name") or "", node.get("en") or "")
            if kw not in name.lower():
                continue
            chain = []
            cur = node
            guard = 0
            while cur and guard < 12:
                guard += 1
                chain.append("%s(%s)" % (cur.get("name"), cur.get("catId")))
                pid = str(cur.get("parent") or "")
                cur = nodes.get(pid) if pid and pid != "0" else None
            chain.reverse()
            tag = " ★叶子" if node.get("leaf") else " (非叶子)"
            hits.append("  " + " > ".join(chain) + tag)
        p_out("本地树（%s）命中 %d 条：" % (Path(files[-1]).name, len(hits)))
        for line in hits[:40]:
            p_out(line)
        if hits:
            return 0

    # 2) 无本地命中或未抓树 → 线上查 queryCatHints + 一二级
    if sess is None:
        p_out("未找到本地命中，且未连接店铺窗口")
        return 2
    for h in sess.category_hints():
        parts = [h.get("cat%d" % i) for i in range(1, 11)]
        parts = [x for x in parts if x]
        line = " > ".join("%s(%s)" % (x.get("catName"), x.get("catId")) for x in parts)
        if kw in line.lower():
            p_out("  [推荐] " + line + " ★叶子")
            hits.append(line)
    roots = sess.category_children(0)
    for r in roots:
        if kw in ("%s|%s" % (r.get("catName"), r.get("catEnName"))).lower():
            p_out("  [一级] %s(%s)" % (r.get("catName"), r.get("catId")))
            hits.append(str(r.get("catId")))
    if not hits:
        p_out("未命中 %r；可先跑 python scripts/goods/api_category_tree.py --port %d --find %s"
              % (keyword, sess.port, keyword))
    return 0


def cmd_list_drafts(sess: api.TemuSession) -> int:
    res = sess.draft_list(page=1, page_size=50)
    items = res.get("pageItems") or []
    p_out("草稿总数: %s（当前页 %d 条）" % (res.get("total"), len(items)))
    for it in items:
        leaf = (it.get("leafCat") or {}).get("catName") or ""
        p_out("  id=%-12s  cat=%-14s  name=%s" % (
            it.get("productDraftId"),
            leaf[:14],
            str(it.get("productName") or "")[:50],
        ))
    return 0


def cmd_read_draft(sess: api.TemuSession, draft_id: int, out_dir: Path,
                   show_size: bool = False) -> int:
    res = sess.draft_query(draft_id)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = out_dir / ("draft_%s_%s.json" % (draft_id, ts))
    save_json(out_file, {"result": res})
    skcs = res.get("productSkcList") or []
    skus = [u for s in skcs for u in (s.get("productSkuList") or [])]
    p_out("草稿 %s: name=%r | 属性=%d | SKC=%d | SKU=%d | 轮播图=%d" % (
        draft_id,
        res.get("productName"),
        len(res.get("productPropertyList") or []),
        len(skcs),
        len(skus),
        len(res.get("carouselImageUrls") or []),
    ))
    p_out("落盘: %s" % out_file)

    if show_size:
        ids = res.get("sizeTemplateIds") or []
        if not ids:
            p_out("  !! 草稿未关联尺码表模板")
        for sid in ids:
            vo = sess.sizespec_detail(int(sid))
            summ = pb.summarize_size_spec(vo)
            p_out("  尺码表: %s (businessId=%s, %s)" % (
                summ["name"], summ["businessId"], summ["className"]))
            p_out("    列: %s" % " | ".join(summ["columns"]))
            for row in summ["rows"]:
                p_out("    %s" % " | ".join("%s=%s" % (k, v) for k, v in row.items()))
            chk = pb.check_size_spec_matches(
                [str(s.get("extCode") or "").rsplit("-", 1)[-1] for s in skus], vo)
            p_out("    尺码校验: ok=%s template=%s spec=%s" % (
                chk["ok"], chk["template_sizes"], chk["spec_sizes"]))
            if chk["missing_in_template"]:
                p_out("    ✗ 模板里没有这些尺码: %s" % chk["missing_in_template"])
    return 0


# ---------------- Pipeline 阶段 ----------------

def stage_env(store: str, port: int) -> tuple[api.TemuSession, dict]:
    p_out("== [1/6 env] %s ==" % store)
    p = api.resolve_port(store, port)
    sess = api.TemuSession(p)
    mallid = sess.get_mallid()
    sites = sess.site_list()
    drafts = sess.draft_list(page=1, page_size=1)
    p_out("  端口=%d | mallid=%s | 站点数=%d | 草稿数=%s" % (
        p, mallid, len(sites), drafts.get("total")
    ))
    return sess, {"port": p, "mallid": mallid, "sites": sites, "page": sess.page_url}


def stage_schema(store: str, sess: api.TemuSession, cat_ids: list[int], force: bool = False) -> dict:
    p_out("== [2/6 schema] %s ==" % store)
    sp = schema_path(store)
    sch = {} if force else (load_json(sp, {}) or {})
    sch["store"] = store.upper()
    sch["mallid"] = sess.get_mallid()
    sch["updatedAt"] = dt.datetime.now().isoformat(timespec="seconds")

    if force or not sch.get("sites"):
        sch["sites"] = sess.site_list()
    site_ids = [100]
    if force or not sch.get("warehouses"):
        sch["warehouses"] = sess.warehouse_list(site_ids)
    if force or not sch.get("cat_roots"):
        sch["cat_roots"] = sess.category_children(0)

    sch.setdefault("cat_paths", {})
    sch.setdefault("templates", {})
    sch.setdefault("image_templates", {})
    sch.setdefault("size_templates", {})

    for cid in sorted(set(int(x) for x in cat_ids if x)):
        k = str(cid)
        if force or k not in sch["cat_paths"]:
            sch["cat_paths"][k] = sess.category_path(cid)
        if force or k not in sch["templates"]:
            sch["templates"][k] = sess.category_template(cid)
        if force or k not in sch["image_templates"]:
            sch["image_templates"][k] = sess.image_template(cid)
        if force or k not in sch["size_templates"]:
            sch["size_templates"][k] = sess.sizespec_list(cat_id=cid, offset=0, limit=50)
        tpl = sch["templates"][k]
        p_out("  类目 %s: 属性=%d | 尺码模板=%d" % (
            cid,
            len(tpl.get("properties") or []),
            len(sch["size_templates"][k] or []),
        ))

    save_json(sp, sch)
    p_out("  缓存: %s (仓=%d, 根类目=%d)" % (
        sp, len(sch.get("warehouses") or []), len(sch.get("cat_roots") or [])
    ))
    return sch


class MediaResolver:
    """本地图片 → 线上 fancy/ URL（按 path|mtime|size 缓存；dry-run 时只读缓存）。"""

    def __init__(self, store: str, sess: api.TemuSession | None, allow_upload: bool):
        self.store = store.upper()
        self.sess = sess
        self.allow_upload = allow_upload
        self.path = img_cache_path(self.store)
        self.cache: dict[str, str] = load_json(self.path, {}) or {}
        self.sign = ""

    def __call__(self, ref: str) -> str:
        s = str(ref or "").strip()
        if not s or s.startswith("http://") or s.startswith("https://"):
            return s
        p = Path(s)
        if not p.is_absolute():
            p = ROOT / p
        if not p.is_file():
            raise FileNotFoundError("图片不存在: %s" % p)
        key = "%s|%d|%d" % (str(p.resolve()), int(p.stat().st_mtime), p.stat().st_size)
        if key in self.cache:
            return self.cache[key]
        if not self.allow_upload:
            return str(p)
        if self.sess is None:
            raise RuntimeError("需要上传图片但未连接店铺��口")
        fancy, _ = self.sess.upload_local_image(p)
        self.cache[key] = fancy
        save_json(self.path, self.cache)
        p_out("  [media] 上传 %s → %s" % (p.name, fancy[-48:]))
        return fancy


def _infer_title_facts(sp: dict, cat_name: str = "",
                       corpus: dict | None = None) -> dict:
    """从 spec 推断标题事实。

    优先级：spec.title_facts > 货号/标题解析 > 语料高频词 > 兜底默认值
    """
    facts = dict(sp.get("title_facts") or {})
    corpus = corpus or {}

    # 件数：优先显式 qty_pcs；否则从货号里的 "9SetA"/"6Pack" 解析
    if not facts.get("qty_pcs"):
        src = " ".join(str(sp.get(k) or "") for k in ("ext_code", "title_en", "title"))
        m = re.search(r"(\d+)\s*(?:set|pcs|pack|件)", src, re.I)
        if m:
            facts["qty_pcs"] = int(m.group(1))

    if not facts.get("who"):
        txt = ("%s %s" % (str(sp.get("title_en") or ""), cat_name)).lower()
        if "women" in txt or "女士" in txt or "女装" in txt:
            facts["who"] = "Womens"
        elif "men" in txt or "男士" in txt or "男装" in txt:
            facts["who"] = "Mens"

    if not facts.get("noun"):
        txt = ("%s %s" % (str(sp.get("title_en") or ""), cat_name)).lower()
        for kw, noun in (("hoodie", "Hoodies"), ("帽衫", "Hoodies"),
                         ("卫衣", "Hoodies"), ("shirt", "Shirts"),
                         ("pant", "Pants"), ("裤", "Pants"),
                         ("top", "Active Tops"), ("上衣", "Active Tops")):
            if kw in txt:
                facts["noun"] = noun
                break

    # 语料存在时，用高频词补齐剩余槽位
    if corpus.get("count"):
        if not facts.get("who"):
            facts["who"], _ = te.pick_best(["Mens", "Womens", "Unisex"], corpus)
        if not facts.get("fabric"):
            facts["fabric"], _ = te.pick_best(
                ["Lightweight", "Ultra Thin", "Stretch", "Soft"], corpus)
        if not facts.get("noun"):
            facts["noun"], _ = te.pick_best(
                ["Hoodies", "Hooded Sweatshirts", "Hooded Shirts",
                 "Compression Tops", "Active Tops"], corpus)
        if not facts.get("feature"):
            feats = []
            for f in ("Breathable", "Quick Dry", "Moisture Wicking"):
                if te.score_term(f, corpus) > 0:
                    feats.append(f)
            facts["feature"] = feats or ["Breathable"]
        if not facts.get("style"):
            facts["style"], _ = te.pick_best(
                ["Athletic", "Sports", "Casual", "Active"], corpus)
        if not facts.get("scene"):
            scenes = [s for s in ("Fishing", "Hiking", "Outdoor", "Running", "Gym")
                      if te.score_term(s, corpus) > 0]
            facts["scene"] = scenes[:3] or ["Outdoor"]

    # 兜底默认值（语料里也没有时）
    facts.setdefault("fabric", "Lightweight")
    facts.setdefault("feature", ["Breathable", "Quick Dry"])
    facts.setdefault("scene", ["Outdoor"])
    return facts


def stage_title(
    store: str,
    specs: list[tuple[str, dict]],
    port: int = 0,
    queries: list[str] | None = None,
    scroll: int = 6,
    offline: bool = False,
    sch: dict | None = None,
) -> list[dict]:
    """标题生成阶段：抓竞品 Top sales 标题 → 按官方 10 槽生成候选 → 落盘报告。

    spec 里可选 `title_facts` 提供事实（qty_pcs/who/fabric/noun/feature/style/
    season/fit/scene）。没给就用 spec 的 title_en 推断，或用类目给默认值。
    产物：out/temu-publish/title_<STORE>_<ts>.json + .md
    """
    p_out("== [3/7 title] %s ==" % store)
    sch = sch or {}

    # 1) 抓竞品语料
    corpus_titles: list[str] = []
    corpus_files: list[str] = []
    if not offline:
        qs = list(queries or [])
        if not qs:
            # 从 spec 里推关键词：用 lemma + 性别
            for _, sp in specs:
                f = sp.get("title_facts") or {}
                noun = f.get("noun") or ""
                who = (f.get("who") or "").lower()
                if noun:
                    qs.append(("%s %s" % (who, noun)).strip())
            if not qs:
                qs = ["hoodies"]
        for q in dict.fromkeys(qs):
            got = False
            for attempt in range(3):
                try:
                    import scrape_temu_www as sc  # noqa: PLC0415

                    res = sc.scrape(port, q, scroll=scroll, log=p_out)
                    corpus_titles += [i["title"] for i in res.get("items") or []]
                    corpus_files.append(q)
                    p_out("  关键词 %r → %d 条标题" % (q, len(res.get("items") or [])))
                    got = True
                    break
                except Exception as e:  # noqa: BLE001
                    msg = str(e)[:120]
                    if attempt < 2:
                        p_out("  ! 抓取 %r 第 %d 次失败（%s），2s 后重试"
                              % (q, attempt + 1, msg))
                        time.sleep(2)
                    else:
                        p_out("  ✗ 抓取 %r 失败：%s" % (q, msg))
            if got:
                time.sleep(1.5)   # 连续抓同一个窗口时稍作间隔，避免 ws 500
    else:
        p_out("  （offline：跳过抓取，只用 spec 自带事实）")

    corpus = te.analyze_corpus(corpus_titles)
    p_out("  语料：%d 条（去重后）" % corpus.get("count", 0))

    # 2) 每个 spec 生成候选
    out: list[dict] = []
    for label, sp in specs:
        cat_name = ""
        cid = str(sp.get("cat_id") or "")
        if cid:
            cat_name = str(((sch.get("cat_paths") or {}).get(cid) or {})
                           .get("cat5NodeVO", {}).get("catName") or "")
        facts = _infer_title_facts(sp, cat_name, corpus)
        variants = []
        for v in ("full", "lean", "safe"):
            r = te.build_title(facts, corpus, variant=v)
            variants.append(r)
        rec = {"label": label, "facts": facts, "variants": variants,
               "queries": corpus_files}
        out.append(rec)
        p_out("  [%s]" % label)
        for v in variants:
            flag = "✓" if v["ok"] else "✗"
            p_out("    %s %-4s (%3d) %s" % (flag, v["variant"], v["length"], v["title"][:96]))
            if v["banned"]:
                p_out("       违禁词: %s" % v["banned"])

    # 3) 落盘（json + md）
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    jf = OUT_DIR / ("title_%s_%s.json" % (store.upper(), ts))
    save_json(jf, {"corpus_count": corpus.get("count", 0),
                   "corpus_sample": corpus_titles[:80],
                   "results": out})
    md_lines = ["# 标题候选 %s %s" % (store.upper(), ts), "",
                "语料 %d 条（来源：%s）" % (corpus.get("count", 0),
                                         ", ".join(corpus_files) or "offline"),
                ""]
    for rec in out:
        md_lines.append("## %s" % rec["label"])
        md_lines.append("事实：`%s`" % json.dumps(rec["facts"], ensure_ascii=False))
        md_lines.append("")
        md_lines.append(te.render_report(rec["variants"], corpus, rec["facts"]))
    mf = OUT_DIR / ("title_%s_%s.md" % (store.upper(), ts))
    mf.write_text("\n".join(md_lines), encoding="utf-8")
    p_out("  落盘: %s" % jf)
    p_out("        %s" % mf)
    return out


def stage_size(
    store: str,
    specs: list[tuple[str, dict]],
    sch: dict,
    sess: api.TemuSession,
    apply: bool = False,
) -> bool:
    """尺码阶段：按 spec.size_values 选/建尺码表模板，把 businessId 写回 spec。

    口径（用户 2026-11-26 定）：
      · 上衣只填 衣长 / 胸围 / 袖长；裤子只填 裤长 / 臀围 / 腰围
      · 先在**该店该类目**下查已有模板，量体项与每码取值都一致才复用；
        否则调 createSizeSpec 建新模板（apply 时才真建）
    """
    p_out("== [4/8 size] %s（上衣=衣长/胸围/袖长，裤子=裤长/臀围/腰围）==" % store)
    ok = True
    for label, sp in specs:
        cat_id = int(sp.get("cat_id") or 0)
        vals = sp.get("size_values")
        if not cat_id or not vals:
            continue  # 沿用 spec 里写死的 size_template_id
        profile = str(sp.get("size_profile") or "").strip().lower()
        if profile not in pb.SIZE_PROFILES:
            cat_path = sch.get("cat_paths", {}).get(str(cat_id)) or {}
            leaf = (cat_path.get("cat6NodeVO") or cat_path.get("cat1NodeVO") or {}).get("catName") or ""
            profile = pb.guess_profile(leaf, sp)

        want: dict[str, dict[str, str]] = {}
        errs: list[str] = []
        for size, raw in vals.items():
            norm, e = pb.normalize_size_values(profile, raw)
            errs.extend("%s: %s" % (size, x) for x in e)
            want[str(size).strip().upper()] = norm
        if errs:
            ok = False
            for e in errs:
                p_out("  [%s] ✗ %s" % (label, e))
            continue

        pick = pb.pick_size_template(
            cat_id, sch, profile, want, sess=sess,
            name_hint=str(sp.get("size_template_name") or "").strip(),
        )
        p_out("  [%s] 类目 %s / %s：%s" % (label, cat_id, pb.SIZE_PROFILES[profile]["label"], pick["reason"]))
        for c in pick["checked"]:
            if c.get("diffs"):
                p_out("      · %s 不一致: %s%s" % (
                    c.get("name"), "; ".join(c.get("diffs") or []),
                    "（共 %d 处）" % c["diffsCount"] if c.get("diffsCount", 0) > len(c.get("diffs") or []) else "",
                ))

        if pick["reused"]:
            sp["size_template_id"] = int(pick["businessId"])
            p_out("      ✓ 复用 businessId=%s" % pick["businessId"])
        else:
            name = str(sp.get("size_template_name") or "").strip() or "%s-默认尺码表" % (
                pb.SIZE_PROFILES[profile]["label"])
            # catId=None + reusable=true ⇒ 进店铺公共模板库（列表查得到、后台下拉可见、下次可复用）
            payload = pb.build_sizespec_payload(pick["vo"], new_name=name, cat_id=None, reusable=True)
            ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            pf = OUT_DIR / ("sizespec_%s_%s_%s.json" % (store.upper(), cat_id, ts))
            save_json(pf, payload)
            p_out("      模板请求体: %s" % pf)
            if apply:
                new_id = sess.sizespec_create(payload)
                sp["size_template_id"] = int(new_id)
                p_out("      ✓ 已新建 businessId=%s（name=%s）" % (new_id, name))
            else:
                p_out("      （未传 --apply，未真正创建模板）")
        for size, v in want.items():
            p_out("      %-4s %s" % (size, " | ".join("%s=%s" % (k, v[k]) for k in v)))
    return ok


def stage_media(store: str, sess: api.TemuSession, specs: list[tuple[str, dict]]) -> MediaResolver:
    p_out("== [3/6 media] %s ==" % store)
    mr = MediaResolver(store, sess, allow_upload=True)
    refs: list[str] = []
    for _, sp in specs:
        for u in sp.get("carousel_images") or []:
            refs.append(str(u))
        if sp.get("material_image"):
            refs.append(str(sp["material_image"]))
        for u in sp.get("images") or []:
            refs.append(str(u))
        if sp.get("color_image"):
            refs.append(str(sp["color_image"]))
        for skc in sp.get("skcs") or []:
            for u in skc.get("images") or []:
                refs.append(str(u))
            if skc.get("color_image"):
                refs.append(str(skc["color_image"]))
            for _, sz in (skc.get("sizes") or {}).items():
                if sz.get("thumb_url"):
                    refs.append(str(sz["thumb_url"]))
        for _, sz in (sp.get("sizes") or {}).items():
            if sz.get("thumb_url"):
                refs.append(str(sz["thumb_url"]))
    local_refs = [r for r in dict.fromkeys(refs) if r and not r.startswith(("http://", "https://"))]
    p_out("  需解析本地图片 %d 张（已缓存直接命中）" % len(local_refs))
    for r in local_refs:
        mr(r)
    return mr


def stage_plan(
    store: str,
    specs: list[tuple[str, dict]],
    sch: dict,
    pic_resolver: MediaResolver,
    sess: api.TemuSession | None = None,
    base_draft_id: int = 0,
    check_sku: bool = False,
) -> tuple[list[dict], bool]:
    p_out("== [4/6 plan] %s (%d 条 spec) ==" % (store, len(specs)))
    base_cache: dict[int, dict] = {}

    def _get_base(did: int) -> dict:
        if not did:
            return {}
        if did not in base_cache:
            if sess is None:
                raise RuntimeError("读取 --base-draft %s 需要连着店铺窗口" % did)
            dq = sess.draft_query(did)
            base_cache[did] = pb.draft_query_to_save_defaults(dq)
        return base_cache[did]

    plans: list[dict] = []
    has_err = False
    all_ext_codes: list[str] = []

    for label, sp in specs:
        bdid = int(base_draft_id or sp.get("base_draft_id") or 0)
        bdef = _get_base(bdid)
        payload, errs, warns = pb.build_draft_save_payload(
            sp, sch, pic_resolver=pic_resolver, base_defaults=bdef
        )
        for skc in payload.get("productSkcReqs") or []:
            if skc.get("extCode"):
                all_ext_codes.append(str(skc["extCode"]))
            for sku in skc.get("productSkuReqs") or []:
                if sku.get("extCode"):
                    all_ext_codes.append(str(sku["extCode"]))
        if errs:
            has_err = True
        plans.append({"label": label, "spec": sp, "payload": payload, "errors": errs, "warns": warns})
        p_out("  [%s] errors=%d warns=%d skc=%d" % (
            label, len(errs), len(warns), len(payload.get("productSkcReqs") or [])
        ))
        for e in errs:
            p_out("    ✗ " + e)
        for w in warns[:6]:
            p_out("    ! " + w)

    if check_sku and sess is not None and all_ext_codes:
        chk = sess.extcode_batch_check(sorted(set(all_ext_codes)))
        rej = chk.get("rejectExtCodeList") or []
        p_out("  [check-sku] 检查 %d 个货号 → 冲突 %d 个 %s" % (len(set(all_ext_codes)), len(rej), rej))
        if rej:
            has_err = True
            plans.append({"label": "_check_sku", "rejectExtCodeList": rej, "errors": ["货号冲突: %s" % rej]})

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    plan_file = OUT_DIR / ("plan_%s_%s.json" % (store.upper(), ts))
    save_json(plan_file, plans)
    p_out("  计划落盘: %s" % plan_file)
    return plans, has_err


def stage_apply(
    store: str,
    sess: api.TemuSession,
    plans: list[dict],
    draft_id_override: int = 0,
) -> list[dict]:
    p_out("== [5/6 apply] %s (仅保存草稿，不提交审核) ==" % store)
    results: list[dict] = []
    ledger = DATA_DIR / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)

    for pl in plans:
        if pl.get("label") == "_check_sku":
            continue
        if pl.get("errors"):
            results.append({"label": pl["label"], "ok": False, "error": "本地校验未通过"})
            continue
        payload = dict(pl["payload"])
        did = int(draft_id_override or payload.get("productDraftId") or 0)
        reused = False
        if did:
            # 原地改：草稿已存在就复用，绝不新建（用户 2026-11-26 要求「修改内容以后在原草稿修改」）。
            # 实测 draft/save 只要带上 productDraftId，SkcId/SkuId/GoodsId 全传 0 也会按 extCode 覆盖原草稿。
            reused = True
        else:
            cat_id = int(pl["spec"].get("cat_id") or 0)
            bind_sites = (payload.get("productSemiManagedReq") or {}).get("bindSiteIds") or [100]
            wh_route = payload.get("productWarehouseRouteReq") or {"targetRouteList": []}
            did = sess.draft_add(cat_id, bind_sites, wh_route)
            p_out("  [%s] draft/add → 新草稿 productDraftId=%d" % (pl["label"], did))
        payload["productDraftId"] = did

        # 防御：若仍有未上传本地路径则拒绝���后台
        for u in payload.get("carouselImageUrls") or []:
            if not str(u).startswith(("http://", "https://")):
                raise RuntimeError("仍有未上传的本地图片路径，不能写入草稿: %s" % u)

        resp = sess.draft_save(payload)
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "store": store.upper(),
            "label": pl["label"],
            "productDraftId": did,
            "productName": payload.get("productName"),
            "ok": bool(resp.get("success")),
            "payload": payload,
        }
        results.append(rec)
        with open(ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps(
                {k: rec[k] for k in ("ts", "store", "label", "productDraftId", "productName", "ok")},
                ensure_ascii=False,
            ) + "\n")
        p_out("  [%s] draft/save → productDraftId=%d ok=%s%s" % (
            pl["label"], did, rec["ok"], "（原地更新）" if reused else ""))

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    apply_file = OUT_DIR / ("apply_%s_%s.json" % (store.upper(), ts))
    save_json(apply_file, [{k: v for k, v in r.items() if k != "payload"} for r in results])
    p_out("  执行记录: %s" % apply_file)
    return results


def stage_verify(store: str, sess: api.TemuSession, apply_results: list[dict]) -> bool:
    p_out("== [6/6 verify] %s (draft/query 回读校验) ==" % store)
    all_ok = True
    for r in apply_results:
        did = r.get("productDraftId")
        if not did or not r.get("ok"):
            all_ok = False
            continue
        got = sess.draft_query(int(did))
        chk = pb.verify_draft_readback(r["payload"], got)
        p_out("  draft=%s ok=%s title=%s cat=%s props=%s skc=%s sku=%s img=%s" % (
            did, chk["ok"], chk["title_match"], chk["cat_match"],
            chk["props_count"], chk["skc_count"], chk["sku_count"], chk["carousel_count"],
        ))
        if not chk["ok"]:
            all_ok = False
    return all_ok


# ---------------- CLI ----------------

def resolve_stages(args) -> list[str]:
    if args.stage:
        return [s.strip() for s in args.stage.split(",") if s.strip()]
    if args.from_stage or args.to_stage:
        i0 = STAGES.index(args.from_stage) if args.from_stage else 0
        i1 = STAGES.index(args.to_stage) if args.to_stage else (len(STAGES) - 1)
        return list(STAGES[i0:i1 + 1])
    if args.apply:
        return ["env", "schema", "size", "media", "plan", "apply", "verify"]
    if args.upload:
        return ["env", "schema", "size", "media", "plan"]
    return ["env", "schema", "plan"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TEMU 上链接到草稿箱 pipeline")
    ap.add_argument("--store", default="BHY", choices=tuple(api.STORES.keys()), help="店铺 (BHY/BEL/KLS)")
    ap.add_argument("--port", type=int, default=0, help="手动指定 CDP 端口（默认自动按店铺名探测）")
    ap.add_argument("--stage", default="", help="只跑指定阶段（逗号分隔：env,schema,media,plan,apply,verify）")
    ap.add_argument("--from", dest="from_stage", default="", choices=("",) + STAGES)
    ap.add_argument("--to", dest="to_stage", default="", choices=("",) + STAGES)
    ap.add_argument("--cat", type=int, action="append", default=[], help="额外缓存的类目 ID（可多次传）")
    ap.add_argument("--force-schema", action="store_true", help="强制刷新 schema 缓存")
    ap.add_argument("--find", default="", help="按关键字搜类目路径/ID")
    ap.add_argument("--list-drafts", action="store_true", help="列出当前草稿")
    ap.add_argument("--read-draft", type=int, default=0, help="读取指定草稿并落盘")
    ap.add_argument("--size-template", type=int, default=0,
                    help="按 businessId 打印尺码表数值（只读）")
    ap.add_argument("--size-set", action="append", default=[],
                    help="改尺码数值，形如 S:10002=102,10003=72（配合 --size-template）")
    ap.add_argument("--size-new-name", default="", help="新建尺码表的名称")
    ap.add_argument("--size-cat", type=int, default=0,
                    help="尺码表所属类目 catId（createSizeSpec 必填，通常填商品叶子类目）")
    ap.add_argument("--spec", default="", help="spec JSON 文件、JSON 数组或目录")
    ap.add_argument("--base-draft", type=int, default=0, help="从���有草稿继承运费/产地/模特等默认字段")
    ap.add_argument("--draft-id", type=int, default=0, help="更新指定草稿 ID（不传则新建草稿）")
    ap.add_argument("--check-sku", action="store_true", help="plan 阶段调用 extCode/batchCheck 查重")
    ap.add_argument("--show-size", action="store_true",
                    help="配合 --read-draft：同时打印尺码表数值并校验")

    # ---- 标题阶段 ----
    ap.add_argument("--title-query", action="append", default=[],
                    help="抓竞品用的英文关键词（可多次传；默认从 spec 的 title_facts 推断）")
    ap.add_argument("--title-scroll", type=int, default=6,
                    help="竞品页向下滚动次数（越多抓越多，默认 6）")
    ap.add_argument("--title-pick", default="full",
                    choices=("full", "lean", "safe"),
                    help="选哪个变体写回 spec.title_en（默认 full）")
    ap.add_argument("--title-offline", action="store_true",
                    help="不抓竞品，只用 spec 自带 title_facts 生成")
    ap.add_argument("--www-port", type=int, default=0,
                    help="www.temu.com 所在窗口的 CDP 端口（默认同 --port）")
    ap.add_argument("--upload", action="store_true", help="上传本地图片到素材库并写入缓存（不建草稿）")
    ap.add_argument("--apply", action="store_true", help="真建/保存草稿并回读校验（绝不提交审核）")
    args = ap.parse_args(argv)

    # 快捷只读工具命令
    if args.find:
        sess = None
        try:
            sess = api.TemuSession(api.resolve_port(args.store, args.port))
        except Exception:
            pass
        return cmd_find_cat(sess, args.find)

    if args.list_drafts:
        sess = api.TemuSession(api.resolve_port(args.store, args.port))
        return cmd_list_drafts(sess)

    if args.read_draft:
        sess = api.TemuSession(api.resolve_port(args.store, args.port))
        return cmd_read_draft(sess, args.read_draft, ROOT / "out" / "draft",
                              show_size=args.show_size)

    if args.size_template:
        sess = api.TemuSession(api.resolve_port(args.store, args.port))
        vo = sess.sizespec_detail(int(args.size_template))
        summ = pb.summarize_size_spec(vo)
        p_out("尺码表: %s (businessId=%s, %s)" % (
            summ["name"], summ["businessId"], summ["className"]))
        p_out("列: %s" % " | ".join(summ["columns"]))
        for row in summ["rows"]:
            p_out("  %s" % " | ".join("%s=%s" % (k, v) for k, v in row.items()))

        # --size-set 尺码:id=值,...  → 组装新模板（默认只预览，--apply 才新建）
        if args.size_set:
            for spec in args.size_set:
                # 形如 S:10002=102,10003=72
                size_part, _, kv = spec.partition(":")
                for pair in kv.split(","):
                    if "=" not in pair:
                        continue
                    eid, val = pair.split("=", 1)
                    ok = pb.set_size_value(vo, size_part, int(eid), val.strip())
                    p_out("  修改 %s 元素 %s → %s : %s" % (
                        size_part, eid, val, "OK" if ok else "✗ 未找到该尺码"))
            cat_for_size = args.size_cat or (args.cat[0] if args.cat else 0)
            payload = pb.build_sizespec_payload(
                vo,
                new_name=args.size_new_name or None,
                cat_id=cat_for_size,
            )
            ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            pf = OUT_DIR / ("sizespec_%s_%s.json" % (args.size_template, ts))
            save_json(pf, payload)
            p_out("  新模板请求体已落盘: %s（%d 字节）" % (pf, len(json.dumps(payload, ensure_ascii=False))))
            if args.apply:
                new_id = sess.sizespec_create(payload)
                p_out("  ✓ 已创建新尺码表 businessId=%s" % new_id)
                p_out("    下一步：把草稿的 sizeTemplateIds 指向 %s（用 --spec 改后 --draft-id 保存）" % new_id)
            else:
                p_out("  （未传 --apply，未真正创建）")
        return 0

    stages = resolve_stages(args)
    specs: list[tuple[str, dict]] = []
    if args.spec:
        specs = load_specs(args.spec)

    cat_ids = list(args.cat)
    for _, sp in specs:
        if sp.get("cat_id"):
            cat_ids.append(int(sp["cat_id"]))

    sess: api.TemuSession | None = None
    sch: dict = load_json(schema_path(args.store), {}) or {}
    pic_res = MediaResolver(args.store, None, allow_upload=False)
    plans: list[dict] = []
    apply_res: list[dict] = []

    if "env" in stages:
        try:
            sess, _ = stage_env(args.store, args.port)
        except Exception as e:
            p_out("  ✗ env 失败: %s" % e)
            return 3

    if "schema" in stages:
        if sess is None:
            sess = api.TemuSession(api.resolve_port(args.store, args.port))
        sch = stage_schema(args.store, sess, cat_ids, force=args.force_schema)

    if "title" in stages:
        if not specs:
            p_out("（未传 --spec，title 阶段跳过）")
        else:
            port_for_scrape = args.www_port or api.resolve_port(args.store, args.port)
            title_out = stage_title(
                args.store, specs, port=port_for_scrape,
                queries=args.title_query or None,
                scroll=args.title_scroll,
                offline=args.title_offline,
                sch=sch,
            )
            # 把选中变体回填到 spec，供后续 plan 使用
            pick = args.title_pick or "full"
            by_label = {r["label"]: r for r in title_out}
            for label, sp in specs:
                rec = by_label.get(label)
                if not rec:
                    continue
                hit = next((v for v in rec["variants"] if v["variant"] == pick), None)
                if hit and hit["ok"]:
                    sp["title_en"] = hit["title"]
                    sp["title"] = hit["title"]
                    p_out("  [%s] title/title_en ← %s：%s"
                          % (label, pick, hit["title"][:80]))
                elif hit:
                    p_out("  [%s] %s 变体不可用（违禁词/超长），保留原标题" % (label, pick))

    if "size" in stages and specs:
        if sess is None:
            sess = api.TemuSession(api.resolve_port(args.store, args.port))
        stage_size(args.store, specs, sch, sess, apply=args.apply)

    if "media" in stages and specs:
        if sess is None:
            sess = api.TemuSession(api.resolve_port(args.store, args.port))
        pic_res = stage_media(args.store, sess, specs)

    if "plan" in stages:
        if not specs:
            if stages == ["env", "schema", "plan"]:
                p_out("（未传 --spec，已完成 env + schema 缓存）")
                return 0
            p_out("!! plan 阶段需要 --spec")
            return 2
        plans, has_err = stage_plan(
            args.store,
            specs,
            sch,
            pic_res,
            sess=sess,
            base_draft_id=args.base_draft,
            check_sku=args.check_sku,
        )
        if has_err:
            p_out("!! 存在校验错误，终止（退出码 2）")
            return 2

    if "apply" in stages:
        if not args.apply:
            p_out("（未传 --apply，跳过写入草稿）")
            return 0
        if sess is None:
            sess = api.TemuSession(api.resolve_port(args.store, args.port))
        apply_res = stage_apply(args.store, sess, plans, draft_id_override=args.draft_id)
        if any(not r.get("ok") for r in apply_res):
            return 3

    if "verify" in stages:
        if sess is None:
            sess = api.TemuSession(api.resolve_port(args.store, args.port))
        if not apply_res and args.draft_id:
            return cmd_read_draft(sess, args.draft_id, ROOT / "out" / "draft")
        if apply_res and not stage_verify(args.store, sess, apply_res):
            return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
