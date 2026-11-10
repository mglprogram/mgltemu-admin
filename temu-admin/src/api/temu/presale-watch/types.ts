/**
 * 修改库存 · 预售（public/data/presale-watch.json）
 *
 * 由 scripts/goods/build_presale_watch.py 生成，数据来自开预售台账
 * data/presale-ledger.json + 在售款明细 goods.json + 库存跟踪 stock-track.json。
 *
 * 用户 2026-11-09 定的口径：
 *   · 预售中   —— 正在预售的款号-尺码
 *   · 已过预售 —— 开预售满 7 天的记录（平台每 SKU 7 天内只允许开启一次）
 *   · 已过预售里满 7 天、可重新开预售的那条 → clearable=true（可以从台账清掉）
 */
export type PresaleStatus = "预售中" | "已过预售";

export interface PresaleWatchItem {
  platform: string;
  store: string;
  code: string;
  size: string;
  spu: string | null;
  title: string;
  category: string;
  /** 在途预售库存 */
  qty: number | null;
  /** 在途库存补货日期 */
  date: string;
  openedAt: string;
  ageDays: number | null;
  status: PresaleStatus;
  /** 已过预售 且 满 7 天可重开 ⇒ 可清除 */
  clearable: boolean;
  /** TEMU 当前库存（在售款明细） */
  temuStock: number | null;
  /** 7 天日均 */
  daily: number;
  /** 建议在途量（日均×7，无日均 20） */
  suggestQty: number;
}

export interface PresaleWatchData {
  generatedAt: string;
  presaleDays: number;
  rule: { note: string; openedFrom: string };
  counts: Record<string, number>;
  platforms: string[];
  stores: string[];
  items: PresaleWatchItem[];
}
