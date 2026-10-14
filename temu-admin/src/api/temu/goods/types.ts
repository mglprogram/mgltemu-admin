/**
 * 在售款明细数据类型
 *
 * 数据由 scripts/goods/run_goods_on_sale.py 抓取（TEMU 商家中心 · 商品列表 · 在售中），
 * 再由 scripts/goods/build_goods_data.py 规范化导出到 public/data/goods.json，
 * 前端静态 fetch，不走后端。
 */

/** 单个 SKU（颜色 + 尺码） */
export interface GoodsSku {
  /** SKU ID */
  skuId: string;
  /** SKU 货号，如 BEL-104504-3BHG-S */
  code: string;
  /** 颜色 / 规格1 */
  color: string;
  /** 尺码 / 规格2 */
  size: string;
  /** 站点申报价（USD） */
  price: number | null;
  currency: string;
  /** 可售库存 */
  stock: number;
  /** 今日销量 */
  todaySales: number | null;
  priceReviewStatus: number | null;
  sitePriceStatus: { siteId: number; status: number }[] | null;
}

/** 一个 SKC（SPU 下的一种颜色组合 / 一个货号） */
export interface GoodsItem {
  /** BEL / BHY */
  store: string;
  /** SPU ID */
  spuId: string;
  /** SKC ID */
  skcId: string;
  /** 商品 ID（平台侧 goodsId） */
  goodsId: string;
  /** 货号（款号），如 BEL-104504-3BHG */
  code: string;
  title: string;
  /** 叶子类目 */
  category: string;
  /** 状态中文，本页固定为「在售中」 */
  status: string;
  /** 状态码（100 = 在售中） */
  statusCode: string;
  /** 主图 */
  mainImage: string;
  /** 累计销量 */
  totalSales: number | null;
  /** 近 7 日销量 */
  sales7: number | null;
  createdAt: number | null;
  skuCount: number;
  /** 该 SKC 全部 SKU 库存合计 */
  stock: number;
  priceMin: number | null;
  priceMax: number | null;
  skus: GoodsSku[];
}

export interface GoodsCounts {
  /** 在售中 SKC 数（= 款数） */
  onSaleSkc: number;
  /** 在售中 SPU 数 */
  spu: number;
  /** 在售中 SKU 数 */
  sku: number;
  /** 库存合计 */
  stock: number;
  /** 叶子类目数 */
  category: number;
  /** 平均申报价 */
  avgPrice: number | null;
}

/** 低库存行：**一个款的一个尺码**（SKU 粒度），含「能不能加库存」判定 */
export interface LowStockRow {
  /** BEL / BHY */
  store: string;
  /** 货号(款号) */
  code: string;
  /** SKU 货号 */
  skuCode: string;
  spuId: string;
  skcId: string;
  skuId: string;
  title: string;
  category: string;
  color: string;
  size: string;
  /** TEMU 当前可售库存（<= threshold） */
  stock: number;
  price: number | null;

