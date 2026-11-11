/**
 * 领星 OMS 全仓库存明细（两个账号：14450 帕沃尔 / LM739 恒信）
 *
 * 数据来自 public/data/linxing-stock.json
 * （scripts/goods/fetch_linxing_stock.py 生成，每天随库存跟踪一起跑）。
 *
 * 与 stock-track.json 里那份「总库存」的区别：
 *   · stock-track 只把总库存**挂到 7 个款号的尺码行上**；
 *   · 本文件是**全量明细**：账号 × 产品 × 尺码 × 仓库，一条不丢，
 *     供页面搜索、按账号/仓库筛选、逐仓展开。
 */

/** 一行 = 某账号 × 某仓 × 某 SKU 的库存 */
export interface LinxingItem {
  /** 领星账号：14450 / LM739 */
  acct: string;
  /** 账号中文名：帕沃尔 / 恒信 */
  acctLabel: string;
  /** 账号短标识：pw / hx */
  acctShort: string;
  /** 仓库名，如 加州3仓 / ZOO WAREHOUSE ⑥美西 */
  wh: string;
  whCode: string;
  /** 领星 SKU 编码 */
  sku: string;
  /** 领星产品名（原始，如 BS2-204203-4SetA-M） */
  productName: string;
  /** 解析出的款号（可能为空） */
  code: string;
  /** 解析出的变体（可能为空） */
  variant: string;
  /** 解析出的尺码（可能为空） */
  size: string;
  /** 总库存 = 可用 + 锁定 + 在途 + 箱 + FBA 退货 */
  total: number;
  /** 7天日均（十部库存表「总销售表→最近7天日均」，按款-套系-尺码各店铺相加）；缺数据为 null */
  daily?: number | null;
  /** 3天日均（同上，台账「最近3天日均」；页面默认隐藏） */
  daily3?: number | null;
  /** 14天日均（同上，台账「最近14天日均」；页面默认隐藏） */
  daily14?: number | null;
  /** 可用 */
  avail: number;
  /** 锁定（已下单未发） */
  lock: number;
  /** 在途 */
  transit: number;
  /** 箱库存 */
  box: number;
  /** FBA 退货 */
  fbaReturn: number;
  stockType: number | null;
  operateTime: string;
  /** 尺码排序键（XS→S→M→L→XL→XXL…，由前端算，用于默认排序；JSON 里没有） */
  sizeOrder?: number;
}

export interface LinxingAccount {
  tag: string;
  label: string;
  short: string;
  rows: number;
  ok: boolean;
  error: string;
}

export interface LinxingStockData {
  generatedAt: string;
  accounts: LinxingAccount[];
  warehouses: string[];
  /** 明细行数 */
  total: number;
  /** 全部总库存合计 */
  totalQty: number;
  /** 全部可用合计 */
  availQty: number;
  items: LinxingItem[];
  /** 7天日均命中行数 / 来源说明 / 用的哪个台账文件（2026-11-11 起来自十部库存表） */
  dailyHit?: number;
  dailySource?: string;
  dailyFile?: string;
  rule: { note: string; source: string };
}
