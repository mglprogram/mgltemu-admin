# -*- coding: utf-8 -*-
"""pw_update_orders.py — 帕沃尔「更新订单」页：勾选店铺 → 点「更新勾选」（无头）。

## 为什么要有这一步（2026-11-08 用户令）

用户原话：「在 temu 发货的 skill 的 pipeline 的下单代发前一步加一个更新订单的步骤：
深圳市帕沃尔国际仓储服务有限公司 (https://www.powerwarehouse.net/hwc_shop.php?act=update_order_news)
勾选 BE（1640478289）、BHY（634418228526171）、BEL（634418227477419）→ 点击更新勾选
这也就是今天复制订单找不到订单失败的原因」

★ 2026-11-10 追加（同用户）：「加上 BD、BAN、BS 店铺的更新订单」
  ⇒ 勾选清单扩到 6 家：BE / BHY / BEL / BD(5965848967) / BAN(1205650698) / BS(8540141066)。

即：帕沃尔侧的店铺订单要**先在这里同步/更新**，`hwc_shop` 的「全部订单」里才查得到单号；
不同步就下单代发 → 查询 0 行 → 「复制订单找不到订单」。

## 用法

    python scripts/power/pw_update_orders.py --dry          # 只 dump 页面结构（不开写操作）
    python scripts/power/pw_update_orders.py --confirm      # 真勾选 + 点「更新勾选」

退出码：0=成功（或 dry 成功）/ 非 0=失败。
"""
import argparse
import json
import re
import sys
import time

LIB = r"D:\AI-workplace\TEMU\scripts\power\lib"
sys.path.insert(0, LIB)

URL = "https://www.powerwarehouse.net/hwc_shop.php?act=update_order_news"

#: 要勾选的店铺（帕沃尔里的「店铺ID」）
#: 2026-11-08 用户给定 BE / BHY / BEL；
#: ★ 2026-11-10 用户令：「加上 BD、BAN、BS 店铺的更新订单」。
#:   ID 是从更新订单页现场读回来的（店铺名称 / 店铺ID 两列），不是猜的：
#:     BS=8540141066 / BD=5965848967 / BAN=1205650698（三家都是 Shein 半托管）。
#:   ⚠ 页面共有 21 家店，其余（HOPLYNN / TK-* / TC / TH / KinglamanSport 等）用户没要求，不勾。
SHOPS = {
    "BE": "1640478289",
    "BHY": "634418228526171",
    "BEL": "634418227477419",
    "BD": "5965848967",
    "BAN": "1205650698",
    "BS": "8540141066",
}

