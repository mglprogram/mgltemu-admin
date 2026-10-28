/**
 * 库存跟踪（九部 + 十部 · 6 个款号逐尺码）
 *
 * 数据来自 public/data/stock-track.json，由 scripts/goods/fetch_stock_track.py
 * 每天 17:00 生成：
 *   九部 https://cbec756b1bad42778134c59af1ab67ce.app.workbuddy.host/  (const INV)
 *   十部 https://warehouse-probe.app.workbuddy.host/                   (const SNAP)
 *
 * 口径：**实际库存 = 代发库存**（已在海外仓、能直接发）。
 * 代发途中在路上的不算可用；海外仓库存 = 实际库存 + 在途，只作参考。
 */

/** 单个尺码（一个 SKU）*/
export interface TrackRow {
  /** 源表原样 SKU，如 `204203-4SetA-L` / `H103902-4B-L` */
  sku: string;
  /** 纯数字款号：204203 */
  code: string;
  /** 变体/套系：4SetA / 5SetB / 2H … */
  variant: string;
  /** 尺码：S / M / L / XL / XXL … */
  size: string;
  /** 实际库存（= 代发库存，已在海外仓可直接发） */
  stock: number;
  /** 代发途中（在路上，尚不可发） */
  transit: number;
  /** 海外仓库存 = 实际库存 + 在途 */
  overseas: number;
  /** 七天日均（九部为看板原值；十部取「近7天日均」款级值） */
  daily: number;
  /** 可售天数（九部看板原值；十部本页自算） */
  sellDays: number;
  /** 补货数量（九部看板原值；十部本页自算） */
  refill: number;
  /** 未交货订单（款级值，不按行累加） */
  openOrders: number;
  /** 订单+库存可售天数（仅九部有） */
  orderDays: number;
  /** 现货可售天数（只看实际库存，不含在途） */
  whDays: number;
  /** 库存状态：紧急补货 / 正常补货 / 无需补货 / 滞销库存 / 现货已断 */
  status: string;
  /** 预计断货日（含在途，仅九部有） */
  stockout: string;
  /** 不含在途的断货日（仅九部有） */
  stockoutNoTransit: string;
  /** 10 天销量（仅十部有） */
  sales10: number | null;
  /** 逐仓实际库存：{仓库名: 件数} */
  wh: Record<string, number>;
  /** 逐仓代发途中 */
  whTransit: Record<string, number>;
  /** 十部：这一行聚合了哪些店铺（BS/BH/BK…） */
  shops?: string[];
  /** 十部：货源（帕沃尔 / 恒信） */
  sources?: string[];
  /** 十部：聚合了多少条源表行 */
  rawRows?: number;
  /** 这个 SKU 的天数/补货/状态是本页自算的（十部没有这三列） */
  derived: boolean;
  /** 上一次快照的实际库存（没有则 null/undefined） */
  prevStock?: number | null;
  /** 上一次快照的海外仓库存 */
  prevOverseas?: number | null;
}

export interface TrackTotals {
  stock: number;
  transit: number;
  overseas: number;
  openOrders: number;
  refill: number;
  daily: number;
  sales10: number;
  skus: number;
  sizes: number;
  urgent: number;
  normal: number;
  ok: number;
  dead: number;
  /** 实际库存 = 0 的尺码数 */
  zero: number;
}

export interface TrackVariant {
  variant: string;
  /** 中文备注（4件套A 这类），没有就是空串 */
  note: string;
  rows: TrackRow[];
  totals: TrackTotals;
}

export interface TrackStyle {
  code: string;
  variants: TrackVariant[];
  totals: TrackTotals;
}

export interface TrackHistory {
  dates: string[];
  styles: Record<string, { overseas: number[]; stock: number[]; transit: number[] }>;
}

/** 一个部门（九部 / 十部）的完整数据 */
export interface TrackSource {
  key: "nine" | "ten";
  label: string;
  short: string;
  url: string;
  /** 看板自己的生成时间 */
  gen: string;
  /** 看板全量行数 */
  totalRows: number;
  /** 看板自检结论（仅九部有） */
  verify: string;
  /** 快照归档日期 */
  day: string;
  /** 看板生成时间不是今天 → 数据没刷新，页面顶黄条 */
  stale: boolean;
  styles: TrackStyle[];
  totals: TrackTotals;
  history: TrackHistory;
  note: string;
}

export interface StockTrackData {
  generatedAt: string;
  styles: string[];
  sources: Record<string, TrackSource>;
  /** 抓取失败的源 */
  failed: string[];
  rule: { note: string; source: string };
}
