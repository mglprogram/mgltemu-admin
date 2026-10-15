/**
 * 大屏「在售商品销量排行」预览（用真实 goods.json 复算排序结果）
 *
 * 目的：前端 composable 里的分组/排序逻辑与 product-family.ts 共用同一份规则，
 * 这里用同一份数据把 Top N 打出来，上大屏前先对数。
 *
 * 跑法（在 temu-admin 目录）：
 *   node scripts/preview-rank.ts                    # 全部页签
 *   node scripts/preview-rank.ts --store BHY        # 单店页签
 *   node scripts/preview-rank.ts --top 15
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { buildFamilyRows, storeTag, type FamilyRow } from "../src/api/temu/goods/product-family.ts";

const here = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const storeArg = (() => {
  const i = argv.indexOf("--store");
  return i >= 0 ? (argv[i + 1] || "ALL").toUpperCase() : "ALL";
})();
const topN = (() => {
  const i = argv.indexOf("--top");
  return i >= 0 ? Number(argv[i + 1]) : 10;
})();
const file = resolve(here, "..", "public", "data", "goods.json");

const data = JSON.parse(readFileSync(file, "utf8"));
const items = Object.values<any>(data.stores || {}).flatMap((s: any) =>
  (s.items || []).map((it: any) => ({
    code: it.code,
    store: it.store || s.label || "",
    todaySales: it.todaySales ?? 0,
    sales7: it.sales7 ?? 0,
    stock: it.stock ?? 0,
  }))
);
const scoped = storeArg === "ALL" ? items : items.filter((i) => i.store === storeArg);
// withStore：单店页签 = true（只在店内合）；全店汇总 = false（跨店合）
const fams = buildFamilyRows(scoped, storeArg !== "ALL");

const hasToday = fams.some((f) => f.todaySales > 0);
const ranked = [...fams].sort((a, b) =>
  hasToday ? b.todaySales - a.todaySales || b.sales7 - a.sales7 : b.sales7 - a.sales7
);

console.log(`数据：${file}`);
console.log(`生成：${data.generatedAt}｜页签：${storeArg}｜在售 ${scoped.length} 个 SKC → ${fams.length} 个「款」`);
console.log(`排序：${hasToday ? "今日销量降序（回落近7日）" : "近7日降序（今天都没销量）"}\n`);

const fmt = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));
ranked.slice(0, topN).forEach((f: FamilyRow, i: number) => {
  const tag = storeTag(f.stores);
  const members = f.members.length > 1 ? `  ⇐ ${f.members.join(" + ")}` : "";
  const notes = f.memberNotes.length ? `  [${f.memberNotes.join(" | ")}]` : "";
  console.log(
    `${String(i + 1).padStart(2)}. ${f.code.padEnd(20)} ${tag.padEnd(4)} ` +
      `今日 ${fmt(f.todaySales).padStart(5)}  7日 ${fmt(f.sales7).padStart(6)}  库存 ${fmt(f.stock).padStart(6)}${members}${notes}`
  );
});

// 自检：全部页签的量 = 两店单店口径之和？跨店特例会重复计入，逐条列出来
if (storeArg === "ALL") {
  const per = ["BHY", "BEL"].map((s) => buildFamilyRows(items.filter((i) => i.store === s), true));
  const sum = (rows: FamilyRow[]) =>
    rows.reduce((acc, f) => ({ t: acc.t + f.todaySales, s: acc.s + f.sales7 }), { t: 0, s: 0 });
  const all = sum(fams);
  const two = per.reduce((acc, rows) => {
    const x = sum(rows);
    return { t: acc.t + x.t, s: acc.s + x.s };
  }, { t: 0, s: 0 });
  console.log(
    `\n对账：全部口径 今日 ${fmt(all.t)} / 7日 ${fmt(all.s)}` +
      ` ｜两店单店口径之和 今日 ${fmt(two.t)} / 7日 ${fmt(two.s)}` +
      `（差 ${fmt(all.t - two.t)} = 跨店特例 H103903 在单店口径里也含倍率后的两店量，属预期）`
  );
}
