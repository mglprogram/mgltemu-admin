import type { NineInvData } from "./types";

/**
 * 九部库存跟踪接口
 *
 * 与 limited / flux-boost / presale 同源做法：Python 抓取后导出静态 JSON
 * （scripts/goods/fetch_nine_inv.py → public/data/nine-inv.json），前端直接 fetch。
 */
const DATA_BASE = `${import.meta.env.BASE_URL}data`;

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${DATA_BASE}/${name}`, { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`加载 ${name} 失败：HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const NineAPI = {
  /** 九部 6 个款号逐尺码库存（含近 90 天趋势） */
  getNineInv() {
    return fetchJson<NineInvData>("nine-inv.json");
  },
};

export default NineAPI;

export * from "./types";
