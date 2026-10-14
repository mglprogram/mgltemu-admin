/**
 * 限流（商品流量视角 · 待处理商品）数据类型
 *
 * 数据由 scripts/bel_goods/build_api_data.py 从
 *   data/<store>/<store>_products.db  表 traffic_limited*
 *   data/focus_watch_latest.json      重点 SPU 监控
 *   data/trend_data.json              历史趋势
 * 导出到 public/data/limited.json，前端静态 fetch。
 */

/** 单个限流商品 */
export interface LimitedItem {
  /** SPU ID */
  spuId: string;
  /** 货号（款号） */
  code: string;
  /** 商品标题 */
  title: string | null;
  /** 流量状态，如「流量预计100%+」 */
  flowStatus: string | null;
  /** 当前申报价 USD */
  declarePrice: number | null;
  /** 建议日常申报价 USD */
  dailyTarget: number | null;
  /** 建议活动申报价 USD */
  activityTarget: number | null;
  /** 近 7 日销量 */
  sales7: number | null;
  /** 需降价额（申报价 - 建议日常价） */
  priceGap: number | null;
}

/** 单店限流汇总 */
export interface LimitedStore {
  /** 店铺显示名 */
  label: string;
  /** 来源数据库文件名 */
  db: string;
  /** 限流商品数 */
  total: number;
  /** 有日常申报价建议的数量 */
  withDaily: number;
  /** 有活动申报价建议的数量 */
  withActivity: number;
  /** 近 7 日有销量的数量 */
  hasSales: number;
  /** 流量状态分布 */
  flowDist: { name: string; value: number }[];
  /** 明细 */
  items: LimitedItem[];
}

/** 重点 SPU 监控命中项 */
export interface FocusHit {
  spu_id: string;
  sku_code: string;
  title: string;
  flow_status: string;
  traffic_state: string;
  declare_price: number | null;
  target_price: number | null;
  activity_price: number | null;
}

/** 重点 SPU 监控结果（按店） */
export interface FocusWatch {
  store: string;
  label: string;
  checkedAt: string;
  limitedTotal: number;
  focusTotal: number;
  /** 重点名单来源（如「动态(近7日, 2026-10-12 08:31:16)」） */
  focusSource?: string;
  hitCount: number;
  hits: FocusHit[];
  safe: string[];
}

/** 一条历史快照（每次监控运行一条，按店拆分） */
export interface TrendSnapshot {
  checked_at: string;
  day: string;
  store: string;
  limited_total: number;
  hit_count: number;
  flow: Record<string, number>;
}

/** 历史趋势 */
export interface LimitedTrend {
  days: string[];
  timeline: TrendSnapshot[];
  recurring: {
    sku_code: string;
    spu_id: string;
    appear: number;
    days: number;
    first_seen: string;
    last_seen: string;
    flow_status: string;
    sales7: number | null;
    declare_price: number | null;
    target_price: number | null;
  }[];
  focusTrack: {
    store: string;
    spu: string;
    /** 货号(款号)，一个 SPU 可能对应多个款号时取第一个 */
    sku_code?: string;
    checks: number;
    hit_times: number;
    first: string;
    last: string;
    current: boolean;
    timeline: { at: string; limited: boolean }[];
  }[];
  resolved: unknown[];
  snapshotCount: number;
  firstCheck: string | null;
  lastCheck: string | null;
}

/** 限流接口响应 */
export interface LimitedData {
  generatedAt: string | null;
  source: string;
  stores: Record<string, LimitedStore>;
  watch: FocusWatch[];
  trend: LimitedTrend;
}