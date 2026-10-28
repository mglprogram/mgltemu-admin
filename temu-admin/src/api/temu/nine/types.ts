/**
 * 九部库存跟踪（TTSA-9 库存管理看板 · 6 个款号逐尺码）
 *
 * 数据来自 public/data/nine-inv.json，由 scripts/goods/fetch_nine_inv.py 每天 17:00 生成：
 *   数据源 https://cbe…app.workbuddy.host/（「TTSA-9库存管理看板」）内嵌的 `const INV`
 *   取 204203 / 204207 / 204004 / 103903 / 103902 / 103901 这 6 个款号的全部尺码行。
 */

/** 单个尺码（一个 SKU）*/
export interface NineInvRow {
  /** 母表 SKU，如 `204203-4SetA-L`（103902/103903 带 `T` 前缀，与看板一致） */
  sku: string;
  /** 纯数字款号：204203 */
  code: string;
  /** 变体/套系：4SetA / 5SetB / 2H … */
  variant: string;
  /** 尺码：S / M / L / XL / XXL … */
  size: string;
  /** 代发途中（恒信在途 + 帕沃尔代发途中） */
  transit: number;
  /** 代发库存（恒信可用 + 帕沃尔代发库存） */
  stock: number;
  /** 海外仓库存 = 代发途中 + 代发库存 */
  overseas: number;
  /** 七天日均 */
  daily: number;
  /** 可售天数 = 海外仓库存 ÷ 七天日均 */
  sellDays: number;
  /** 补货数量 =（60 − 可售天数）× 七天日均 */
  refill: number;
  /** 未交货订单 */
  openOrders: number;
  /** 订单+库存可售天数 */
  orderDays: number;
  /** 海外仓可售天数（只看代发库存，不看在途） */
  whDays: number;
  /** 库存状态：紧急补货 / 正常补货 / 无需补货 / 滞销库存 */
  status: string;
  /** 预计断货日（含在途排期） */
  stockout: string;
  /** 不含在途的断货日 */
  stockoutNoTransit: string;
  /** 逐仓代发库存：{仓库名: 件数} */
  wh: Record<string, number>;
  /** 逐仓代发途中 */
  whTransit: Record<string, number>;
  /** 上一次快照的代发库存（没有则为 null） */
  prevStock: number | null;
  /** 上一次快照的海外仓库存 */
  prevOverseas: number | null;
}

export interface NineInvTotals {
  stock: number;
  transit: number;
  overseas: number;
  openOrders: number;
  refill: number;
  daily: number;
  skus: number;
  sizes: number;
  urgent: number;
  normal: number;
  ok: number;
  dead: number;
}

export interface NineInvVariant {
  variant: string;
  /** 中文备注（4件套A 这类），没有就是空串 */
  note: string;
  rows: NineInvRow[];
  totals: NineInvTotals;
}

export interface NineInvStyle {
  code: string;
  variants: NineInvVariant[];
  totals: NineInvTotals;
}

export interface NineInvHistory {
  dates: string[];
  styles: Record<string, { overseas: number[]; stock: number[]; transit: number[] }>;
}

export interface NineInvData {
  generatedAt: string;
  board: {
    /** 看板自己的生成时间 */
    gen: string;
    url: string;
    /** 看板全量行数（588） */
    rows: number;
    /** 我们取到的行数（70） */
    picked: number;
    /** 看板自检结论 */
    verify: string;
    /** 看板生成时间不是今天 → 数据没刷新，页面顶黄条 */
    stale: boolean;
  };
  styles: NineInvStyle[];
  totals: NineInvTotals;
  history: NineInvHistory;
  rule: { note: string; source: string };
  /** 只在「今日对比」时由前端补，不落 JSON */
  store?: string;
}
