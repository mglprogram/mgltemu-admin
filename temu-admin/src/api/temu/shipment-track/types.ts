/**
 * 货件跟踪（追踪表 × 恒信轨迹 × 领星OMS入库明细）
 *
 * 数据来自 public/data/shipment-track.json，由 scripts/goods/fetch_shipment_track.py 生成：
 *   ① 货件追踪表（微信收到的最新一份 xlsx，每个 sheet 一个月）
 *   ② 恒信轨迹 http://hengxe.nextsls.com/tracking/app（按「其他单号」查，查不到退回运单号）
 *   ③ 领星 OMS OpenAPI v1 · inboundOrder/detail（产品名 / 尺码 / 数量）
 */

/** 恒信轨迹里的一条节点 */
export interface ShipmentTrace {
  time: string;
  info: string;
}

/** 恒信物流（一票一个 shipment） */
export interface ShipmentLogistics {
  /** 恒信是否查到了这票 */
  ok: boolean;
  /** 用什么单号匹配到的：otherNo（其他单号）/ trackingNo（运单号） */
  matchedBy: "otherNo" | "trackingNo" | "";
  /** 原始状态码：ready / in_transit / delivered … */
  status: string;
  /** 中文状态：已下单 / 转运中 / 已签收 … */
  statusText: string;
  shipmentId: string;
  country: string;
  postcode: string;
  /** 恒信那边的件数 */
  parcels: number | null;
  /** 最新节点（traces[0]） */
  latest: ShipmentTrace;
  /** 历史上第一个「延迟/异常/转船/查验」节点，没有就是空串 */
  latestAbnormal: string;
  /** **最新节点本身**就是异常/延迟 */
  currentAbnormal: boolean;
  /** 表内「现轨迹」落后于恒信最新节点 */
  lag: boolean;
  traceCount: number;
  /** 全部轨迹，时间倒序 */
  traces: ShipmentTrace[];
}

/** OMS 入库单里的一个 SKU */
export interface ShipmentSku {
  sku: string;
  /** 完整产品名，如 BS2-203802-4SetB-L */
  productName: string;
  /** 款号+套系（产品名去掉尺码），如 BS2-203802-4SetB */
  style: string;
  /** 尺码：S / M / L / XL / XXL … */
  size: string;
  qty: number;
}

/** 领星 OMS 入库单 */
export interface ShipmentOms {
  /** OMS 里有没有这张入库单 */
  found: boolean;
  /** 命中的 OMS 账号 tag（LM739 / 14450） */
  account?: string;
  status?: number;
  /** 草稿 / 待入库 / 收货中 / 已收货 / 已上架 / 已取消 / 待审核 / 驳回 */
  statusText: string;
  whName: string;
  whCode: string;
  expectedDate: string;
  orderCreateTime: string;
  receivedEndTime: string;
  shelfEndTime: string;
  canceledTime: string;
  remark: string;
  trackingNo: string;
  referOrderNo: string;
  skus: ShipmentSku[];
  skuCount: number;
  totalQty: number;
}

/** 一票货件 */
export interface ShipmentRow {
  /** 来自哪个 sheet（26年10月走货 …） */
  sheet: string;
  seq: string;
  /** 运单号（货代主单号，如 PT276410268654） */
  trackingNo: string;
  /** 其他单号（= 领星入库单号 IB…，也是查恒信的 key） */
  otherNo: string;
  channel: string;
  fbaWarehouse: string;
  /** 件数（箱数） */
  qty: number | string;
  /** 表里写的发货品名（粗糙，实际产品以 oms.skus 为准） */
  goods: string;
  weightKg: number | null;
  vessel: string;
  inboundDate: string;
  etdDate: string;
  /** 到港日期（空 = 还没到港） */
  arriveDate: string;
  signDate: string;
  /** 表内「现轨迹」原文 */
  excelTrace: string;

  /** 是否已到港（arriveDate 有值） */
  arrived: boolean;
  logistics: ShipmentLogistics;
  oms: ShipmentOms;
  /** 需要关注的原因列表（空 = 正常） */
  issues: string[];
}

export interface ShipmentSummary {
  total: number;
  arrived: number;
  notArrived: number;
  delivered: number;
  inTransit: number;
  /** 最新节点是异常/延迟的票数 */
  abnormal: number;
  /** 表内轨迹落后的票数 */
  lag: number;
  omsOnShelf: number;
  omsPending: number;
  omsCanceled: number;
  omsMissing: number;
  issues: number;
  /** 追踪表「数量(件)」合计 */
  totalQty: number;
  /** OMS 明细里的件数合计 */
  skuQty: number;
  byLogisticsStatus: Record<string, number>;
  byOmsStatus: Record<string, number>;
}

export interface ShipmentSource {
  workbook: string;
  workbookPath: string;
  workbookModified: string;
  sheets: string[];
  tracking: string;
  oms: string;
}

export interface ShipmentTrackData {
  generatedAt: string;
  source: ShipmentSource;
  summary: ShipmentSummary;
  sheets: string[];
  rows: ShipmentRow[];
}
