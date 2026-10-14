# -*- coding: utf-8 -*-
"""在「上新生命周期管理」页按 SKC 货号批量查询，抽取申报价 / 日常申报价 / 活动申报价。

流程（每批）：
  1. 把货号列表注入 window.__CODES__
  2. 用 React 原生 setter 写入受控输入框（page input 会被 React 重置）
  3. 点「查询」
  4. 解析结果表格：SKC 属性(申报价格) / SKU 属性集(站点申报价格 + 处理方式里的日常/活动申报价)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

ENTRY = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"
STORE_ID = os.environ.get("BEL_STORE_ID", "27677489075954")
TRAFFIC_JSON = Path(os.environ.get(
    "TRAFFIC_JSON", r"D:\AI-workplace\TEMU\data\bel\_traffic_raw.json"))
OUT = Path(os.environ.get("PRICE_OUT", r"D:\AI-workplace\TEMU\data\bel\_price_raw.json"))
HERE = Path(__file__).resolve().parent


def run(args, timeout=180):
    p = subprocess.run(["node", str(ENTRY)] + args, capture_output=True,
                       text=True, encoding="utf-8", timeout=timeout)
    if not p.stdout.strip():
        raise RuntimeError(f"no output: rc={p.returncode} err={p.stderr[:400]}")
    return json.loads(p.stdout)


def exec_js(js: str, timeout=180):
    inner = run(["page", "exec", "--store-id", STORE_ID, "--script", js], timeout)["data"]["data"]
    if inner.get("exceptionDetails"):
        raise RuntimeError("page exception: " + json.dumps(inner["exceptionDetails"], ensure_ascii=False)[:500])
    return inner["result"]


def exec_json(js: str):
    """页面脚本可能返回 JSON 字符串，也可能直接返回对象。"""
    raw = exec_js(js)
    return json.loads(raw) if isinstance(raw, str) else raw


SET_CODES = (HERE / "set_codes.js").read_text(encoding="utf-8").strip()

INJECT = "window.__CODES__ = %s;\n" + SET_CODES

CLICK_QUERY = """(() => {
  const btn = Array.from(document.querySelectorAll('button'))
    .find(b => (b.innerText || '').trim() === '查询');
  if (!btn) return 'no query btn';
  btn.click();
  return 'clicked';
})()"""

ENSURE_SKC = r"""(() => {
  const el = document.querySelector('input[placeholder="多个货号以空格，逗号分隔"]');
  if (!el) return JSON.stringify({err: 'no code input'});
  const row = el.closest('div[class*=row_field]');
  const sel = row.querySelector('input[readonly]');
  if (sel.value === 'SKC') return JSON.stringify({type: 'SKC', changed: false});
  sel.click();
  const opt = Array.from(document.querySelectorAll('li')).find(
    e => e.offsetParent !== null && (e.innerText || '').trim() === 'SKC'
          && String(e.className).includes('cIL_item'));
  if (!opt) return JSON.stringify({type: sel.value, changed: false, note: 'no SKC option'});
  opt.click();
  return JSON.stringify({type: 'SKC', changed: true});
})()"""

PARSE = (HERE / "parse_prices.js").read_text(encoding="utf-8").strip()


def clean(codes):
    """货号清洗：去掉抓取时混入的小数尾巴（如 A1.0），并保序去重。"""
    out = []
    for c in codes:
        c = str(c).strip()
        if "." in c and re.search(r"\.\d+$", c):
            c = c[:c.rindex(".")]
        if c and c not in out:
            out.append(c)
    return out


def main():
    traffic = json.loads(TRAFFIC_JSON.read_text(encoding="utf-8"))
    codes = clean([r["sku_code"] for r in traffic])
    print(f"待查货号 {len(codes)} 个（清洗去重后）")

    # 确保货号类型下拉为 SKC
    t = exec_json(ENSURE_SKC)
    print("货号类型:", t)
    if t.get("type") != "SKC":
        print("!! 未能切到 SKC，终止以免查错字段")
        return 2
    time.sleep(2)

    BATCH = 5
    seen, all_rows = {}, []
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i + BATCH]
        payload = json.dumps(",".join(batch), ensure_ascii=False)
        r = exec_json("(() => { %s })()" % (INJECT % payload))
        got = r.get("len") if isinstance(r, dict) else None
        exec_js(CLICK_QUERY)
        time.sleep(10)
        res = exec_json(PARSE)
        new = [x for x in res["rows"] if x["code"] not in seen]
        for x in new:
            seen[x["code"]] = x
        all_rows = list(seen.values())
        print(f"  批 {i//BATCH+1}: 输入{len(batch)} 解析{res['count']} 新增{len(new)} 累计{len(all_rows)}")

    OUT.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n合计 {len(all_rows)}/{len(codes)} -> {OUT}")
    miss = set(codes) - set(seen)
    if miss:
        print("未查到:", sorted(miss))


if __name__ == "__main__":
    main()