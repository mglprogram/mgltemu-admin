/**
 * 帕沃尔海外仓 · Kansas 仓库存（只有 Kansas 仓有效）
 *
 * 数据来自 public/data/pw-stock.json
 * （scripts/goods/fetch_pw_stock.py 生成，24 小时随库存跟踪一起跑）。
 *
 * 为什么单独一份：帕沃尔后台的库存页**只有 Kansas 仓的数据是有效的**
 * （用户 2026-11-02 明确说明），其它仓（加州3仓 / 新泽西2仓）不用。
 * 领星 OMS 那边帕沃尔 14450 账号是全仓汇总，这里看的是「Kansas 仓到底有多少货」。
 */

export interface PwStockItem {
  /** 帕沃尔 Fnsku */
  fnsku: string;
  /** 卖家 SKU（如 BS-207220-4SetA-XXL） */
  sku: string;
  /** 产品名称 */
  name: string;
  nameEn: string;
  /** 仓库，固定 kansas仓 */
  wh: string;
  /** 代发途中（在途） */
  transit: number;
  /** 代发库存（现货，可发） */
  stock: number;
  transferTransit: number;
  transferStock: number;
  /** 待处理库存 */
  pending: number;
  sales10: number;
  sales30: number;
  sales90: number;
  /** 库龄（天） */
  ageDays: number;
  /** 体积 ft³ */
  volume: number;
  /** 仓储费 $ */
  fee: number;
  returnStock: number;
  returnPending: number;
  /** 库存预警（如「安全库存」） */
  warn: string;
  /** 入仓时间 */
  inboundAt: string;
}

export interface PwStockData {
  generatedAt: string;
  warehouse: string;
  /** Kansas 行数 */
  total: number;
  /** 全量扫过的行数（含被丢弃的其它仓） */
  scanned: number;
  pages: number;
  items: PwStockItem[];
  rule: { note: string; source: string };
}
