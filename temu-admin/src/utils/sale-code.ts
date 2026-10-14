/**
 * 款号 → 短码（地图/大屏上显示用）
 *
 * 背景（用户 2026-10-14）：地图 tooltip 上写全名 `BH-102303-6SetA` 太长，
 * 要求「直接 102104 就可以」—— 也就是取款号里那段 4~6 位数字。
 *
 *   BH-102105-3BHL   → 102105
 *   BLA-102104-5SetA → 102104
 *   BS2-202504-5SetA → 202504
 *   BS-R1026-3BHW    → 1026      （只有 4 位也认）
 *   BH-YOGA-4SetA    → YOGA-4SetA（一个 4~6 位数字都没有 → 退回「去掉品牌前缀」）
 *
 * ⚠ 短码会**撞号**：`BH-102105-3BHL` 与 `BH-102105-3BGR` 都是 `102105`。
 *   所以展示时必须去重，否则会出现「102105 · 102105 · 202504」。
 */
export function saleCode(style: string): string {
  const s = (style || "").trim();
  if (!s) return "";
  const m = s.match(/\d{4,6}/);
  if (m) return m[0];
  // 没有 4~6 位数字（如 BH-YOGA-4SetA）→ 去掉品牌前缀，保留可读的后半段
  return s.replace(/^[A-Za-z0-9]+-/, "");
}

/** 取某州/某款榜单里的「畅销款 Top N」（按传入顺序 = 单量降序，短码去重） */
export function topSaleCodes(
  items: { name: string }[] | undefined,
  n = 3
): string[] {
  const out: string[] = [];
  for (const it of items || []) {
    const code = saleCode(it.name);
    if (code && !out.includes(code)) out.push(code);
    if (out.length >= n) break;
  }
  return out;
}
