/**
 * 「同款合并」口径自检（node:test，无需额外依赖）
 *
 * 跑法（在 temu-admin 目录）：
 *   node --test src/api/temu/goods/product-family.test.ts
 *
 * 覆盖用户 2026-10-14 / 10-15 给的三条口径 + 反例（不该被误合并的款）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  baseOf,
  buildFamilyRows,
  displayCode,
  familyKey,
  familyRule,
  storeOf,
  stripStore,
  todaySalesIndex,
  unitFactor,
} from "./product-family.ts";

test("店铺前缀识别：字母店铺码才算店铺", () => {
  assert.equal(storeOf("BHY-202504-10SetA"), "BHY");
  assert.equal(storeOf("BEL-H103903-6B"), "BEL");
  assert.equal(storeOf("202504-10SetA"), "", "纯数字货号不能被当店铺");
  assert.equal(storeOf("H103903-2B"), "", "H103903 是款号不是店铺");
  assert.equal(stripStore("BHY-202504-10SetA"), "202504-10SetA");
  assert.equal(stripStore("202504-10SetA"), "202504-10SetA");
});

test("通例：…Set 后面的变体尾巴被合并，非 Set 款不合并", () => {
  assert.equal(baseOf("BHY-204203-4SetA"), "204203-4Set");
  assert.equal(baseOf("BEL-204203-4SetC"), "204203-4Set");
  assert.equal(baseOf("BHY-202504-10SetB"), "202504-10Set");
  assert.equal(baseOf("BHY-102105-3BHL"), "102105-3BHL", "没有 Set 的原样返回");
  // 单店页签：同款不同店不该并到一行
  assert.notEqual(familyKey("BHY-204203-4SetA", true), familyKey("BEL-204203-4SetC", true));
  // 全店汇总：跨店并成一行
  assert.equal(familyKey("BHY-204203-4SetA", false), familyKey("BEL-204203-4SetC", false));
});

test("特例①：202504-10 全系列（含两店）归到一行，展示 BHY-202504-10SetA", () => {
  const codes = [
    "BHY-202504-10SetA",
    "BHY-202504-10SetB",
    "BHY-202504-10SetC",
    "BEL-202504-10SetB",
    "BEL-202504-10SetG",
  ];
  const keys = new Set(codes.map((c) => familyKey(c)));
  assert.equal(keys.size, 1, `应只有一个归组键，实际 ${[...keys].join(",")}`);
  assert.equal(keys.has("rule:202504-10"), true);
  for (const c of codes) {
    assert.equal(displayCode(c), "BHY-202504-10SetA");
    assert.equal(unitFactor(c), 1, "10Set 各变体之间无量纲换算");
  }
  // 即使切到单店页签，特例依然跨店只出现一行
  assert.equal(familyKey("BHY-202504-10SetA", true), familyKey("BEL-202504-10SetB", true));
  // 不误伤：别的 202504-XX 款
  assert.equal(familyRule("BHY-202504-5SetA"), null);
});

test("特例②：H103903-4B ×2、6B ×3 全部累加到 H103903-2B", () => {
  assert.equal(unitFactor("BHY-H103903-2B"), 1);
  assert.equal(unitFactor("BHY-H103903-4B"), 2);
  assert.equal(unitFactor("BEL-H103903-6B"), 3);
  for (const c of ["BHY-H103903-2B", "BHY-H103903-4B", "BEL-H103903-6B"]) {
    assert.equal(displayCode(c), "H103903-2B");
  }
  // 2BH / 4BH 这类别的尾巴不并入
  assert.equal(familyRule("BHY-H103903-2BH"), null);
  assert.equal(familyRule("BHY-H103903-4BH"), null);
});

test("buildFamilyRows：两店「全部」口径 = 原样相加（倍率生效）", () => {
  // 取自 2026-10-15 线上 goods.json 的真实数字
  const rows = [
    { code: "BHY-202504-10SetA", store: "BHY", todaySales: 19, sales7: 373, stock: 541 },
    { code: "BHY-202504-10SetB", store: "BHY", todaySales: 1, sales7: 48, stock: 386 },
    { code: "BHY-202504-10SetC", store: "BHY", todaySales: 0, sales7: 0, stock: 806 },
    { code: "BEL-202504-10SetB", store: "BEL", todaySales: 3, sales7: 46, stock: 212 },
    { code: "BEL-202504-10SetG", store: "BEL", todaySales: 1, sales7: 0, stock: 250 },
  ];
  const fams = buildFamilyRows(rows, false);
  assert.equal(fams.length, 1);
  assert.equal(fams[0].code, "BHY-202504-10SetA");
  assert.equal(fams[0].todaySales, 24, "19+1+0+3+1");
  assert.equal(fams[0].sales7, 467, "373+48+0+46+0");
  assert.equal(fams[0].stock, 2195);
  assert.deepEqual(fams[0].stores, ["BHY", "BEL"]);
});

test("buildFamilyRows：H103903 单店口径带倍率相加", () => {
  const bhy = buildFamilyRows(
    [
      { code: "BHY-H103903-2B", store: "BHY", todaySales: 0, sales7: 134 },
      { code: "BHY-H103903-4B", store: "BHY", todaySales: 0, sales7: 0 },
      { code: "BHY-H103903-2BH", store: "BHY", todaySales: 0, sales7: 43 },
    ],
    true
  );
  const twoB = bhy.find((f) => f.code === "H103903-2B")!;
  assert.equal(twoB.sales7, 134, "2B + 4B×2（4B 是 0）");
  assert.equal(bhy.length, 2, "2BH 单独一行");
  const bel = buildFamilyRows(
    [
      { code: "BEL-H103903-2B", store: "BEL", todaySales: 0, sales7: 1 },
      { code: "BEL-H103903-6B", store: "BEL", todaySales: 11, sales7: 20 },
    ],
    true
  );
  assert.equal(bel.length, 1);
  assert.equal(bel[0].todaySales, 33, "0 + 11×3");
  assert.equal(bel[0].sales7, 61, "1 + 20×3");
  assert.equal(bel[0].memberNotes.length, 1, "只有 6B 带倍率说明");
});

test("todaySalesIndex：按 店铺|SKU 查同款合计，两店不串", () => {
  const idx = todaySalesIndex([
    { code: "BHY-204203-4SetA", store: "BHY", todaySales: 2 },
    { code: "BHY-204203-4SetC", store: "BHY", todaySales: 3 },
    { code: "BEL-204203-4SetC", store: "BEL", todaySales: 7 },
  ]);
  assert.equal(idx.get("BHY|BHY-204203-4SetA")!.todaySales, 5);
  assert.equal(idx.get("BHY|BHY-204203-4SetC")!.todaySales, 5);
  assert.equal(idx.get("BEL|BEL-204203-4SetC")!.todaySales, 7, "BEL 页签不该串到 BHY 的量");
});
