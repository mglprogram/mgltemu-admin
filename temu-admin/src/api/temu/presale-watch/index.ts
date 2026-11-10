import type { PresaleWatchData } from "./types";

/**
 * 修改库存 · 预售 面板数据源
 *
 * public/data/presale-watch.json —— 由 scripts/goods/build_presale_watch.py 生成：
 *   读开预售台账 data/presale-ledger.json（预售中 / 已过预售），
 *   再挂上「在售款明细」的 SPU/标题 与「库存跟踪」的 7 天日均。
 * 静态托管，前端直接 fetch，不走后端。
 */
const DATA_BASE = `${import.meta.env.BASE_URL}data`;

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${DATA_BASE}/${name}`, { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`加载 ${name} 失败：HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const PresaleWatchAPI = {
  getPresaleWatchData() {
    return fetchJson<PresaleWatchData>("presale-watch.json");
  },
};

export default PresaleWatchAPI;

export * from "./types";
