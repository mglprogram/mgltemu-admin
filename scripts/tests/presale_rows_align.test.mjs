#!/usr/bin/env node
/**
 * 列对齐测试：`_ROWS_JS` 里「按尾部对齐」取今日SKC/今日SKU/预计7日/预计14日 的逻辑。
 *
 * 现场证据（BHY 销量分析，rowspan 合并）：
 *   组首行 XXL：[3]=今日SKC 0 [4]=今日SKU 0 [5]=预计7日 12 [6]=预计14日 30 [8]=黑五 49
 *   续行 M    ：[1]=今日SKU 0 [2]=预计7日 13 [3]=预计14日 32 [5]=黑五 59
 *                ↑ 缺「今日SKC销量」格，整段左移一位 —— 旧代码固定下标会读错。
 *
 * 跑法：node --test scripts/tests/presale_rows_align.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";

/** 与 _ROWS_JS 里完全相同的算法（保持同步，改一边必须改另一边） */
function pickNums(cells, hasBF) {
  const num = (t) => {
    const m = String(t ?? "").replace(/,/g, "").match(/-?\d+(\.\d+)?/);
    return m ? Number(m[0]) : null;
  };
  const si = cells.findIndex((c) => c.indexOf("SKU货号") >= 0);
  const mi = cells.findIndex((c) => /发货$/.test(c));
  if (si < 0 || mi < 0 || mi <= si) return null;
  const seg = cells.slice(si + 1, mi).map(num).filter((v) => v !== null);
  let nums = seg;
  let note = "";
  if (seg.length >= 4) {
    const n = seg.length;
    const pick = (back) => (n - back >= 0 ? seg[n - back] : null);
    const tail = hasBF ? [pick(5), pick(4), pick(3), pick(2)]
                       : [pick(4), pick(3), pick(2), pick(1)];
    const ok = tail[2] !== null && tail[3] !== null && tail[2] <= tail[3];
    if (ok) { nums = tail; note = hasBF ? "tail-bf" : "tail"; } else { nums = seg; note = "fixed"; }
  }
  return {
    today_skc: nums.length > 0 ? nums[0] : null,
    today_sku: nums.length > 1 ? nums[1] : null,
    forecast7: nums.length > 2 ? nums[2] : null,
    forecast14: nums.length > 3 ? nums[3] : null,
    note,
  };
}

// 组首行：整行，含「商品信息」和「今日SKC销量」
const FIRST = [
  "",
  "4pcs ... SPU： 8312381638 SKC： 12078640547 SKC货号： BHY-YOGA-4SetA 爆款",
  "混合色-XXL SKU ID： 74742032837 SKU货号： BHY-YOGA-4SetA-XXL 旺款",
  "0", "0", "12", "30", "", "49",
  "卖家自发货", "0", "-", "-", "已售罄 连续售罄1天", "123.65", "85.67", "美国站", "查看", "立即加库存",
];

// 续行：前两格被 rowspan 合并掉（缺「商品信息」和「今日SKC销量」）
const CONT = [
  "混合色-M SKU ID： 61113111283 SKU货号： BHY-YOGA-4SetA-M 爆款",
  "0", "13", "32", "", "59",
  "卖家自发货", "0", "-", "-", "已售罄 连续售罄1天", "494.62", "美国站",
];

test("组首行：SKU=0，预计7日=12", () => {
  const r = pickNums(FIRST, true);
  assert.equal(r.today_skc, 0);
  assert.equal(r.today_sku, 0, "今日 SKU 销量应为 0，不是 12");
  assert.equal(r.forecast7, 12);
  assert.equal(r.forecast14, 30);
  assert.equal(r.note, "tail-bf");
});

test("续行（缺今日SKC格）：今日SKU=0，预计7日=13（不是 13 当销量）", () => {
  const r = pickNums(CONT, true);
  assert.equal(r.today_sku, 0, "TMUE 可售为 0 时今日销量必须是 0");
  assert.equal(r.forecast7, 13, "预计7日 应取 13");
  assert.equal(r.forecast14, 32);
});

test("续行不再出现「预计7日 被当成今日SKU」", () => {
  const r = pickNums(CONT, true);
  assert.notEqual(r.today_sku, 13);
  assert.notEqual(r.forecast7, 32);
});

test("无「预计至黑五」列时也能正确（段更短）", () => {
  // 8 列版本：[今日SKC, 今日SKU, 7日, 14日] 后直接发货模式
  const cells = [
    "x SKU ID： 1 SKU货号： A-B-1-L 旺款",
    "0", "0", "17", "34", "卖家自发货", "0",
  ];
  const r = pickNums(cells, false);
  assert.equal(r.today_sku, 0);
  assert.equal(r.forecast7, 17);
  assert.equal(r.forecast14, 34);
});

test("日期式尾部（正常结构）仍取对的四格", () => {
  // 段 = [SKC, SKU, 7日, 14日, 黑五]（5 个）
  const cells = ["x SKU ID： 1 SKU货号： A-B-1-L", "2", "3", "20", "40", "60", "卖家自发货"];
  const r = pickNums(cells, true);
  assert.equal(r.today_skc, 2);
  assert.equal(r.today_sku, 3);
  assert.equal(r.forecast7, 20);
  assert.equal(r.forecast14, 40);
});
