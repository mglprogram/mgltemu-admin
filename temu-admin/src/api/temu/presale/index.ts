import type { PresaleData } from "./types";

/**
 * 预售建议数据接口
 *
 * 与 limited / flux-boost 同源：构建期由 Python 导出静态 JSON
 * （scripts/bel_goods/build_presale_data.py → public/data/presale.json），前端直接 fetch。
 */
const DATA_BASE = `${import.meta.env.BASE_URL}data`;

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${DATA_BASE}/${name}`, { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`加载 ${name} 失败：HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const PresaleAPI = {
  getPresaleData() {
    return fetchJson<PresaleData>("presale.json");
  },
};

export default PresaleAPI;

export * from "./types";
