/**
 * 订单地理分布（美国州/市）数据类型
 *
 * 数据来源：帕沃尔海外仓 powerwarehouse.net → 发货列表(deliver.php)
 *   · 订单清单：deliver.php?act=query_order_list（两店 BEL/BHY）
 *   · 州-市：deliver.php?act=order_info&id=<出库单id> 的「交货信息 · 收件地址」
 * 构建期导出为静态 JSON（public/data/order-geo.json），前端直接 fetch，不走后端。
 */

/** 一个州里某个款（产品）的单量 */
export interface GeoProductRef {
  /** 款号（产品名去掉末尾尺码），如 BH-102105-3BHL */
  name: string;
  orders: number;
  /** 该款在本州全部有产品订单中的占比 0~1 */
  share: number;
}

/** 单个州的聚合 */
export interface GeoStateItem {
  /** 州全名，如 Texas（与 ECharts 美国地图 name 对齐） */
  name: string;
  /** 州缩写，如 TX */
  code: string;
  /** 订单数（一个出库单算 1 单） */
  orders: number;
  /** 件数（数量列求和，可能为 0 = 没解析到） */
  qty: number;
  /** 占比 0~1 */
  share: number;
  /** 该州订单最多的城市（Top 8） */
  cities: { name: string; orders: number }[];
  /** 该州最畅销的款（Top 5）—— 地图 tooltip / 明细表直接用 */
  topProducts?: GeoProductRef[];
}

/** 款维度榜单的一项：这款卖了多少、卖到哪些州、是不是「专供某州」 */
export interface GeoProductItem {
  /** 款号 */
  name: string;
  orders: number;
  qty: number;
  /** 占全部订单的比例 0~1 */
  share: number;
  /** 该款自己的州分布 Top6 */
  states: { name: string; code: string; orders: number; share: number }[];
  /** 头号州 */
  topState: string;
  topStateCode: string;
  /** 头号州占该款的比例 0~1 */
  topStateShare: number;
  /**
   * 地域集中度 = 头号州占该款比例 ÷ 大盘该州占比。
   * x1 ≈ 与大盘同构；x≥3 基本就是「专供某州」的款。
   */
  lift: number;
  /** 分店单量 */
  shops?: Record<string, number>;
}

/** 城市条目 */
export interface GeoCityItem {
  /** 城市名（帕沃尔对部分城市做了脱敏，形如 "Po****"） */
  name: string;
  /** 州全名 */
  state: string;
  /** 州缩写 */
  code: string;
  orders: number;
}

/** 月度趋势的一个点 */
export interface GeoMonthItem {
  /** YYYY-MM */
  month: string;
  orders: number;
  /** 该月 Top 州（最多 5 个） */
  topStates: { name: string; orders: number }[];
}

/**
 * 「产品查询」用的**全量**款明细（order-geo-products.json，按需加载）。
 *
 * 与 `GeoProductItem`（order-geo.json 里的 Top60，给地图用）的区别：
 *   · 这里是**全部 220 个款**；
 *   · 每个款的州分布是**完整**的（不是 Top6）；
 *   · 多带 月度 与 城市 Top8 与 Fnsku（便于按 Fnsku 搜）。
 */
export interface GeoProductDetail {
  /** 款号（产品名去掉末尾尺码） */
  name: string;
  /** 数字短码，如 102105（与 utils/sale-code.ts 同一套规则） */
  code: string;
  orders: number;
  qty: number;
  /** 占大盘订单比例 0~1 */
  share: number;
  topState: string;
  topStateCode: string;
  topStateShare: number;
  /** 地域集中度（头号州占比 ÷ 大盘该州占比） */
  lift: number;
  /** 分店单量 */
  shops: Record<string, number>;
  /** Fnsku 列表（帕沃尔后台按 Fnsku 找货） */
  fnskus: string[];
  /** 完整州分布（按单量降序） */
  states: { name: string; code: string; orders: number; share: number }[];
  /** 月度趋势 */
  months: { month: string; orders: number }[];
  /** 城市 Top8 */
  cities: { name: string; state: string; code: string; orders: number }[];
}

export interface GeoProductIndex {
  generatedAt: string;
  range: { start: string; end: string };
  productCount: number;
  products: GeoProductDetail[];
}

/** 单店口径 */
export interface GeoStoreBlock {
  shopId: string;
  /** 出库单总数 */
  orders: number;
  /** 成功解析出州的数量 */
  geoOrders: number;
  states: GeoStateItem[];
  topCities: GeoCityItem[];
  months: GeoMonthItem[];
  /** 款维度榜单（Top 60） */
  topProducts?: GeoProductItem[];
}

export interface OrderGeoData {
  /** 生成时间（本地时间字符串） */
  generatedAt: string;
  /** 统计区间 */
  range: { start: string; end: string };
  /** 数据来源说明 */
  source: string;
  /**
   * 合并口径的一句话补充（2026-10-15 起）：
   * 帕沃尔覆盖 2026-01-01~10-14，之后并入领星 OMS(恒信仓) 的 BEL/BHY 出库单。
   */
  sourceNote?: string;
  /** 本次从领星 OMS 并入的订单数 */
  omsOrders?: number;
  /** 领星 OMS 里其他店铺（TikTok/SHEIN 等）的单量 —— **未计入本页** */
  omsOtherStores?: number;
  /** 全部（两店合计） */
  totals: {
    orders: number;
    geoOrders: number;
    stateCount: number;
    cityCount: number;
    qty: number;
    /** 款数量 */
    styleCount?: number;
  };
  /** 合计口径 */
  states: GeoStateItem[];
  topCities: GeoCityItem[];
  months: GeoMonthItem[];
  /** 款维度榜单（Top 60）：每个款卖到哪些州、地域集中度 */
  topProducts?: GeoProductItem[];
  /** 分店口径：BEL / BHY */
  stores: Record<string, GeoStoreBlock>;
}
