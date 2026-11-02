/**
 * 滞销库存接口（产品-库存 → 滞销库存）
 *
 * 与 limited / presale 同源做法：Python 从飞书读表后导出静态 JSON
 * （scripts/goods/build_stale_stock_data.py → public/data/stale-stock.json），
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

const StaleStockAPI = {
  getStaleStock() {
    return fetchJson<StaleStockData>("stale-stock.json");
  },
};

export default StaleStockAPI;

export * from "./types";
