/**
 * 「同款合并」对账脚本（跑真实数据，打印每店的分组结果）
 *
 * 用途：改完 product-family.ts 的规则后，先跑这个看数字对不对，再上大屏。
 *
 * 跑法（在 temu-admin 目录）：
 *   node scripts/check-family-merge.ts                       # 用 dist/data（线上那份）
 *   node scripts/check-family-merge.ts public/data/goods.json
 *   node scripts/check-family-merge.ts --store BHY           # 只看某店（单店口径）
 *
 * 输出：
 *   ① 三份口径（全部 / BHY / BEL）各自的「款」数
 *   ② 目标款（202504-10、H103903-2B）的成员与数字明细
 *   ③ 一致性自检：全部口径的合计 = BHY + BEL（逐款）
 *      ⚠ 202504-10 这类跨店特例，单店口径下也已经含两店（规则如此），
 *        所以它在单店清单里也会出现 —— 这是预期，不报错，只在备注里标出来。
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import {
  buildFamilyRows,
  familyRule,
  storeOf,
  unitFactor,
  type FamilyRow,
} from "../src/api/temu/goods/product-family.ts";

type Item = { code: string; store?: string; todaySales?: number | null; sales7?: number | null; stock?: number | null };

const here = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const storeArg = (() => {
  const i = argv.indexOf("--store");
  return i >= 0 ? argv[i + 1] : "";
})();
const fileArg = argv.find((a) => !a.startsWith("--") && a.endsWith(".json"));
const file = fileArg
  ? resolve(process.cwd(), fileArg)
  : resolve(here, "..", "dist", "data", "goods.json");

const data = JSON.parse(readFileSync(file, "utf8"));
const all: Item[] = Object.values<any>(data.stores || {}).flatMap((s: any) =>
  (s.items || []).map((it: any) => ({
    code: it.code,
    store: it.store || s.label || "",
    todaySales: it.todaySales ?? 0,
    sales7: it.sales7 ?? 0,
    stock: it.stock ?? 0,
  }))
);

const fmt = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(2));
const line = (f: FamilyRow) =>
  [
    f.code.padEnd(22),
    `今日 ${fmt(f.todaySales).padStart(6)}`,
    `7日 ${fmt(f.sales7).padStart(7)}`,
    `库存 ${fmt(f.stock).padStart(7)}`,
    f.members.length > 1 ? `[${f.members.length} 项] ${f.members.join(" + ")}` : "",
    f.memberNotes.join(" | "),
  ]
    .filter(Boolean)
    .join("  ");

console.log(`数据文件：${file}`);
console.log(`生成时间：${data.generatedAt || "—"}`);
console.log(`在售 SKC：${all.length}（两店合计）\n`);

// ------------------------------------------------ ① 三份口径的款数
const perStore = ["BHY", "BEL"].filter((s) => !storeArg || s === storeArg);
const sizes: Record<string, FamilyRow[]> = {
  全部: buildFamilyRows(all, false),
  ...Object.fromEntries(perStore.map((s) => [s, buildFamilyRows(all.filter((i) => i.store === s), true)])),
};
for (const [k, v] of Object.entries(sizes)) {
  const merged = v.filter((f) => f.members.length > 1).length;
  console.log(`${k.padEnd(6)} 款数 ${String(v.length).padStart(4)}（其中合并行 ${merged}）`);
}
console.log();

// ------------------------------------------------ ② 目标款明细
const target = (code: string) => {
  const rule = familyRule(code);
  const want = rule ? rule.display.replace(/^[A-Za-z]{2,4}-/, "") : code;
  const hit = sizes.全部.filter((f) => f.code.includes(want) || f.members.some((m) => m.includes(code)));
  return hit;
};
for (const code of ["BHY-202504-10SetA", "H103903-2B", "H103903-4B", "H103903-6B"]) {
  const hit = target(code);
  console.log(`── 查 ${code}（倍率 ${unitFactor(code)}）`);
  if (!hit.length) console.log("   （没有命中）");
  for (const f of hit) console.log("   " + line(f));
  console.log();
}

// ------------------------------------------------ ③ 目标款逐项对账
console.log("── 对账：全部口径 vs 单店口径（只看目标款）");
for (const f of sizes.全部) {
  const notes = f.members.map((m) => unitFactor(m)).filter((x) => x !== 1);
  if (!notes.length) continue;
  const seed = f.members[0].replace(/^[A-Za-z]{2,4}-/, "").replace(/-[A-Z]$/, "");
  const per = perStore
    .map((s) => {
      const g = sizes[s].find((x) => x.members.some((m) => m.includes(seed.split("-")[0])));
      return g ? `${s} ${fmt(g.todaySales)}` : `${s} —`;
    })
    .join(" / ");
  console.log(`   ${f.code}: 全部今日 ${fmt(f.todaySales)}（${per}）`);
  console.log(`      倍率成员：${f.memberNotes.join(" | ") || "无"}`);
}

// ------------------------------------------------ ④ 反例检查：不该合并的款
console.log("\n── 反例（含倍率但未被规则覆盖 = 可能漏配）");
const suspicious = sizes.全部.filter(
  (f) => f.members.length === 1 && !familyRule(f.members[0]) && /-(2|4|6)B$/i.test(f.members[0])
);
if (!suspicious.length) console.log("   无");
for (const f of suspicious) console.log("   " + line(f));

// ------------------------------------------------ ⑤ 店铺归属检查
console.log("\n── 店铺归属（合并行里出现的店铺）");
for (const f of sizes.全部.filter((x) => x.members.length > 1)) {
  const stores = [...new Set(f.members.map((m) => storeOf(m) || "?"))];
  if (stores.length > 1) console.log(`   ${f.code} → ${stores.join(" + ")}`);
}
