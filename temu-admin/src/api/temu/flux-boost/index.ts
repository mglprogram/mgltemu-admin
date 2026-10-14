import type { FluxBoostData } from "./types";

/**
 * TEMU 运营数据接口
 *
 * 数据是构建期由 scripts/bel_goods/build_api_data.py 导出的静态 JSON
 * （public/data/*.json），Vite 构建时原样拷进 dist，Cloudflare Pages
 * 直接静态托管 —— 所以这里用 fetch 直接取静态资源，不走后端接口壳。
 *
 * 若日后接了真实后端，把 BASE 换成 API 前缀、把 fetch 换成 request 即可，
 * 页面层的调用方式不用动。
 */
const DATA_BASE = `${import.meta.env.BASE_URL}data`;

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${DATA_BASE}/${name}`, { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`加载 ${name} 失败：HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const FluxBoostAPI = {
  /**
   * 已开启流量加速的 SPU 尺码申报价对照
   */
  getFluxBoostData() {
    return fetchJson<FluxBoostData>("flux-boost.json");
  },
};

export default FluxBoostAPI;

// 重导出类型
export * from "./types";