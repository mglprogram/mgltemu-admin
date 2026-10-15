/**
 * 「同款合并」口径（大屏销量榜 / 销量-库存 今日销量共用）
 *
 * ---------------------------------------------------------------------------
 * 背景：TEMU 上同一个产品会拆成多个 SKC 上架。看数据的人要的是「这一个产品」
 * 今天卖了多少，而不是「这几个货号各自卖了多少」。所以销量一律先按产品归组相加。
 *
 * 归组规则（用户在 2026-10-14 / 10-15 两次明确）：
 *
 *   1. 通例（自动）：同款不同变体
 *      `BEL-204203-4SetC` + `BHY-204203-4SetA` → `204203-4Set`
 *      做法：去掉店铺前缀，再去掉 `…Set` 后面的变体尾巴（A / C / A1 / A1.1 …）。
 *      没有 `Set` 字样的款（如 102105-3BHL）**不合并**，避免误伤。
 *      页签决定合并范围：全店汇总 = 两店一起合（跨店相加）；BHY / BEL 页签只在各自店内合。
 *
 *   2. 特例（人工指定，跨店 + 强制归并）：`202504-10` 全系列
 *      `BHY-202504-10SetA / 10SetB / 10SetC` + `BEL-202504-10SetB / 10SetG`
 *      → 全部累加到一行，展示为 `202504-10SetA`。
 *
 *   3. 特例（人工指定，**带倍率**）：`H103903-2B`
 *      `H103903-4B` = 2 × `H103903-2B`，`H103903-6B` = 3 × `H103903-2B`
 *      → 全部累加到 `H103903-2B`（4B 的数乘 2、6B 的数乘 3 再加）。
 *      倍率只影响**数值相加**；表格里显示的仍是原始件数（不做单位换算），
 *      倍率写在合并行的成员说明里，点开可核对。
 *
 * ⚠ 这两条特例是**跨店**归并（不限页签）。用户原话：
 *   「把所有 202504-10 的产品全部累加到 BHY-202504-10SetA」
 *   「H103903-4B 对应 2 个 H103903-2B、6B 对应 3 个，全部累加到 H103903-2B」
 *   所以即使切到单店页签，202504-10 也只出现一行（两店的量相加）。
 *
 * ---------------------------------------------------------------------------
 * 维护提示：改动本文件后请跑一遍自检（BHY/BEL 实测数据），确认
 *   202504-10 → 一行、H103903-2B = 2B + 2×4B + 3×6B，其余款分组不变。
 */

/** 一条「特例」规则：命中 regexOf 的货号一律归入同一组 */
export interface FamilyRule {
  /** 规则名（自检 / 悬浮说明用） */
  id: string;
  /** 说明（给人看的） */
  note: string;
  /** 命中判定（对「去掉店铺前缀」后的款号匹配） */
  test: RegExp;
  /** 展示用款号（可带店铺前缀，如 BHY-202504-10SetA） */
  display: string;
  /** 倍率：该货号的数 × 倍率 后累加（默认 1） */
  factor?: (code: string) => number;
}

/** 去掉店铺前缀：`BHY-202504-10SetA` → `202504-10SetA`；`BEL-H103903-6B` → `H103903-6B` */
export function storeOf(code: string): string {
  const m = String(code || "").match(/^([A-Za-z0-9]{2,4})-/);
  // 前缀可能是纯数字货号的一部分（`202504-10SetA` 的 `202504`）——只有全字母/带数字的
  // 店铺码才算店铺，这里用「纯字母」和已知店铺兜底：TEMU 侧店铺码是 BEL / BHY（2~3 位字母）。
  return m && /^[A-Za-z]{2,4}$/.test(m[1]) ? m[1].toUpperCase() : "";
}

/** 去掉店铺前缀 */
export function stripStore(code: string): string {
  const s = String(code || "");
  const store = storeOf(s);
  return store ? s.slice(store.length + 1) : s;
}

/** 通例：去掉 `…Set` 后面的变体尾巴；没有 Set 的原样返回 */
export function baseOf(code: string): string {
  const rest = stripStore(code);
  const m = rest.match(/^(.*?\d+Set)/i);
  return m ? m[1] : rest;
}

/**
 * 人工指定的特例规则表。
 *
 * 顺序有意义：先匹配到的生效（两条规则不重叠，但保持显式）。
 */
export const FAMILY_RULES: FamilyRule[] = [
  {
    id: "202504-10",
    note:
      "202504-10 全系列（10SetA/B/C/G 各店）合并到一行，展示为 BHY-202504-10SetA（用户 2026-10-15 指定）",
    // 10SetA / 10SetB / 10SetC / 10SetG …（`Set` 后 0~2 个字母，覆盖 A1 / C1.1 这类尾巴的字母主体）
    test: /^202504-10Set[A-Za-z]{0,2}$/i,
    display: "BHY-202504-10SetA",
  },
  {
    id: "H103903-2B",
    note:
      "H103903-4B = 2 × H103903-2B，H103903-6B = 3 × H103903-2B，全部累加到 H103903-2B（用户 2026-10-15 指定）",
    // 2B（本体，倍率 1）/ 4B（×2）/ 6B（×3）；2BH、4BH 等其它尾巴不并入
    test: /^H103903-(2|4|6)B$/i,
    display: "H103903-2B",
    factor: (code: string) => {
      const m = stripStore(code).match(/-(\d+)B$/i);
      const n = m ? Number(m[1]) : 2;
      // 4B → 2 个 2B；6B → 3 个 2B；2B → 1 个
      return n >= 2 ? n / 2 : 1;
    },
  },
];