  // ---- 加库存判定（enrich_restock.py 写入；未跑增强时这些字段缺席）----
  /** 结论：可加库存 / 可开预售 / 库存够卖(不用加) / 仓里有货(未定量) / 待到货 / 待到货确认 / 需补货 / 未匹配 */
  verdict?: string;
  /** 仓里现货够不够加（物理条件） */
  canRestock?: boolean;
  /** 判定说明（人话） */
  reason?: string;
  /** 变体换算系数（看板单位 ÷ TEMU 单位） */
  factor?: number;
  /** 匹配到的海外仓看板键 */
  boardKey?: string;
  /** 仓内现货来源：看板·代发库存 / 领星总表·代发库存 */
  stockSource?: string;
  /** 仓内现货（看板原始件数） */
  whStockRaw?: number | null;
  /** 仓内现货（折 TEMU 单位） */
  whStock?: number | null;
  /** 仓内现货按仓/货源拆分（悬浮时看是帕沃尔还是恒信/领星的货） */
  whByWarehouse?: {
    仓库?: string;
    货源?: string;
    代发库存?: number | null;
    代发途中?: number | null;
    代发库存Temu?: number | null;
    来源?: string;
    看板键?: string;
    url?: string;
  }[];
  /** 变体兜底说明（如「货号末尾数字已忽略（3BHL1 → 102105-3BHL-L）」） */
  variantNote?: string;
  /** 日均（TEMU 单位）；优先看板「近7天日均」 */
  dailyUse?: number | null;
  /** 日均来源：看板·近7天日均 / 领星覆盖甘特·日耗 */
  dailySource?: string;
  /** 看板同款近7天日均（对账用） */
  boardDaily?: number | null;
  /** 领星覆盖甘特日耗（对账用） */
  lingxingDaily?: number | null;
  /** 目标天数需求量 */
  needUnits?: number | null;
  /** 建议增加量（上限 = 仓内现货；= 0 表示现有库存已够目标天数） */
  suggestAdd?: number | null;
  /** 加后 TEMU 可售 */
  afterStock?: number | null;
  /** 现有 + 仓内现货全加进去能卖多少天（按日均） */
  coverDays?: number | null;
  /** 距目标天数还差多少天 */
  shortDays?: number | null;
  /** 在途件数（原始） */
  transitRaw?: number | null;
  /** 在途件数来源 */
  transitSource?: string;
  /** 已确认会到的在途（飞书到货计划里带到货日的未入仓批次） */
  inboundConfirmed?: number | null;
  /** 在途但没有到货日（飞书到货计划里没有批次）⇒ 现在加不了 */
  transitNoEta?: number | null;
  /** 现在能加的上限（= 仓内现货） */
  canAddMax?: number | null;
  /** 是否建议开 7 天预售（有到货日且在预售窗口内） */
  preSale?: boolean;
  /** 距到货日还有几天 */
  daysToArrival?: number | null;
  /** 最近到货日 */
  arrivalDate?: string;
  /** 到货日来源（领星到货计划甘特 / 库存覆盖甘特） */
  arrivalSource?: string;
  /** 未入仓批次 */
  arrivalBatches?: { 批次?: string; 件数?: number; 到货日?: string; 渠道?: string; 入库单号?: string }[];
  /** 领星覆盖甘特：可用库存 / 空窗天数 / 缺口件数 / 风险 */
  lingxingAvailable?: number | null;
  gapDays?: number | null;
  shortageUnits?: number | null;
  grade?: string;
  risk?: string;
  /** 看板里最接近的键（未匹配时给人工确认用） */
  nearKeys?: string[];
}

/** 低库存按款分组：一个款一行，展开看该款**全部尺码**（含没到阈值的） */
export interface LowStockGroup {
  storeKey: string;
  storeLabel: string;
  /** 货号(款号) */
  code: string;
  /** 该款合并的 SKC 集合（构成行唯一键的一部分：同款号可能有两个 SKC） */
  skcIds?: string[];
  spuId: string;
  skcId: string;
  title: string;
  category: string;
  priceMin: number | null;
  priceMax: number | null;
  /** 该款在售 SKU（尺码）总数 */
  skuCount: number;
  /** 其中库存 ≤ 阈值的尺码数 */
  lowCount: number;
  /** 该款库存合计 */
  totalStock: number;
  /** 低库存的那些尺码（库存合计） */
  lowStockSum: number;
  /** 该款全部尺码明细（含库存充足的，便于看出能否换码/调拨） */
  skus: (GoodsSku & {
    low: boolean;
    verdict?: string;
    reason?: string;
    suggestAdd?: number | null;
    whStockRaw?: number | null;
    whStock?: number | null;
    dailyUse?: number | null;
    dailySource?: string;
    boardDaily?: number | null;
    lingxingDaily?: number | null;
    needUnits?: number | null;
    afterStock?: number | null;
    coverDays?: number | null;
    shortDays?: number | null;
    transitRaw?: number | null;
    transitSource?: string;
    arrivalDate?: string;
    arrivalSource?: string;
    variantNote?: string;
    whByWarehouse?: LowStockRow["whByWarehouse"];
    boardKey?: string;
    stockSource?: string;
    factor?: number;
    nearKeys?: string[];
  })[];
  /** 该款可加库存的尺码数 */
  restockCount: number;
  /** 该款建议加量合计 */
  suggestAddTotal: number;
  /** 该款最近到货日 */
  arrivalDate: string;
  /** 该款各尺码的未入仓批次（带 size，供悬浮展示） */
  arrivalBatches?: { size?: string; 批次?: string; 件数?: number; 到货日?: string; 渠道?: string; 入库单号?: string }[];
  /** 结论分布（可加库存 / 待到货 / …） */
  verdicts: Record<string, number>;
}

