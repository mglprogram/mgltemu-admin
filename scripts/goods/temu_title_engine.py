# -*- coding: utf-8 -*-
"""TEMU 标题生成引擎（离线，纯逻辑）。

输入：竞品标题语料 + 商品事实（件数/性别/品类/卖点/场景/季节）
输出：按平台官方「名称结构推荐」排布的候选标题 + 每个词块的语料依据。

官方结构（后台「商品名称」下方原文）：
    [节日活动]+[几件装]+[人群]+[面料/颜色]+[品类名词]+[细节/功能/图案]+[风格]+[季节]+[版型]+[适用场景]

三条硬规则（用户 2026-11-26 定）：
    1. 颜色不逐色罗列 —— 多色款只写 Multi Color 或不写
    2. 件数须与实物一致 —— 写 N Pcs 就必须发 N 件
    3. 竞品在用 ≠ 你能用 —— UPF50/uv/sun protection/rash guard/windproof/gift 一律避开
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

#: 官方 10 槽（槽名 → 内部键）
SLOTS: tuple[tuple[str, str], ...] = (
    ("节日活动", "event"),
    ("几件装", "qty"),
    ("人群", "who"),
    ("面料/颜色", "fabric"),
    ("品类名词", "noun"),
    ("细节/功能/图案", "feature"),
    ("风格", "style"),
    ("季节", "season"),
    ("版型", "fit"),
    ("适用场景", "scene"),
)

#: 违禁/高风险词（竞品在用不代表可用）
BANNED = (
    "upf", "uv", "sun protection", "sun-protection", "spf",
    "rash guard", "rashguard", "rash prevention", "rash resistant",
    "windproof", "waterproof", "antibacterial", "anti-bacterial",
    "medical", "therapy", "cure", "pain relief",
    "best ", "#1", "no.1", "top rated",
    "free shipping", "sale", "discount", "cheap",
    "gift", "labor day", "christmas", "black friday", "prime",
    "temu", "amazon", "shein", "walmart",
)


def norm(s: Any) -> str:
    return str(s or "").strip().lower()


def find_banned(title: str) -> list[str]:
    """返回标题里命中的违禁词。"""
    t = norm(title)
    return [w for w in BANNED if w in t]


# ---------------------------------------------------------------- 语料分析

def analyze_corpus(titles: list[str]) -> dict[str, Any]:
    """统计语料里的高频词块，用于给候选标题提供依据。"""
    if not titles:
        return {"count": 0, "tokens": Counter(), "phrases": Counter()}

    words: Counter = Counter()
    phrases: Counter = Counter()
    for t in titles:
        low = norm(t)
        for w in re.findall(r"[a-z0-9']+", low):
            if len(w) >= 3 and w not in ("the", "and", "for", "with", "men", "women"):
                words[w] += 1
        # 二元词组
        toks = re.findall(r"[a-z0-9']+", low)
        for a, b in zip(toks, toks[1:]):
            if len(a) >= 3 and len(b) >= 3:
                phrases["%s %s" % (a, b)] += 1
    return {"count": len(titles), "tokens": words, "phrases": phrases}


def score_term(term: str, corpus: dict[str, Any]) -> int:
    """某个词块在语料里出现的次数（整词/短语匹配）。"""
    if not corpus.get("count"):
        return 0
    low = norm(term)
    toks = corpus["tokens"]
    if " " in low:
        return corpus["phrases"].get(low, 0) or toks.get(low, 0)
    return toks.get(low, 0)


def pick_best(options: list[str], corpus: dict[str, Any]) -> tuple[str, int]:
    """从候选词里挑语料出现次数最高的。"""
    scored = [(score_term(o, corpus), o) for o in options]
    scored.sort(reverse=True)
    return scored[0][1], scored[0][0]


# ---------------------------------------------------------------- 生成标题

def build_title(
    facts: dict[str, Any],
    corpus: dict[str, Any] | None = None,
    variant: str = "full",
) -> dict[str, Any]:
    """按官方 10 槽生成标题。

    facts 需要的键（都用简单英文/数字）：
        qty_pcs   件数（int）；若为 None 则不出件数槽
        who       人群，如 "Mens" / "Womens"
        fabric    面料观感，如 "Lightweight"
        noun      品类名词，如 "Hoodies"
        feature   功能，如 ["Breathable", "Quick Dry"]
        style     风格，如 "Athletic"
        season    季节，如 "All Season"
        fit       版型，如 "Regular Fit"
        scene     场景，如 ["Fishing", "Hiking", "Outdoor"]
        color     （可选）颜色；多色请留空
        event     （可选）节日活动；默认不填

    variant:
        full   —— 10 槽全填
        lean   —— 只保留搜索权重最高的槽（去 fit/season/style）
        safe   —— full 去掉件数（件数未确认时用）
    """
    corpus = corpus or {}
    parts: list[str] = []
    used: list[dict[str, Any]] = []

    def add(slot: str, text: str, basis: int | None = None):
        if not text:
            return
        parts.append(text)
        used.append({"slot": slot, "text": text,
                     "corpus_hits": basis if basis is not None else score_term(text, corpus)})

    if variant != "lean":
        if facts.get("event"):
            add("节日活动", str(facts["event"]))

    if variant != "safe" and facts.get("qty_pcs"):
        add("几件装", "%d Pcs" % int(facts["qty_pcs"]))

    add("人群", str(facts.get("who") or ""))
    add("面料/颜色", str(facts.get("fabric") or ""))
    add("品类名词", str(facts.get("noun") or ""))

    feats = facts.get("feature") or []
    if isinstance(feats, str):
        feats = [feats]
    for f in feats:
        add("细节/功能/图案", str(f))

    if variant != "lean":
        add("风格", str(facts.get("style") or ""))
        add("季节", str(facts.get("season") or ""))
        add("版型", str(facts.get("fit") or ""))

    scenes = facts.get("scene") or []
    if isinstance(scenes, str):
        scenes = [scenes]
    for s in scenes:
        add("适用场景", str(s))

    title = " ".join(p for p in parts if p).strip()
    title = re.sub(r"\s+", " ", title)

    hits = find_banned(title)
    return {
        "variant": variant,
        "title": title,
        "length": len(title),
        "slots": used,
        "banned": hits,
        "ok": not hits and 0 < len(title) <= 250,
    }


def suggest_facts_from_corpus(
    corpus: dict[str, Any],
    seed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从语料里挑各槽的最优词，给 seed 打底（seed 里已有的优先）。"""
    seed = dict(seed or {})
    cand = {
        "who": ["Mens", "Womens", "Unisex"],
        "fabric": ["Lightweight", "Ultra Thin", "Stretch", "Soft"],
        "noun": ["Hoodies", "Hooded Sweatshirts", "Hooded Shirts", "Compression Tops"],
        "style": ["Athletic", "Sports", "Casual", "Active"],
        "season": ["All Season", "Spring Summer Autumn Winter", "Summer"],
        "fit": ["Regular Fit", "Slim Fit", "Loose Fit"],
    }
    for k, opts in cand.items():
        if seed.get(k):
            continue
        best, hits = pick_best(opts, corpus)
        if hits > 0:
            seed[k] = best
    return seed


def render_report(results: list[dict[str, Any]], corpus: dict[str, Any],
                  facts: dict[str, Any]) -> str:
    """生成便于人看的选择报告（markdown 片段）。"""
    lines = ["## 标题候选（语料 %d 条）" % corpus.get("count", 0), ""]
    if facts:
        lines.append("事实：" + "，".join(
            "%s=%s" % (k, v) for k, v in facts.items() if v not in (None, "", [])))
        lines.append("")
    for r in results:
        flag = "✅" if r["ok"] else "⚠️"
        lines.append("%s **%s** (%d 字符)" % (flag, r["variant"], r["length"]))
        lines.append("```")
        lines.append(r["title"])
        lines.append("```")
        if r.get("banned"):
            lines.append("- ⚠️ 命中违禁词：%s" % ", ".join(r["banned"]))
        weak = [s for s in r["slots"] if s["corpus_hits"] == 0]
        if weak:
            lines.append("- 语料无依据的槽：%s" % ", ".join(
                "%s=%s" % (s["slot"], s["text"]) for s in weak))
        lines.append("")
    return "\n".join(lines)
