/**
 * 流量加速（流量加速器）数据类型
 *
 * 数据由 scripts/bel_goods/build_api_data.py 合并
 * data/flux/<store>_<section>.json（fetch_flux_all.py 抓取）
 * 导出到 public/data/flux-boost.json，前端静态 fetch。
 *
 * 结构：两店 × 两板块
 *   - accelerating 已开启：有尺码级申报价对照（原日常申报价 → 调整后申报价）
 *   - pending      待开启：无尺码价，只有流量/转化指标与可选优化方式
 */

/** 单个 SKU 尺码的价格明细（仅已开启板块） */
export interface FluxBoostSku {
  /** SKC */
  skc: string | null;
  /** 颜色 */
  color: string | null;
  /** 尺码 */
  size: string | null;
  /** 原日常申报价 USD */
  origPrice: number | null;
  /** 调整后申报价 USD */
  afterPrice: number | null;
}

/** 流量/转化指标（仅待开启板块有值） */
export interface FluxStats {
  exposure?: number | null;
  clicks?: number | null;
  visitors?: number | null;
  views?: number | null;
  addCart?: number | null;
  favorites?: number | null;
  orders?: number | null;
  pieces?: number | null;
  buyers?: number | null;
  detailBuyers?: number | null;
  detailConv?: number | null;
  exposureOrderConv?: number | null;
  ctr?: number | null;
  clickOrderConv?: number | null;
  searchExposure?: number | null;
  searchClicks?: number | null;
  searchOrders?: number | null;
  searchPieces?: number | null;
  recExposure?: number | null;
  recClicks?: number | null;
  recOrders?: number | null;
  recPieces?: number | null;
}

/** 一个 SPU（两个板块共用，字段按板块可空） */
export interface FluxBoostItem {
  /** SPU ID */
  spuId: string;
  /** 商品标题 */
  title: string | null;
  /** 叶子类目 */
  category?: string | null;
  /** 经营站点 */
  site?: string | null;
  /** 首次加入站点日期 */
  firstListed?: string | null;
  /** 流量加速状态（已开启板块：「进行中」等） */
  state?: string | null;
  /** 加速开始时间 */
  periodFrom?: string | null;
  /** 加速到期时间 */
  periodTo?: string | null;
  /** 行内标签（待开启板块，如「商品流量待关注」） */
  tags?: string | null;
  /** 尺码数 */
  sizeCount: number;
  /** 原日常申报价 USD（已开启） */
  origPrice?: number | null;
  /** 调整后申报价 USD（已开启） */
  afterPrice?: number | null;
  /** 降幅 USD（已开启） */
  drop?: number | null;
  /** 降幅百分比（已开启） */
  dropPct?: number | null;
  /** 尺码明细（已开启） */
  skus: FluxBoostSku[];
  /** 流量/转化指标（待开启） */
  stats?: FluxStats;
  /** 可选优化方式文案（待开启） */
  optimize?: string | null;
  /** 是否有「查看详情」入口（待开启） */
  hasViewDetail?: boolean;
  /**
   * 页面上存在的写操作入口（如「立即开启」「报名流量加速器」）
   *
   * 仅作**提示展示**用，前端不提供任何触发按钮 —— 抓取全程只读。
   */
  writeActions?: string[];
}

/** 一个板块（已开启 / 待开启） */
export interface FluxSection {
  /** 板块显示名 */
  label: string;
  /** SPU 数 */
  total: number;
  /** 该板块抓取时间 */
  scrapedAt?: string | null;
  /** 明细 */
  list: FluxBoostItem[];
}

/** 一个店铺 */
export interface FluxStore {
  label: string;
  sections: {
    /** 已开启且加速生效（有尺码级申报价） */
    accelerating: FluxSection;
    /** 已开启但动力不足（无加速明细，页面上只有「查看详情」） */
    weak: FluxSection;
    /** 待开启（有资格未开启） */
    pending: FluxSection;
  };
}

/** 流量加速接口响应 */
export interface FluxBoostData {
  /** 合并生成时间 */
  generatedAt: string | null;
  /** 数据来源说明 */
  source: string;
  /** SPU 总数（两店两板块合计） */
  total: number;
  /** 按店铺分组 */
  stores: Record<string, FluxStore>;
}
