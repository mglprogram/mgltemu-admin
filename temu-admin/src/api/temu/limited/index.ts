import type { LimitedData } from "./types";

/**
 * 限流数据接口
 *
 * 与 flux-boost 同源：构建期由 Python 导出静态 JSON（public/data/limited.json），
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

const LimitedAPI = {
  /**
   * 限流总览：两店明细 + 重点 SPU 告警 + 趋势 + 反复限流排行
   */
  getLimitedData() {
    return fetchJson<LimitedData>("limited.json");
  },
};

export default LimitedAPI;

// 重导出类型
export * from "./types";