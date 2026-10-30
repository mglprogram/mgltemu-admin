import type { ShipmentTrackData } from "./types";

/**
 * 货件跟踪接口
 *
 * 与其它 TEMU 模块同源：Python 抓取后导出静态 JSON
 * （scripts/goods/fetch_shipment_track.py → public/data/shipment-track.json），前端直接 fetch。
 */
const DATA_BASE = `${import.meta.env.BASE_URL}data`;

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${DATA_BASE}/${name}`, { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`加载 ${name} 失败：HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const ShipmentTrackAPI = {
  /** 货件跟踪：追踪表全量 + 恒信轨迹 + 领星 OMS 入库明细 */
  getShipmentTrack() {
    return fetchJson<ShipmentTrackData>("shipment-track.json");
  },
};

export default ShipmentTrackAPI;

export * from "./types";
