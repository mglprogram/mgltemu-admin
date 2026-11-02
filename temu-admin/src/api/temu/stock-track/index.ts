import type { StockTrackData } from "./types";
import type { LinxingStockData } from "./linxing-types";
import type { PwStockData } from "./pw-types";

/**
 * 库存跟踪接口（九部 + 十部）
 *
 * 与 limited / presale 同源做法：Python 抓取后导出静态 JSON
 * （scripts/goods/fetch_stock_track.py → public/data/stock-track.json），前端直接 fetch。
 */
const DATA_BASE = `${import.meta.env.BASE_URL}data`;

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${DATA_BASE}/${name}`, { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`加载 ${name} 失败：HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const StockTrackAPI = {
  /** 九部 + 十部，6 个款号逐尺码库存（含近 90 天趋势） */
  getStockTrack() {
    return fetchJson<StockTrackData>("stock-track.json");
  },
  /** 领星 OMS 全仓库存明细（两个账号，全量、可查询） */
  getLinxingStock() {
    return fetchJson<LinxingStockData>("linxing-stock.json");
  },
  /** 帕沃尔海外仓 Kansas 仓库存（只有 Kansas 有效） */
  getPwStock() {
    return fetchJson<PwStockData>("pw-stock.json");
  },
};

export default StockTrackAPI;

export * from "./types";
export * from "./linxing-types";
export * from "./pw-types";
