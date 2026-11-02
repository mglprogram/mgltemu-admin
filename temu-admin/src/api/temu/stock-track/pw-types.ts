/**
 * 帕沃尔海外仓 · 加州3仓 + Kansas 仓库存
 *
 * 数据来自 public/data/pw-stock.json
 * （scripts/goods/fetch_pw_stock.py 生成，每天 17:30 随库存跟踪一起跑）。
 *
 * 为什么单独一份：
 *   · Kansas —— 帕沃尔后台的库存页里，这个仓的库存一直是有效可用的；
 *   · 加州3  —— 2026-11-02 晚用户通知：领星 14450（帕沃尔）账号**已停用**，
 *              以后加州3 的库存改从帕沃尔库存链接更新，不再走领星。
 * 两个仓各自一列展示，不相加（货压在不同仓，混起来看不出该从哪发）。
 */

export interface PwStockItem {
  /** 帕沃尔 Fnsku */
  fnsku: string;
  /** 卖家 SKU（如 BS-207220-4SetA-XXL） */
  sku: string;
  /** 产品名称 */
  name: string;
  nameEn: string;
  /** 解析出的款号（通用解析，不限 7 个款；解析不出为空串） */
  code: string;
  /** 解析出的变体 */
  variant: string;
  /** 解析出的尺码 */
  size: string;
  /** 仓库：加州3仓 / kansas仓 */
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
  /** "加州3仓+kansas仓" */
  warehouse: string;
  /** 保留的仓库名 */
  warehouses?: string[];
  /** 逐仓汇总（行数 / 代发库存合计） */
  whs?: { wh: string; rows: number; stock: number }[];
  /** 保留的行数 */
  total: number;
  /** 全量扫过的行数（含被丢弃的其它仓） */
  scanned: number;
  pages: number;
  items: PwStockItem[];
  rule: { note: string; source: string };
}