#: 页面自检：把 checkbox / 按钮 / 可见文本都读回来（用来确认真实 DOM，不猜选择器）
JS_DUMP = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0; };
  const txt = e => (e.innerText || e.textContent || '').replace(/\s+/g,' ').trim();
  const cbs = [...document.querySelectorAll('input[type=checkbox]')].map(e => {
    const lab = e.closest('label,tr,li,div');
    return {name: e.name||'', value: e.value||'', id: e.id||'',
            checked: !!e.checked, visible: vis(e),
            near: lab ? txt(lab).slice(0, 80) : ''};
  });
  const btns = [...document.querySelectorAll('button,input[type=button],input[type=submit],a.btn,.btn')]
    .filter(vis).map(e => ({tag: e.tagName, type: e.type||'', value: e.value||'',
                            text: txt(e).slice(0, 40), id: e.id||'', cls: (e.className||'').toString().slice(0,60)}));
  return {url: location.href, title: document.title,
          checkboxes: cbs, buttons: btns,
          body: txt(document.body).slice(0, 1500)};
}"""

#: 勾选指定店铺 id（value 精确或包含）；同时把 label 也点一下（部分页面靠 label 触发）
JS_CHECK = r"""(ids) => {
  const hit = [], miss = [];
  for (const id of ids) {
    const el = [...document.querySelectorAll('input[type=checkbox]')]
      .find(e => String(e.value) === String(id) || String(e.value).indexOf(String(id)) >= 0
                 || String(e.name) === String(id));
    if (!el) { miss.push(id); continue; }
    if (!el.checked) {
      el.click();
      if (!el.checked) { el.checked = true; el.dispatchEvent(new Event('change', {bubbles:true})); }
    }
    hit.push({id: id, value: el.value, checked: !!el.checked});
  }
  return {hit: hit, miss: miss};
}"""

#: 点「更新勾选」——真机 DOM（2026-11-08 探测）：`<a class="gx-btn" onclick="gx_gouxuan()">更新勾选</a>`
#: 只按下标文本精确匹配会漏（工具栏里还有「更新订单」「更新订单(新)」）；这里三步兜底：
#:   ① 点文本恰为「更新勾选」的元素 → ② 调 `gx_gouxuan()` → ③ 找 onclick 含 gx_gouxuan 的元素点。
JS_CLICK_UPDATE = r"""() => {
  const txt = e => (e.innerText || e.value || '').replace(/\s+/g, '').trim();
  const all = [...document.querySelectorAll('a,button,input,span,div')];
  const btn = all.find(e => txt(e) === '更新勾选')
           || all.find(e => txt(e).indexOf('更新勾选') === 0);
  if (btn) { btn.scrollIntoView({block: 'center'}); btn.click();
             return {ok: true, how: 'click-text', tag: btn.tagName,
                     cls: (btn.className || '').toString().slice(0, 40),
                     onclick: (btn.getAttribute('onclick') || '').slice(0, 60)}; }
  try { if (typeof gx_gouxuan === 'function') { gx_gouxuan(); return {ok: true, how: 'fn'}; } } catch (e) {}
  const byAttr = all.find(e => (e.getAttribute('onclick') || '').indexOf('gx_gouxuan') >= 0);
  if (byAttr) { byAttr.click(); return {ok: true, how: 'click-onclick'}; }
  return {ok: false, why: 'no-button',
          seen: all.map(txt).filter(t => t).slice(0, 20)};
}"""

#: 点完回读：顶部「您当前一共需要更新 N 家店铺，已更新 M 家店铺」+ 本批三店的更新状态/时间
JS_RESULT = r"""(ids) => {
  const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const body = txt(document.body);
  const m = body.match(/一共需要更新\s*(\d+)\s*家店铺[，,]?\s*已更新\s*(\d+)\s*家店铺/);
  const rows = [];
  document.querySelectorAll('tr').forEach(tr => {
    const cb = tr.querySelector('input[name="checkboxes[]"]');
    if (!cb || ids.indexOf(String(cb.value)) < 0) return;
    const tds = [...tr.querySelectorAll('td')].map(txt);
    rows.push({id: String(cb.value), cells: tds.slice(0, 9)});
  });
  return {head: m ? {need: +m[1], done: +m[2]} : null, rows: rows,
          body_tail: body.slice(-400)};
}"""


def log(msg):
    print(msg, flush=True)


def open_page():
    import pw_hwc_flow as F  # noqa: PLC0415
    pg = F.page()
    F.arm(pg)
    return pg, F


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只读页面结构，不勾选不提交")
    ap.add_argument("--confirm", action="store_true", help="真勾选 + 点「更新勾选」")
    ap.add_argument("--shops", default="", help="覆盖店铺，格式 NAME=ID,NAME=ID（默认 BE/BHY/BEL）")
    ap.add_argument("--out", default="", help="dump json 落盘路径")
    ap.add_argument("--settle", type=float, default=6.0, help="点完更新后等多少秒")
    ap.add_argument("--wait", type=float, default=180.0, help="轮询等三店出终态的最长秒数")
    a = ap.parse_args()

    shops = dict(SHOPS)
    if a.shops:
        shops = {}
        for part in a.shops.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                shops[k.strip()] = v.strip()

    pg, F = open_page()
    log("[1/5] 打开更新订单页")
    pg.goto(URL)
    time.sleep(5)
    F.arm(pg)

    log("[2/5] 读页面结构")
    dump = pg.evaluate(JS_DUMP) or {}
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=1)
        log("       dump → %s" % a.out)
    log("       url=%s" % dump.get("url"))
    log("       checkbox=%d 个：%s" % (
        len(dump.get("checkboxes") or []),
        ", ".join("%s=%s" % (c.get("near", "")[:16] or "(无标签)", c.get("value"))
                  for c in (dump.get("checkboxes") or [])[:10])))
    log("       按钮：%s" % (", ".join(b.get("text") or b.get("value") or ""
                                    for b in (dump.get("buttons") or []))[:200]))

    if a.dry:
        log("[DRY] 未做任何写操作")
        return 0
    if not a.confirm:
        log("[FAIL] 需要 --confirm 才会真勾选提���（或加 --dry 只看结构）")
        return 2

    ids = list(shops.values())
    log("[3/5] 勾选店铺：%s" % ", ".join("%s(%s)" % (k, v) for k, v in shops.items()))
    r = pg.evaluate(JS_CHECK, ids) or {}
    for h in r.get("hit") or []:
        log("       ✓ %s → value=%s checked=%s" % (h.get("id"), h.get("value"), h.get("checked")))
    if r.get("miss"):
        log("[FAIL] 以下店铺 id 在页面上找不到 checkbox：%s" % r["miss"])
        return 3

    log("[4/5] 点「更新勾选」")
    c = pg.evaluate(JS_CLICK_UPDATE) or {}
    if not c.get("ok"):
        log("[FAIL] 找不到「更新勾选」按钮（页面按钮：%s）" % c.get("seen"))
        return 4
    log("       已点：%s（%s）" % (c.get("how") or c.get("text"), c.get("onclick") or ""))
    time.sleep(float(a.settle or 6))

    log("[5/5] 回读结果（轮询直到三店都出终态）")
    F.arm(pg)
    deadline = time.time() + float(a.wait or 180)
    last = {}
    while True:
        try:
            after = pg.evaluate(JS_RESULT, ids) or {}
        except Exception as e:  # noqa: BLE001
            log("       ⚠ 回读异常：%s" % str(e)[:100])
            after = {}
        rows = {r.get("id"): (r.get("cells") or []) for r in (after.get("rows") or [])}
        last = rows
        done = 0
        for sid in ids:
            cells = rows.get(sid) or []
            st = cells[6] if len(cells) > 6 else ""
            if any(k in str(st) for k in ("成功", "失败", "没有获取到")):
                done += 1
        if done == len(ids):
            break
        if time.time() > deadline:
            log("       ⚠ 等待超时（%ss），以下按当前状态输出" % a.wait)
            break
        time.sleep(10)

    for sid in ids:
        cells = last.get(sid) or []
        log("       id=%-20s 数量=%s 失败=%s 状态=%s 更新于=%s"
            % (sid, cells[4] if len(cells) > 4 else "?",
               cells[5] if len(cells) > 5 else "?",
               cells[6] if len(cells) > 6 else "?",
               cells[7] if len(cells) > 7 else "?"))

    # 终态闸门：三店都要有明确结果（成功 / 成功但无新单 / 失败），否则如实报错
    bad = []
    for sid in ids:
        cells = last.get(sid) or []
        st = str(cells[6] if len(cells) > 6 else "")
        if not any(k in st for k in ("成功", "没有获取到")):
            bad.append("%s→%r" % (sid, st))
    if bad:
        log("[FAIL] 以下店铺没有拿到更新结果：%s" % ", ".join(bad))
        return 5
    log("[OK] 更新订单完成（勾选 %d 个店铺，均已出结果）" % len(ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
