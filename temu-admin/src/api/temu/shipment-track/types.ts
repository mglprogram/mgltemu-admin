/**
 * 货件跟踪（以**领星入库单号**为主键的全程跟踪 + 到港→上架预测）
 *
 * 2026-11-02 改版（用户要求）：
 *   「其他单号改成入库单号，因为可以通过领星查，所以直接从领星每天抓取入库单号跟踪数据更全」
 *   「根据到港日期和签收日期帮我预测到港之后，平均几天能签收送仓上架」
 *
 * 数据来自 public/data/shipment-track.json，由 scripts/goods/fetch_shipment_track.py 生成：
 *   ① 领星 OMS 入库单（inboundOrder）—— **主表**，两个账号（LM739 美西 / 14450 加州3仓）
 *   ② 恒信轨迹 —— 只用来拿【到港】节点与最新节点（仅覆盖 LM739）
 *   ③ 历史货件追踪表 —— 只取「到港日期/签收日期」做标定样本
 */

/** 恒信轨迹里的一条节点 */
export interface ShipmentTrace {
  time: string;
  info: string;
}

export interface ShipmentLogistics {
  /** 恒信是否查到这票（帕沃尔那批恒信不覆盖，恒为 false，属正常） */
  ok: boolean;
  status: string;
  /** 已下单 / 已提柜 / 转运中 / 已签收 … */
  statusText: string;
  shipmentId: string;
  parcels: number | null;
  /** 最新节点（traces[0]） */
  latest: ShipmentTrace;
  /** 最新节点本身是延迟/异常/转船/查验 */
  currentAbnormal: boolean;
  traceCount: number;
  /** 全部轨迹，时间倒序 */
  traces: ShipmentTrace[];
}

/** 入库单里的一个 SKU */
export interface ShipmentSku {
  sku: string;
  /** 完整产品名，如 BS2-203802-4SetB-L */
  productName: string;
  /** 款号+套系（产品名去掉尺码） */
  style: string;
  /** 尺码：S / M / L / XL / XXL … */
  size: string;
  qty: number;
  /** OMS 已收货数量 */
  receivedQty: number;
}

/** 一张入库单（= 一票货） */
export interface ShipmentRow {
  /** 入库单号（主键），如 IB106260929RS / IB003261024RZ */
  inboundNo: string;
  /** 命中哪个 OMS 账号：LM739 / 14450 */
  account: string;

  whCode: string;
  whName: string;
  /** 渠道原值：以星 / 盐田 / 美森 / ""（空=未知） */
  channel: string;
  /** 渠道展示名：以星 ZIMKP / 盐田 TRK / 美森 MLLKP / 未知 */
  channelText: string;
  remark: string;

  /** OMS 状态码 0~7 */
  status?: number;
  /** 草稿 / 待入库 / 收货中 / 已收货 / 已上架 / 已取消 / 待审核 / 驳回 */
  statusText: string;
  createTime: string;
  /** OMS 预计到货日 */
  expectedDate: string;
  receivedTime: string;
  /** 上架时间（OMS shelfEndTime） */
  shelfTime: string;
  canceledTime: string;

  /** 到港日期（恒信【到港】节点优先，回落到历史表的到港日期） */
  arriveDate: string;
  /** 到港日期来源：hengxe / excel / "" */
  arriveSource: "hengxe" | "excel" | "";

  skus: ShipmentSku[];
  skuCount: number;
  totalQty: number;
  receivedQty: number;

  logistics: ShipmentLogistics;

  /**
   * 这一票的**货代查件页直链**（恒信 API 查不到时给用户点）。
   * LM739 → hengxe.nextsls.com/tracking/app?numbers=<入库单号>
   * 14450 → tracking.nextsls.com/trace?app=62a9924e…&numbers=<入库单号>
   */
  trackUrl: string;

  /** 该渠道「到港→上架」中位天数（预测用的基数） */
  leadDays: number;
  /** 预测上架日 */
  predictedShelf: string;
  /** 预测基准：到港日 / OMS预计到货 */
  predictedBasis: string;
  /** 到港至今等了几天 */
  waitDays: number | null;
  /** 超过预测上架日几天（>0 = 超期；0 = 没超） */
  overdueDays: number;

  /** 需要关注的原因（空 = 正常） */
  issues: string[];
}

export interface LeadStat {
  n: number;
  mean: number | null;
  median: number;
  p80: number | null;
  min: number | null;
  max: number | null;
}

export interface LeadTime {
  overall: LeadStat;
  /** 按渠道（以星 / 盐田 / 美森 / "" 未知） */
  channels: Record<string, LeadStat>;
  /** 未知渠道用的兜底中位数 */
  fallback: number;
  note: string;
}

export interface ShipmentSummary {
  total: number;
  /** 待入库 + 收货中 */
  waiting: number;
  onShelf: number;
  receiving: number;
  received: number;
  canceled: number;
  /** 有到港日期 */
  arrived: number;
  notArrived: number;
  /** 已到港但还没上架（最该催的） */
  arrivedWaiting: number;
  /** 已到港且超过预测上架日 */
  overdue: number;
  abnormal: number;
  /** 恒信查不到（帕沃尔那批恒为 0，已排除） */
  noTrace: number;
  issues: number;
  totalQty: number;
  waitingQty: number;
  byStatus: Record<string, number>;
  byChannel: Record<string, number>;
}

export interface ShipmentSource {
  primary: string;
  accounts: string[];
  tracking: string;
  calibration: string;
}

export interface ShipmentTrackData {
  generatedAt: string;
  source: ShipmentSource;
  leadTime: LeadTime;
  summary: ShipmentSummary;
  rows: ShipmentRow[];
}
