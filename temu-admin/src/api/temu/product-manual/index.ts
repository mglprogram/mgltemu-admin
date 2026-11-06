/**
 * 产品手册接口（产品-库存 → 产品手册）
 *
 * 与 stale-stock 同源做法：Python 导出静态 JSON，前端直接 fetch，不走后端。
 * 生成脚本 scripts/goods/build_product_manual.py。
 */
import type { ProductManualData } from "./types";

const DATA_BASE = `${import.meta.env.BASE_URL}data`;

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${DATA_BASE}/${name}`, { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`加载 ${name} 失败：HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const ProductManualAPI = {
  getProductManual() {
    return fetchJson<ProductManualData>("product-manual.json");
  },
};

export default ProductManualAPI;

export * from "./types";
