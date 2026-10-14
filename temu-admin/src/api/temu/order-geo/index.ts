import type { OrderGeoData } from "./types";

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
};

export default OrderGeoAPI;

// 重导出类型
export * from "./types";
