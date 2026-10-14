/**
 * 销量-库存（原「预售建议」）类型定义
 *
 * 数据来自 public/data/presale.json（scripts/bel_goods/build_presale_data.py 导出）：
 *   TEMU 销量分析（已售罄/即将售罄 + 每个尺码的预计未来7日销量）
 *   × 海外仓看板「代发库存」（在仓现货）
 *   × 飞书领星（在途批次 + 到货日）
 */

/** 单个 SKU（尺码）行 */
export interface PresaleRow {
  skuCode: string;
  skuId: string;
  size: string;
  title: string;
  /** TEMU 售罄状态：已售罄 / 即将售罄 / 低库存预警 */
  status: string;
  /** 预计未来 7 日总销量（TEMU 预测，件） */
  forecast7: number | null;
  /** 今日销量（该尺码今天卖了多少，取自销量分析页「今日SKU销量」） */
  todaySales?: number | null;
  /** 今日销量口径说明 */
  todaySource?: string;
  /** 预计未来 14 日总销量 */
  forecast14: number | null;
  /** TEMU 现有销售可用库存 */
  available: number | null;
  /** TEMU 可售够卖几天 = 可售 ÷（预计未来7日 ÷ 7）—— 「加库存」判据看的是它（平台只认申报值，仓库再多货没申报也卖不出去） */
  availDays: number | null;
  /** 对应的看板小款号 */
  probeSku: string;
  /** 换算系数：看板/领星单位 ÷ TEMU 单位（10SetA = 2 × 5SetA） */
  factor: number;
  /** 看板代发库存**原始件数**（5Set 单位）= 已经在海外仓的现货 */
  probeStockRaw: number | null;
  /** 看板代发库存折成 TEMU 单位（= 原始 ÷ factor） */
  probeStock: number | null;
  /** 看板代发途中原始件数（仅供参考，不算现货） */
  probeTransitRaw: number | null;
  /** 日均需求 = 预计未来7日 / 7（TEMU 单位） */
  dailyNeed: number | null;
  /** 发货模式：卖家自发货 / 合作仓对接发货（后者后台没有「立即加库存」入口） */
  shipMode?: string;
  /** 加库存建议（只算不写）：increase 建议增加量，target 目标可售，warehouse 建议填哪个发货仓 */
  topup?: {
    increase: number;
    target: number;
    days: number;
    warehouse: string;
    skip: string;
  };
  /** 现货可售天数 = 折后现货 / 日均需求 */
  stockDays: number | null;
  /** 领星「型号-变体-尺码」键 */
  lingxingKey: string;
  /** 领星可发库存（在仓，5Set 单位） */
  lingxingAvailable: number | null;
  /** 领星在途件数（5Set 单位） */
  lingxingTransit: number | null;
  lingxingGapDays: number | null;
  lingxingShortage: number | null;
  lingxingNextArrival: string;
  /** 领星库存档位 / 日耗 / 风险 / 覆盖段类型 */
  lingxingStockGrade: string;
  lingxingDailyUse: number | null;
  lingxingRisk: string;
  lingxingSegType: string;
  /** 在途**原始件数**（5Set 单位，取自领星） */
  transitRaw: number | null;
  /** 在途件数来源（领星库存覆盖甘特 / 库存总表 / 到货计划票级） */
  transitSource: string;
  /** 在途折成 TEMU 单位 */
  transit: number | null;
  /** 最近到货日 YYYY-MM-DD */
  arrivalDate: string;
  /** 到货日来源（到货计划甘特 / 库存覆盖甘特） */
  arrivalSource: string;
  /** 这个到货日在票级里覆盖了多少件（说明它只解释了多少在途） */
  arrivalTicketQty: number | null;
  /** 到货日距今天数（负 = 逾期未入仓） */
  arrivalDays: number | null;
  arrivalBatches: { 到货日?: string; 件数?: number; 尺码件数?: number; 批次?: string; 渠道?: string }[];
  /** 结论：可开预售 / 先加库存 / 要补货 / 不用开 / 跳过 */
  verdict: string;
  /** 可以开预售（现货撑不住 + 在途有货且有到货日） */
  canPresale: boolean;
  /** 可以重新加库存（仓里有现货，而 TEMU 已断货 / 快断货） */
  canRestock: boolean;
  /** 建议动作标签：['开预售','加库存'] */
  actions: string[];
  reason: string;
  listedAt: string;
  listedDays: number | null;
  /** 所属店铺 —— 只在「全部」页签里由前端补上，导出数据里没有 */
  store?: string;
}

export interface PresaleStore {
  store: string;
  window: string;
  source: string;
  live: boolean;
  rowCount: number;
  presaleCount: number;
  restockCount: number;
  rows: PresaleRow[];
}

export interface PresaleData {
  generatedAt: string;
  rule: { text: string; restock: string; threshold: number; forecast: string; note: string };
  sources: {
    看板: { builtAt: string; rows: number; url: string };
    飞书: { base: string; fetchedAt: string; counts: Record<string, number>; empty: boolean };
  };
  stores: PresaleStore[];
}
