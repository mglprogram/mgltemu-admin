/**
 * 滞销库存类型定义
 *
 * 数据由 scripts/goods/build_stale_stock_data.py 从飞书导出，
 * 口径与 temu-bottom-stock-sync 的通知/报表**完全一致**（别另起一套）：
 *   · 库存 = 领星 OMS 加州3仓 + 帕沃尔 Kansas 仓
 *   · 销量 = 领星 OMS 出库单（5天是实测，3天用于「连续三天零动销」）
 *   · 可售天数 = (代发库存 + 代发途中) / (5天销量 / 5)
 *   · 有动销 = 5天销量 > 0；无动销 = 5天销量 = 0
 *   · 希音款抓不到数据，单列一档，**不能算滞销**
 */

/** 一款的当前值 + 近 N 天趋势 */
export interface StaleSku {
  /** 款号（= 飞书「链接名称」） */
  link: string;
  /** 款式类别 */
  cat: string;
  /** 代发库存（领星加州3仓 + 帕沃尔 Kansas） */
  stock: number;
  /** 代发途中 */
  transit: number;
  /** 总管道 = 库存 + 途中 */
  pipeline: number;
  /** 近 5 天销量（领星出库单，实测） */
  sales5: number;
  /** 近 3 天销量（用于「连续三天零动销」） */
  sales3: number;
  /** 可售天数 = 总管道 / 日均；5天销量=0 时为 null */
  days: number | null;
  /** 库存健康度：积压 / 无销量 / 偏紧 / 健康 / 严重缺货 */
  health: string;
  /** 数据状态（正常 / 未找到SKU / 自定义备注） */
  status: string;
  /** 抓不到数据（希音渠道等），不算滞销 */
  missing: boolean;
  /** 有动销 = 5天销量 > 0 */
  active: boolean;
  /** 日均销量 = 5天销量 / 5 */
  daily: number;
  /** 逐日趋势 */
  trend: StaleTrendPoint[];
}

/** 单款单日 */
export interface StaleTrendPoint {
  d: string;
  stock: number;
  sales5: number;
}

/** 按款式类别聚合 */
export interface StaleCategory {
  cat: string;
  skus: number;
  stock: number;
  sales5: number;
  inactive: number;
}

/** 每日汇总趋势（来自「每日款明细」求和） */
export interface StaleTrendDay {
  d: string;
  stock: number;
  sales5: number;
  skus: number;
}

/** 每日快照（含健康度款数） */
export interface StaleSnapshot {
  d: string;
  stock: number;
  transit: number;
  sales5: number;
  sales3: number;
  healthy: number;
  overstock: number;
  nosale: number;
}

export interface StaleSummary {
  skus: number;
  matched: number;
  missing: number;
  active: number;
  inactive: number;
  stock: number;
  transit: number;
  pipeline: number;
  sales5: number;
  sales3: number;
  daily: number;
  inactiveStock: number;
  inactiveTransit: number;
  inactivePipeline: number;
  /** 无动销压货全部清完要几天（按有动销款的日均），无销量时为 null */
  inactiveClearDays: number | null;
}

export interface StaleStockData {
  generatedAt: string;
  source: string;
  days: number;
  summary: StaleSummary;
  healthCounts: Record<string, number>;
  active: StaleSku[];
  inactive: StaleSku[];
  missing: StaleSku[];
  categories: StaleCategory[];
  trend: StaleTrendDay[];
  snapshots: StaleSnapshot[];
}
