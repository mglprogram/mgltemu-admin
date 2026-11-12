/**
 * 修改库存 · 预售 / 清零（public/data/presale-watch.json）
 *
 * 由 scripts/goods/build_presale_watch.py 生成：
 *   · TEMU  → data/presale-ledger.json（开预售台账）+ 在售款明细 goods.json + 库存跟踪
 *   · SHEIN → data/shein-clear-ledger.json（清零台账，run_shein_stock.py --apply 写入）
 *
 * 用户 2026-11-09 定的 TEMU 口径：
 *   · 预售中   —— 正在预售的款号-尺码
 *   · 已过预售 —— 开预售满 7 天的记录（平台每 SKU 7 天内只允许开启一次）
 *   · 已过预售里满 7 天、可重新开预售的那条 → clearable=true（可以从台账清掉）
 *
 * 用户 2026-11-12 定的 SHEIN 口径：
 *   · 半托管**没有预售**，只有清零（可售<=1 天 → 线上 OVERWRITE 0）
 *   · 只列现在线上仍为 0 的记录；已补回去的（onlineNow>0）后端直接不输出
 *   · 店铺筛选固定 BS / BD / BAN / BE
 */
export type PresaleStatus = "预售中" | "已过预售" | "已清零";

export interface PresaleWatchItem {
  platform: string;
  store: string;
  code: string;
  /** SHEIN 才有：套装/变体（如 5SetE） */
  variant?: string;
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

  // —— 以下为 SHEIN 清零专用 ——
  /** 商家SKU（BD-202504-5SetE-XL） */
  seller?: string;
  /** 平台SKU */
  sku?: string;
  /** 操作仓库码 */
  wh?: string;
  /** 清零前线上可用 */
  before?: number | null;
  /** 仓内总库存（恒信+帕沃尔） */
  total?: number | null;
  /** 可售天数（仓内 ÷ 7天日均） */
  days?: number | null;
  /** 最近一次清零时间 */
  clearedAt?: string;
  /** 首次清零时间 */
  firstClearedAt?: string;
  /** 被清零过几次 */
  times?: number;
  /** 回读确认线上已为 0 */
  confirmed?: boolean;
  /** 核对时线上可用现值（>0 ⇒ 已回补） */
  onlineNow?: number | null;
  /** 上次核对时间 */
  checkedAt?: string;
}

export interface PresaleWatchData {
  generatedAt: string;
  presaleDays: number;
  rule: { note: string; openedFrom: string; clearedFrom?: string };
  counts: Record<string, number>;
  platforms: string[];
  stores: string[];
  /** SHEIN 固定的店铺筛选按钮 */
  sheinStores?: string[];
  items: PresaleWatchItem[];
}
