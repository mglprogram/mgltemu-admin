/**
 * 产品手册类型定义（产品-库存 → 产品手册）
 *
 * 数据来自 public/data/product-manual.json
 * （scripts/goods/build_product_manual.py 从 D:\together\产品手册.xlsx 导出）。
 * 图片是从 xlsx 的 WPS 内嵌图（DISPIMG）抽出来转成的 webp，放在 public/manual-img/。
 */
export interface ManualProduct {
  /** 源表行号（排查用） */
  row: number;
  /** 款号 */
  style: string;
  /** 中文名称 */
  name: string;
  /** 报关编码 */
  hs: string;
  /** 做货工厂 */
  factory: string;
  /** 主图（实物/细节图），可能为空 */
  img: string | null;
  /** 尺码表列里的图（源表本身就是「尺码表 + 定做重量表」合成图） */
  sizeImgs: string[];
  /** 洗水唛成分列里的图 */
  compImgs: string[];
  /** 产品克重列里的图（重量表） */
  weightImgs: string[];
  /** 产品克重（纯文本时） */
  weight: string;
  /** 洗水唛成分（后台显示，纯文本时） */
  careLabel: string;
  /** 出厂价（仅在生成时加了 --with-price 才有） */
  price?: string;
  /** 变动（同上） */
  priceChange?: string;
}

export interface ProductManualData {
  generatedAt: string;
  source: string;
  sourceMtime: string;
  withPrice: boolean;
  /** 产品明细表头（已剔除价格列） */
  fields: string[];
  products: ManualProduct[];
  /** 款号命名规则表（原样网格） */
  rules: string[][];
}
