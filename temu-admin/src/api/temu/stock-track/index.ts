import type { StockTrackData } from "./types";
import type { LinxingStockData } from "./linxing-types";
import type { PwStockData } from "./pw-types";

/** 领星在途入库单（scripts/goods/fetch_linxing_inbound.py → linxing-inbound.json） */
export interface LinxingInboundOrder {
  /** 入库单号，如 IB106261102RU */
  no: string;
  /** 还没到的件数 */
  qty: number;
  /** 这张单总共下了多少 */
  inbound: number;
  /** 预计到货日 */
  expected: string;
  /** 状态中文（待到货 / 收货中 …） */
  status: string;
  wh: string;
  /** 预计到货日已过（可能延误） */
  late: boolean;
}

export interface LinxingInboundData {
  generatedAt: string;
  /** 扫过的入库单张数 */
  orders: number;
  /** 还没到齐的单数 */
  open: number;
  /** 有待入库的 SKU 数 */
  skus: number;
  /** {款|套系|尺码: 待入库合计} */
  total: Record<string, number>;
  /** {款|套系|尺码: [单号…]} */
  map: Record<string, LinxingInboundOrder[]>;
  rule: { note: string; source: string };
}

/**
 * 库存跟踪接口（九部 + 十部）
 *
 * 与 limited / presale 同源做法：Python 抓取后导出静态 JSON
 * （scripts/goods/fetch_stock_track.py → public/data/stock-track.json），前端直接 fetch。
 */
const DATA_BASE = `${import.meta.env.BASE_URL}data`;

async function fetchJson<T>(name: string): Promise<T> {
  // 库存 JSON 每天更新一次，但用户多数时候是从收藏夹/刷新进来：
  // 给个 5 分钟内的缓存，既不会看到过期数（`no-cache` 式的强制校验交给 CDN），
  // 又能让来回切页签时不重复下载 / 重复解析 100~600 KB 的 JSON。
  const res = await fetch(`${DATA_BASE}/${name}`, { cache: "default" });
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
  /** 帕沃尔海外仓 加州3 + Kansas 库存（代发库存，现货可发） */
  getPwStock() {
    return fetchJson<PwStockData>("pw-stock.json");
  },
  /** 领星入库单（在途）：{款|套系|尺码} → 还没到齐的单号与预计到货日 */
  getLinxingInbound() {
    return fetchJson<LinxingInboundData>("linxing-inbound.json");
  },
  /** 帕沃尔代发库存明细的参考单号：Fnsku → [{ref, qty, boxes…}] */
  getPwRefs() {
    return fetchJson<PwRefsData>("pw-refs.json");
  },
};

/** 帕沃尔代发库存明细里的参考单号（scripts/goods/fetch_pw_refs.py → pw-refs.json） */
export interface PwRefItem {
  /** 参考单号，如 144503047（用户拿去查这票货的就是它） */
  ref: string;
  /**
   * 是否真的是业务参考单号（14450xxxx 这类 8~12 位）。
   * 帕沃尔 Kansas 仓部分行的「参考单号」列其实是 20 位快递/监管号，这里为 false。
   */
  isRef?: boolean;
  /** 这个参考单号下的总件数（多箱合并） */
  qty: number;
  /** 剩余库存 */
  left: number;
  /** 仓库 */
  wh: string;
  /** 入库时间 */
  in: string;
  /** 箱号（如 144503047-1_1） */
  boxes: string[];
}

export interface PwRefsData {
  generatedAt: string;
  /** 有参考单号的 SKU 数 */
  skus: number;
  /** 参考单号条数 */
  rows: number;
  /** Fnsku → 参考单号（前端按 pw-stock 的 fnsku 查） */
  map: Record<string, PwRefItem[]>;
  /** 仓库 → 帕沃尔仓 ID（排障用） */
  byWh: Record<string, string>;
  rule: { note: string; source: string };
}

export default StockTrackAPI;

export * from "./types";
export * from "./linxing-types";
export * from "./pw-types";
