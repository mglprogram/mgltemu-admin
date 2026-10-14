/**
 * 订单地理分布（美国州/市）数据类型
 *
 * 数据来源：帕沃尔海外仓 powerwarehouse.net → 发货列表(deliver.php)
 *   · 订单清单：deliver.php?act=query_order_list（两店 BEL/BHY）
 *   · 州-市：deliver.php?act=order_info&id=<出库单id> 的「交货信息 · 收件地址」
 * 构建期导出为静态 JSON（public/data/order-geo.json），前端直接 fetch，不走后端。
 */

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
  /** 该州订单最多的城市（Top 5） */
  cities: { name: string; orders: number }[];
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
}

export interface OrderGeoData {
  /** 生成时间（本地时间字符串） */
  generatedAt: string;
  /** 统计区间 */
  range: { start: string; end: string };
  /** 数据来源说明 */
  source: string;
  /** 全部（两店合计） */
  totals: {
    orders: number;
    geoOrders: number;
    stateCount: number;
    cityCount: number;
    qty: number;
  };
  /** 合计口径 */
  states: GeoStateItem[];
  topCities: GeoCityItem[];
  months: GeoMonthItem[];
  /** 分店口径：BEL / BHY */
  stores: Record<string, GeoStoreBlock>;
}