/** 加库存判定汇总 */
export interface RestockSummary {
  targetDays?: number;
  /** 结论分布：可加库存 / 仓里现货不足 / 待到货 / 需补货 / 未匹配 … */
  verdictCounts: Record<string, number>;
  /** 建议增加量合计（仅「可加库存」） */
  suggestAddTotal: number;
  /** 可以加库存的款·尺码数（verdictCounts 里也可读到） */
  canRestockSkus?: string[];
  /** 作废链接（货号含 ERROR）的条目，建议清理 */
  voidRows?: string[];
  /** 建议加得最多的前 N 条 */
  top: {
    store: string;
    code: string;
    size?: string;
    color?: string;
    stock: number | null;
    whStockRaw?: number | null;
    whStock?: number | null;
    dailyUse?: number | null;
    dailySource?: string;
    suggestAdd?: number | null;
    arrivalDate?: string;
  }[];
  /** 待到货的前 N 条 */
  waitingTop: {
    store: string;
    code: string;
    size?: string;
    transitRaw?: number | null;
    arrivalDate?: string;
  }[];
  /** 可开预售的建议（有到货日且在预售窗口内） */
  preSaleRows?: {
    store: string;
    code: string;
    size?: string;
    stock?: number | null;
    arrivalDate?: string;
    daysToArrival?: number | null;
    inboundConfirmed?: number | null;
  }[];
  sources?: {
    看板?: {
      builtAt?: string | null;
      fetchedAt?: string | null;
      url?: string | null;
      /** 是否实时拉的（否则用了缓存） */
      live?: boolean;
    };
    飞书?: { fetchedAt?: string | null; empty?: boolean; url?: string; base?: string };
  };
  rule?: string;
}

export interface LowStockSummary {
  /** 低库存 SKU（款×尺码）数 */
  sku: number;
  /** 涉及多少个款 */
  style: number;
  /** 其中 0 库存的个数 */
  zero: number;
  /** 分档：0 / 1-4 / 5-9 */
  buckets?: { name: string; value: number }[];
  stores: string[];
  /** 最低库存值 */
  minStock?: number | null;
  /** 最紧急的前 N 条 */
  top: LowStockRow[];
}

export interface LowStockBlock {
  /** 阈值（当前 10，口径是「<= 阈值」） */
  threshold: number;
  rows: LowStockRow[];
  summary: LowStockSummary;
  /** 加库存判定汇总（enrich_restock.py 写入） */
  restock?: RestockSummary;
  byStore?: Record<string, LowStockSummary>;
}

export interface GoodsStore {
  label: string;
  counts: GoodsCounts;
  /** 抓取端各状态数量（用于对账） */
  statusCounts: Record<string, number>;
  categories: { name: string; value: number }[];
  /** 该店低库存（<= threshold）明细 */
  lowStock: LowStockBlock;
  items: GoodsItem[];
}

export interface GoodsData {
  generatedAt: string | null;
  /** 可加库存判定的生成时间 */
  restockGeneratedAt?: string | null;
  source: string;
  /** 状态中文名，本页为「在售中」 */
  statusLabel: string;
  statusCode: string;
  /** 低库存阈值（首页卡片与明细页共用同一口径） */
  lowStockThreshold: number;
  /** 两店合计的低库存总览 */
  lowStock: LowStockBlock;
  stores: Record<string, GoodsStore>;
  /**
   * 各店「近7日销量」的来源说明（形如 `{BEL: "销量分析页（2026-10-12 08:31）"}`）。
   *
   * 商品列表接口与官方导出都**不返回销量**，sales7 是另外 join 进来的，
   * 所以它的时间可能比商品数据旧 —— 前端可据此提示口径。
   */
  salesSource?: Record<string, string>;
  /** 销量字段来源的一句话说明 */
  salesSourceNote?: string;
  totals: {
    onSaleSkc: number;
    spu: number;
    sku: number;
    stock: number;
  };
}
