import type { GoodsData } from "./types";

/**
 * 在售款明细接口
 *
 * 与 limited / flux-boost 同源：构建期由 Python 导出静态 JSON
 * （public/data/goods.json），前端直接 fetch，不走后端。
 */
const DATA_BASE = `${import.meta.env.BASE_URL}data`;

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${DATA_BASE}/${name}`, { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`加载 ${name} 失败：HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const GoodsAPI = {
  /** 在售款明细：两店「在售中」的 SPU / SKC / SKU 全量 */
  getGoodsData() {
    return fetchJson<GoodsData>("goods.json");
  },
};

export default GoodsAPI;

// 重导出类型
export * from "./types";
