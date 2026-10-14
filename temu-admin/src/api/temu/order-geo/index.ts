import type { OrderGeoData, GeoProductIndex } from "./types";

/**
 * 订单地理分布接口
 *
 * 与其它 TEMU 模块同源：构建期由 Python 导出静态 JSON（public/data/order-geo.json），
 * 前端直接 fetch，不走后端。
 */
const DATA_BASE = `${import.meta.env.BASE_URL}data`;

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${DATA_BASE}/${name}`, { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`加载 ${name} 失败：HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const OrderGeoAPI = {
  /** 订单分布：美国各州/城市出单量 + 月度趋势 */
  getOrderGeoData() {
    return fetchJson<OrderGeoData>("order-geo.json");
  },
  /**
   * 产品查询索引：全部 220 个款的完整州分布 / 月度 / 城市 / Fnsku（~450KB）。
   *
   * ⚠ 只有「订单分布」页会用它，**数据大屏不要调**（大屏只需要 order-geo.json 里
   *   的 Top60）；所以单独一个文件、由页面按需加载，避免拖慢大屏。
   */
  getProductIndex() {
    return fetchJson<GeoProductIndex>("order-geo-products.json");
  },
};

export default OrderGeoAPI;

// 重导出类型
export * from "./types";