/** 命中特例规则；没命中返回 null */
export function familyRule(code: string): FamilyRule | null {
  const rest = stripStore(code);
  return FAMILY_RULES.find((r) => r.test.test(rest)) || null;
}

/** 倍率（没命中特例 = 1） */
export function unitFactor(code: string): number {
  const r = familyRule(code);
  return r?.factor ? r.factor(code) : 1;
}

/**
 * 归组键。**同一个键 = 相加成同一行**。
 *
 * 特例规则返回 `rule:<id>`（跨店 + 跨变体）；其余返回 `base:<款号>`（由调用方决定
 * 是否把店铺并进去 —— 单店页签要按店分组，就传 `withStore`）。
 *
 * @param code      货号（可带店铺前缀）
 * @param withStore 通例是否按店铺分开（单店页签 = true；全店汇总 = false）
 */
export function familyKey(code: string, withStore = false): string {
  const rule = familyRule(code);
  if (rule) return `rule:${rule.id}`;
  const base = baseOf(code);
  return withStore ? `base:${storeOf(code)}|${base.toLowerCase()}` : `base:${base.toLowerCase()}`;
}

/** 归组后展示用款号：特例用规则里指定的款号，通例用去掉店铺前缀与变体尾巴的款号 */
export function displayCode(code: string): string {
  const rule = familyRule(code);
  return rule ? rule.display : baseOf(code);
}

/** 合并行的悬浮说明：倍率写在这里，方便核对 */
export function mergeNote(code: string): string {
  const f = unitFactor(code);
  return f === 1 ? "" : `×${f}（${stripStore(code)} 折 ${f} 个 ${displayCode(code)}）`;
}

/** 一行「同款」数据（大屏榜单 / 销量-库存 的今日销量都用这个结构） */
export interface FamilyRow {
  /** 归组键 */
  key: string;
  /** 展示用款号 */
  code: string;
  /** 组内成员货号（原样，带店铺前缀） */
  members: string[];
  /** 组内出现的店铺（去重、按出现顺序） */
  stores: string[];
  /** 今日销量（已按倍率累加） */
  todaySales: number;
  /** 近 7 日销量（已按倍率累加） */
  sales7: number;
  /** 库存合计（已按倍率累加） */
  stock: number;
  /** 特例规则说明（没命中为空） */
  ruleNote: string;
  /** 有倍率的成员说明（如 `×2（H103903-4B 折 2 个 H103903-2B）`） */
  memberNotes: string[];
}

/**
 * 把「一行一个货号」的销量聚合成「一行一个产品」。
 *
 * @param rows     每行的 `{ code, store?, todaySales?, sales7?, stock? }`
 * @param withStore 通例是否按店铺分开（单店页签传 true —— 只在店里合变体，不跨店）
 */
export function buildFamilyRows(
  rows: { code: string; store?: string; todaySales?: number | null; sales7?: number | null; stock?: number | null }[],
  withStore = false
): FamilyRow[] {
  const map = new Map<string, FamilyRow>();
  for (const r of rows) {
    const code = String(r.code || "");
    if (!code) continue;
    const key = familyKey(code, withStore);
    const f = unitFactor(code);
    const rule = familyRule(code);
    const cur = map.get(key);
    const store = r.store || storeOf(code);
    const note = mergeNote(code);
    if (!cur) {
      map.set(key, {
        key,
        code: displayCode(code),
        members: [code],
        stores: store ? [store] : [],
        todaySales: (r.todaySales || 0) * f,
        sales7: (r.sales7 || 0) * f,
        stock: (r.stock || 0) * f,
        ruleNote: rule?.note || "",
        memberNotes: note ? [`${code} ${note}`] : [],
      });
    } else {
      cur.todaySales += (r.todaySales || 0) * f;
      cur.sales7 += (r.sales7 || 0) * f;
      cur.stock += (r.stock || 0) * f;
      cur.members.push(code);
      if (store && !cur.stores.includes(store)) cur.stores.push(store);
      if (note) cur.memberNotes.push(`${code} ${note}`);
    }
  }
  return [...map.values()];
}

/**
 * 「同款今日销量合计」查询表：`shop|skuCode` → 该款（含同款变体）今日销量合计。
 *
 * 用途：销量-库存页每一行是一个具体 SKU，但看的人想知道「这个产品今天卖了多少」。
 * 键里带店铺，保证行与行的归属不串。
 */
export function todaySalesIndex(
  rows: { code: string; store?: string; todaySales?: number | null }[]
): Map<string, FamilyRow> {
  const out = new Map<string, FamilyRow>();
  for (const fam of buildFamilyRows(rows, true)) {
    for (const m of fam.members) {
      const store = storeOf(m);
      out.set(`${store}|${m}`, fam);
      // 也允许不带前缀的 key（调用方可能只有裸款号）
      if (!out.has(`|${m}`)) out.set(`|${m}`, fam);
    }
  }
  return out;
}

/** 合并行的店铺标签：两店都有 → 「两店」 */
export function storeTag(stores: string[]): string {
  const uniq = [...new Set(stores.filter(Boolean))];
  return uniq.length > 1 ? "两店" : uniq[0] || "";
}
